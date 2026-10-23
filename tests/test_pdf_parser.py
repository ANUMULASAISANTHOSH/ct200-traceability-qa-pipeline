"""Tests for PDF extraction, layout parsing, and document hierarchy reconstruction."""

import os
import pytest
from src.app.domain.models.document import DocumentDraft, NodeType
from src.app.infrastructure.pdf.extractor import PDFExtractor
from src.app.infrastructure.pdf.parser import DocumentParser

V1_PATH = "data/ct200_manual.pdf"
V2_PATH = "data/ct200_manual_v2.pdf"


@pytest.fixture(scope="session")
def parser() -> DocumentParser:
    return DocumentParser()


@pytest.fixture(scope="session")
def v1_doc(parser: DocumentParser) -> DocumentDraft:
    return parser.parse(V1_PATH, document_id="ct200_manual_v1")


@pytest.fixture(scope="session")
def v2_doc(parser: DocumentParser) -> DocumentDraft:
    return parser.parse(V2_PATH, document_id="ct200_manual_v2")


def test_extractor_selectable_text_verification():
    """Verify that all pages have valid selectable vector text without requiring OCR."""
    extractor = PDFExtractor()
    v1_pages = extractor.extract_document(V1_PATH)
    v2_pages = extractor.extract_document(V2_PATH)

    assert len(v1_pages) == 6
    assert len(v2_pages) == 7

    for p in v1_pages:
        assert p.is_ocr is False
        assert len(p.tokens) > 0

    for p in v2_pages:
        assert p.is_ocr is False
        assert len(p.tokens) > 0


def test_case_1_skipped_level_heading_2_1_1_1(v1_doc: DocumentDraft, v2_doc: DocumentDraft):
    """Case 1: 2.1.1.1 Battery Life retains declared depth (4), attaches to 2.1, and emits missing-parent warning."""
    for doc in [v1_doc, v2_doc]:
        nodes_2_1_1_1 = doc.find_nodes_by_section("2.1.1.1")
        assert len(nodes_2_1_1_1) == 1
        node = nodes_2_1_1_1[0]

        # 1. Preserves declared depth
        assert node.depth == 4
        assert "Battery Life Under Typical Use" in node.title

        # 2. Attaches directly to nearest parent 2.1
        parent = doc.find_node_by_id(node.parent_id)
        assert parent is not None
        assert parent.section_number == "2.1"
        assert "General Specifications" in parent.title

        # 3. Emits explicit missing-parent warning
        missing_warnings = [
            w for w in doc.warnings
            if w.code == "MISSING_PARENT_SECTION" and "2.1.1.1" in w.message
        ]
        assert len(missing_warnings) == 1
        assert "2.1.1" in missing_warnings[0].message
        assert "2.1" in missing_warnings[0].message

        # 4. Verifies no implicit/invented 2.1.1 node exists
        assert doc.find_nodes_by_section("2.1.1") == []


def test_case_2_section_3_4_before_3_3_source_order(v1_doc: DocumentDraft, v2_doc: DocumentDraft):
    """Case 2: 3.4 Auto Shutoff remains before 3.3 Result Display in source order."""
    for doc in [v1_doc, v2_doc]:
        node_3_4 = doc.find_nodes_by_section("3.4")[0]
        node_3_3 = doc.find_nodes_by_section("3.3")[0]

        # 1. 3.4 has lower source_order index than 3.3
        assert node_3_4.source_order < node_3_3.source_order

        # 2. Both attach to parent Section 3
        parent_3 = doc.find_node_by_id(node_3_4.parent_id)
        assert parent_3.section_number == "3"
        assert node_3_3.parent_id == parent_3.id

        # 3. In parent's child list, 3.4 precedes 3.3
        section_children = [c.section_number for c in parent_3.children if c.section_number]
        idx_3_4 = section_children.index("3.4")
        idx_3_3 = section_children.index("3.3")
        assert idx_3_4 < idx_3_3

        # 4. Emits out-of-order section warning
        order_warnings = [
            w for w in doc.warnings
            if w.code == "OUT_OF_ORDER_SECTION" and "3.3" in w.message and "3.4" in w.message
        ]
        assert len(order_warnings) == 1


def test_case_3_distinct_error_codes_headings(v1_doc: DocumentDraft, v2_doc: DocumentDraft):
    """Case 3: Two 'Error Codes' headings remain distinct nodes under different parents."""
    for doc in [v1_doc, v2_doc]:
        ec_sections = [
            n for n in doc.flatten()
            if n.node_type == NodeType.SECTION and n.title and "Error Codes" in n.title
        ]
        assert len(ec_sections) == 2

        sec_4_2 = [n for n in ec_sections if n.section_number == "4.2"][0]
        sec_7_1 = [n for n in ec_sections if n.section_number == "7.1"][0]

        # Distinct node IDs
        assert sec_4_2.id != sec_7_1.id
        assert sec_4_2.parent_id != sec_7_1.parent_id

        # Parent verification
        parent_4 = doc.find_node_by_id(sec_4_2.parent_id)
        parent_7 = doc.find_node_by_id(sec_7_1.parent_id)

        assert parent_4.section_number == "4"
        assert "Alarms and Safety Behavior" in parent_4.title

        assert parent_7.section_number == "7"
        assert "Troubleshooting" in parent_7.title


def test_case_4_complete_tables_and_wrapped_cell_merging(v1_doc: DocumentDraft, v2_doc: DocumentDraft):
    """Case 4: Reconstruct specifications and error-code tables; reject/merge false fragments; test E6 in v2."""
    # --- Specifications Table ---
    for doc in [v1_doc, v2_doc]:
        tables = doc.find_nodes_by_type(NodeType.TABLE)
        specs_table = [t for t in tables if "Parameter" in t.table_data.headers][0]
        assert specs_table.table_data.headers == ["Parameter", "Value"]
        assert len(specs_table.table_data.rows) == 7

        row_map = {r[0]: r[1] for r in specs_table.table_data.rows}
        assert row_map["Measurement method"] == "Oscillometric"
        assert row_map["Pressure range"] == "0–299 mmHg"
        assert row_map["Pulse range"] == "40–199 bpm"
        assert row_map["Accuracy (pressure)"] == "±3 mmHg"
        assert row_map["Accuracy (pulse)"] == "±5%"
        assert row_map["Power source"] == "4x AA batteries or 6V DC adapter"
        assert row_map["Display"] == "Backlit LCD"

    # --- Error Codes Table in V1 ---
    v1_tables = v1_doc.find_nodes_by_type(NodeType.TABLE)
    v1_err_table = [t for t in v1_tables if "Code" in t.table_data.headers][0]
    assert v1_err_table.table_data.headers == ["Code", "Meaning", "Device Behavior"]
    assert len(v1_err_table.table_data.rows) == 5

    v1_rows = {r[0]: (r[1], r[2]) for r in v1_err_table.table_data.rows}
    assert "E1" in v1_rows and "E2" in v1_rows and "E3" in v1_rows and "E4" in v1_rows and "E5" in v1_rows
    # Check wrapped cells merged into complete strings
    assert v1_rows["E1"][0] == "Cuff not connected or leak detected"
    assert v1_rows["E2"][0] == "Motion artifact detected during measurement"
    assert v1_rows["E2"][1] == "Aborts measurement, displays E2, prompts retry"
    assert v1_rows["E3"][0] == "Overpressure condition"
    assert "within 2 seconds" in v1_rows["E3"][1]
    assert v1_rows["E4"][0] == "Low battery during measurement"
    assert v1_rows["E5"][0] == "Internal sensor fault"
    assert "displays E5 until serviced" in v1_rows["E5"][1]

    # --- Error Codes Table in V2 ---
    v2_tables = v2_doc.find_nodes_by_type(NodeType.TABLE)
    v2_err_table = [t for t in v2_tables if "Code" in t.table_data.headers][0]
    assert len(v2_err_table.table_data.rows) == 6

    v2_rows = {r[0]: (r[1], r[2]) for r in v2_err_table.table_data.rows}
    # Check V2 timing change for E3
    assert "within 1.5 seconds" in v2_rows["E3"][1]
    # Check V2 row E6
    assert "E6" in v2_rows
    assert v2_rows["E6"][0] == "Bluetooth sync failure"
    assert "Displays E6 on next sync attempt; does not affect measurement" in v2_rows["E6"][1]

    # False fragment warnings emitted
    for doc in [v1_doc, v2_doc]:
        frag_warnings = [w for w in doc.warnings if w.code == "FALSE_TABLE_FRAGMENT_MERGED"]
        assert len(frag_warnings) >= 1


def test_case_5_page_spanning_content(v1_doc: DocumentDraft, v2_doc: DocumentDraft):
    """Case 5: Preserve page-spanning content (section 2 heading, v2 cleaning instructions, v2 classification)."""
    # 1. Section 2 heading at bottom of page 1 is preserved and parent of 2.1
    sec_2 = v1_doc.find_nodes_by_section("2")[0]
    assert sec_2.page_location.page_number == 1
    assert any(c.section_number == "2.1" for c in sec_2.children)

    # 2. V2 Section 6.1 Cleaning Instructions spans pages 5 and 6
    sec_6_1 = v2_doc.find_nodes_by_section("6.1")[0]
    cleaning_paras = [c for c in sec_6_1.children if c.node_type == NodeType.PARAGRAPH]
    assert len(cleaning_paras) >= 1
    cleaning_text = " ".join(p.content for p in cleaning_paras)
    assert "Wipe the device body and cuff exterior" in cleaning_text
    assert "use alcohol, solvents, or abrasive cleaners on the display." in cleaning_text

    cleaning_pages = cleaning_paras[0].page_location.page_numbers
    assert 5 in cleaning_pages and 6 in cleaning_pages

    # 3. V2 Section 8.1 Classification spans pages 6 and 7
    sec_8_1 = v2_doc.find_nodes_by_section("8.1")[0]
    class_paras = [c for c in sec_8_1.children if c.node_type == NodeType.PARAGRAPH]
    assert len(class_paras) >= 1
    class_text = " ".join(p.content for p in class_paras)
    assert "Class II medical device" in class_text
    assert "validated against relevant clinical accuracy standards for oscillometric devices." in class_text

    class_pages = class_paras[0].page_location.page_numbers
    assert 6 in class_pages and 7 in class_pages


def test_v2_new_section_5_3_data_export(v1_doc: DocumentDraft, v2_doc: DocumentDraft):
    """Verify that section 5.3 Data Export is present in v2 but absent in v1."""
    assert v1_doc.find_nodes_by_section("5.3") == []

    v2_sec_5_3 = v2_doc.find_nodes_by_section("5.3")
    assert len(v2_sec_5_3) == 1
    node = v2_sec_5_3[0]
    assert "Data Export" in node.title
    assert node.page_location.page_number == 5

    parent = v2_doc.find_node_by_id(node.parent_id)
    assert parent.section_number == "5"
    assert "Data Management" in parent.title

    para = [c for c in node.children if c.node_type == NodeType.PARAGRAPH][0]
    assert "Starting with firmware 1.4" in para.content
    assert "exporting stored readings as a CSV file" in para.content
