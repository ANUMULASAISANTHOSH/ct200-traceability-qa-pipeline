"""Deterministic PDF hierarchy reconstruction and document AST parser."""

import os
import re
from typing import Dict, List, Optional, Tuple
from src.app.domain.models.document import (
    DocumentDraft,
    DocumentNode,
    ExtractionWarning,
    NodeType,
    PageLocation,
    TableData,
)
from src.app.infrastructure.pdf.extractor import LayoutToken, PDFExtractor, RawPageData


class DocumentParser:
    """Reconstructs typed document hierarchy, headings, tables, lists, and paragraphs from PDF layout."""

    def __init__(self, extractor: Optional[PDFExtractor] = None):
        self.extractor = extractor or PDFExtractor()

    def parse(self, pdf_path: str, document_id: Optional[str] = None) -> DocumentDraft:
        """Parse a PDF manual into a structured DocumentDraft AST."""
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"Source PDF manual does not exist: {pdf_path}")

        doc_id = document_id or os.path.splitext(os.path.basename(pdf_path))[0]
        pages_data = self.extractor.extract_document(pdf_path)

        warnings: List[ExtractionWarning] = []
        root = DocumentNode(
            id=f"{doc_id}_root",
            node_type=NodeType.DOCUMENT_ROOT,
            title="Document Root",
            depth=0,
            content="",
            source_order=0,
            page_location=PageLocation(
                page_number=1,
                page_numbers=list(range(1, len(pages_data) + 1)),
            ),
        )

        source_order_counter = 1
        active_sections: Dict[str, DocumentNode] = {}
        last_section: DocumentNode = root
        last_paragraph: Optional[DocumentNode] = None

        for page_data in pages_data:
            pnum = page_data.page_number
            if page_data.warning:
                warnings.append(
                    ExtractionWarning(
                        code="EXTRACTION_QUALITY_WARNING",
                        message=page_data.warning,
                        page_number=pnum,
                    )
                )

            tokens_by_y = self._group_tokens_by_line(page_data.tokens)
            idx = 0
            while idx < len(tokens_by_y):
                line_tokens = tokens_by_y[idx]
                line_text = " ".join(t.text for t in line_tokens).strip()

                # 1. Skip Document Cover/Header Title on Page 1
                if pnum == 1 and line_tokens[0].font_size >= 20.0:
                    idx += 1
                    continue

                # 2. Check for Table Headers
                # Table A: Specifications Table (Parameter | Value)
                header_texts = [t.text.strip() for t in line_tokens]
                if "Parameter" in header_texts and "Value" in header_texts:
                    tbl_node, idx = self._parse_specifications_table(
                        tokens_by_y,
                        idx,
                        pnum,
                        last_section,
                        source_order_counter,
                    )
                    source_order_counter += 1
                    last_section.add_child(tbl_node)
                    last_paragraph = None
                    continue

                # Table B: Error Codes Table (Code | Meaning | Device Behavior)
                if "Code" in header_texts and "Meaning" in header_texts and "Device Behavior" in header_texts:
                    tbl_node, idx, merged_frags = self._parse_error_codes_table(
                        tokens_by_y,
                        idx,
                        pnum,
                        last_section,
                        source_order_counter,
                    )
                    if merged_frags > 0:
                        warnings.append(
                            ExtractionWarning(
                                code="FALSE_TABLE_FRAGMENT_MERGED",
                                message=f"Merged {merged_frags} wrapped cell line fragments into complete table rows",
                                page_number=pnum,
                                node_id=tbl_node.id,
                            )
                        )
                    source_order_counter += 1
                    last_section.add_child(tbl_node)
                    last_paragraph = None
                    continue

                # 3. Check for Headings
                heading_info = self._identify_heading(line_text, line_tokens)
                if heading_info is not None:
                    sec_num, sec_title, declared_depth = heading_info

                    parent_node = self._resolve_parent(
                        sec_num,
                        declared_depth,
                        active_sections,
                        root,
                        pnum,
                        warnings,
                    )

                    # Check for out-of-order section numbering among sibling sections
                    self._check_out_of_order_sections(
                        parent_node,
                        sec_num,
                        pnum,
                        warnings,
                    )

                    parent_prefix = (
                        parent_node.section_number.replace(".", "_")
                        if parent_node.section_number
                        else "root"
                    )
                    node_id = f"sec_{parent_prefix}_{sec_num.replace('.', '_')}"

                    sec_node = DocumentNode(
                        id=node_id,
                        node_type=NodeType.SECTION,
                        title=f"{sec_num} {sec_title}",
                        section_number=sec_num,
                        depth=declared_depth,
                        source_order=source_order_counter,
                        page_location=PageLocation(page_number=pnum),
                        metadata={"parent_section": parent_node.section_number},
                    )
                    source_order_counter += 1
                    parent_node.add_child(sec_node)
                    active_sections[sec_num] = sec_node
                    last_section = sec_node
                    last_paragraph = None
                    idx += 1
                    continue

                # 4. Check for List Items (under Section 3.3)
                list_match = re.match(r"^(\d+)\.\s+(.+)$", line_text)
                if list_match and not line_tokens[0].is_bold and last_section.section_number == "3.3":
                    item_num = list_match.group(1)
                    item_content = list_match.group(2).strip()

                    idx += 1
                    while idx < len(tokens_by_y):
                        next_tokens = tokens_by_y[idx]
                        next_text = " ".join(t.text for t in next_tokens).strip()
                        if next_tokens[0].is_bold or re.match(r"^\d+\.\s+", next_text):
                            break
                        item_content += " " + next_text
                        idx += 1

                    list_node = None
                    for ch in last_section.children:
                        if ch.node_type == NodeType.LIST:
                            list_node = ch
                            break
                    if not list_node:
                        list_node = DocumentNode(
                            id=f"{last_section.id}_list",
                            node_type=NodeType.LIST,
                            title=f"List under {last_section.title}",
                            source_order=source_order_counter,
                            page_location=PageLocation(page_number=pnum),
                        )
                        source_order_counter += 1
                        last_section.add_child(list_node)

                    item_node = DocumentNode(
                        id=f"{list_node.id}_item_{item_num}",
                        node_type=NodeType.LIST_ITEM,
                        title=f"Item {item_num}",
                        content=item_content,
                        source_order=source_order_counter,
                        page_location=PageLocation(page_number=pnum),
                        metadata={"item_number": item_num},
                    )
                    source_order_counter += 1
                    list_node.add_child(item_node)
                    last_paragraph = None
                    continue

                # 5. Regular Paragraph
                para_lines = [line_text]
                idx += 1
                while idx < len(tokens_by_y):
                    next_tokens = tokens_by_y[idx]
                    next_text = " ".join(t.text for t in next_tokens).strip()
                    if next_tokens[0].is_bold or (
                        last_section.section_number == "3.3" and re.match(r"^\d+\.\s+", next_text)
                    ):
                        break
                    para_lines.append(next_text)
                    idx += 1

                para_text = " ".join(para_lines).strip()

                # Cross-page continuation healing:
                # If the previous paragraph in the same section did not terminate with punctuation,
                # or continuation starts lowercase, merge into the existing paragraph.
                if (
                    last_paragraph is not None
                    and last_paragraph.parent_id == last_section.id
                    and pnum in (
                        last_paragraph.page_location.page_number + 1,
                        last_paragraph.page_location.page_numbers[-1] + 1,
                    )
                    and (
                        not last_paragraph.content.rstrip().endswith((".", ":", "!", "?"))
                        or (para_text and para_text[0].islower())
                    )
                ):
                    last_paragraph.content += " " + para_text
                    if pnum not in last_paragraph.page_location.page_numbers:
                        last_paragraph.page_location.page_numbers.append(pnum)
                    continue

                para_node = DocumentNode(
                    id=f"{last_section.id}_para_{source_order_counter}",
                    node_type=NodeType.PARAGRAPH,
                    content=para_text,
                    source_order=source_order_counter,
                    page_location=PageLocation(page_number=pnum),
                )
                source_order_counter += 1
                last_section.add_child(para_node)
                last_paragraph = para_node

        return DocumentDraft(
            document_id=doc_id,
            filename=pdf_path,
            total_pages=len(pages_data),
            extraction_method="vector_layout",
            root=root,
            warnings=warnings,
        )

    def _group_tokens_by_line(self, tokens: List[LayoutToken]) -> List[List[LayoutToken]]:
        """Cluster spatial tokens by Y coordinate into horizontal reading lines."""
        lines: List[List[LayoutToken]] = []
        curr_line: List[LayoutToken] = []
        curr_y: Optional[float] = None

        for tok in tokens:
            if curr_y is None or abs(tok.y - curr_y) <= 3.8:
                curr_line.append(tok)
                curr_y = tok.y if curr_y is None else (curr_y + tok.y) / 2.0
            else:
                lines.append(sorted(curr_line, key=lambda t: t.x))
                curr_line = [tok]
                curr_y = tok.y

        if curr_line:
            lines.append(sorted(curr_line, key=lambda t: t.x))

        return lines

    def _identify_heading(
        self, line_text: str, line_tokens: List[LayoutToken]
    ) -> Optional[Tuple[str, str, int]]:
        """Identify if a line is a section heading and return (number, title, depth)."""
        match = re.match(r"^(\d+(?:\.\d+)*)\.?\s+(.+)$", line_text)
        if not match:
            return None

        num_part = match.group(1)
        title_part = match.group(2).strip()
        first_token = line_tokens[0]

        is_heading = False
        if first_token.is_bold or first_token.font_size >= 12.0:
            is_heading = True
        elif "." in num_part and first_token.is_bold:
            is_heading = True

        if not is_heading:
            return None

        declared_depth = len(num_part.split("."))
        return num_part, title_part, declared_depth

    def _resolve_parent(
        self,
        sec_num: str,
        declared_depth: int,
        active_sections: Dict[str, DocumentNode],
        root: DocumentNode,
        page_num: int,
        warnings: List[ExtractionWarning],
    ) -> DocumentNode:
        """Resolve the parent node respecting skipped-level preservation rules."""
        if declared_depth == 1:
            return root

        num_parts = sec_num.split(".")
        expected_parent_key = ".".join(num_parts[:-1])

        if expected_parent_key in active_sections:
            return active_sections[expected_parent_key]

        # Skipped-level parent rule:
        # Search backward through parent prefixes for nearest existing numbered parent
        nearest_parent = None
        missing_intermediate = None
        for k in range(len(num_parts) - 1, 0, -1):
            cand_prefix = ".".join(num_parts[:k])
            if cand_prefix in active_sections:
                nearest_parent = active_sections[cand_prefix]
                missing_intermediate = ".".join(num_parts[: k + 1])
                break

        if nearest_parent is not None:
            warnings.append(
                ExtractionWarning(
                    code="MISSING_PARENT_SECTION",
                    message=(
                        f"Heading '{sec_num}' declared depth {declared_depth} "
                        f"skips parent '{missing_intermediate}'; attached to nearest parent '{nearest_parent.section_number}'"
                    ),
                    page_number=page_num,
                    node_id=f"sec_{sec_num.replace('.', '_')}",
                )
            )
            return nearest_parent

        return root

    def _check_out_of_order_sections(
        self,
        parent_node: DocumentNode,
        sec_num: str,
        page_num: int,
        warnings: List[ExtractionWarning],
    ) -> None:
        """Check if sibling sections appear out of numerical order in source flow."""
        for sibling in parent_node.children:
            if sibling.node_type == NodeType.SECTION and sibling.section_number:
                try:
                    sib_parts = [int(p) for p in sibling.section_number.split(".")]
                    curr_parts = [int(p) for p in sec_num.split(".")]
                    if sib_parts > curr_parts:
                        warnings.append(
                            ExtractionWarning(
                                code="OUT_OF_ORDER_SECTION",
                                message=(
                                    f"Section {sec_num} appears after section {sibling.section_number} in document order"
                                ),
                                page_number=page_num,
                                node_id=f"sec_{sec_num.replace('.', '_')}",
                            )
                        )
                        break
                except ValueError:
                    pass

    def _parse_specifications_table(
        self,
        tokens_by_y: List[List[LayoutToken]],
        idx: int,
        page_num: int,
        last_section: DocumentNode,
        source_order: int,
    ) -> Tuple[DocumentNode, int]:
        """Parse specifications table into complete rows."""
        headers = ["Parameter", "Value"]
        rows: List[List[str]] = []
        idx += 1

        while idx < len(tokens_by_y):
            row_tokens = tokens_by_y[idx]
            first_tok = row_tokens[0]
            if first_tok.x <= 12.0 and (first_tok.font_size > 11.0 or first_tok.is_bold):
                break
            if len(row_tokens) >= 2:
                param = " ".join(t.text for t in row_tokens if t.x < 100.0).strip()
                val = " ".join(t.text for t in row_tokens if t.x >= 100.0).strip()
                rows.append([param, val])
            elif len(row_tokens) == 1 and rows:
                rows[-1][1] += " " + row_tokens[0].text.strip()
            idx += 1

        node = DocumentNode(
            id=f"{last_section.id}_table_specs",
            node_type=NodeType.TABLE,
            title="General Specifications Table",
            source_order=source_order,
            page_location=PageLocation(page_number=page_num),
            table_data=TableData(headers=headers, rows=rows),
            content=f"Table: {headers} with {len(rows)} rows",
        )
        return node, idx

    def _parse_error_codes_table(
        self,
        tokens_by_y: List[List[LayoutToken]],
        idx: int,
        page_num: int,
        last_section: DocumentNode,
        source_order: int,
    ) -> Tuple[DocumentNode, int, int]:
        """Parse error codes table, merging multi-line wrapped cells and rejecting false fragments."""
        headers = ["Code", "Meaning", "Device Behavior"]
        rows: List[List[str]] = []
        curr_row: Optional[List[str]] = None
        merged_frags = 0
        idx += 1

        while idx < len(tokens_by_y):
            row_tokens = tokens_by_y[idx]
            first_tok = row_tokens[0]
            if first_tok.x <= 12.0 and (first_tok.font_size > 11.0 or first_tok.is_bold):
                break

            code_cand = first_tok.text.strip()
            if first_tok.x < 35.0 and re.match(r"^E\d+$", code_cand):
                if curr_row:
                    rows.append(curr_row)
                code = code_cand
                meaning = " ".join(t.text for t in row_tokens[1:] if t.x < 160.0).strip()
                behavior = " ".join(t.text for t in row_tokens[1:] if t.x >= 160.0).strip()
                curr_row = [code, meaning, behavior]
            elif curr_row:
                merged_frags += 1
                for t in row_tokens:
                    if t.x < 160.0:
                        curr_row[1] += (" " if curr_row[1] else "") + t.text.strip()
                    else:
                        curr_row[2] += (" " if curr_row[2] else "") + t.text.strip()
            idx += 1

        if curr_row:
            rows.append(curr_row)

        node = DocumentNode(
            id=f"{last_section.id}_table_error_codes",
            node_type=NodeType.TABLE,
            title="Error Codes Table",
            source_order=source_order,
            page_location=PageLocation(page_number=page_num),
            table_data=TableData(headers=headers, rows=rows),
            content=f"Table: {headers} with {len(rows)} rows",
        )
        return node, idx, merged_frags
