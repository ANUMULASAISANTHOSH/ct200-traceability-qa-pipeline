"""Cross-version node lineage matcher adhering to strict hierarchical matching rules."""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple
from src.app.domain.models.document import DocumentDraft, DocumentNode, NodeType
from src.app.domain.models.hashing import compute_node_content_hash
from src.app.infrastructure.db.models import NodeVersionORM, generate_id


def normalize_heading(text: Optional[str]) -> str:
    """Normalize heading text for invariant cross-version comparison."""
    if not text:
        return ""
    # Strip declared numbering if accidentally part of the title
    cleaned = re.sub(r"^\d+(\.\d+)*\s*", "", text.strip())
    # Lowercase and strip punctuation
    cleaned = re.sub(r"[^\w\s]", " ", cleaned.lower())
    return " ".join(cleaned.split())


@dataclass
class MatchedNodePlan:
    """Plan for persisting an ingested node with assigned logical identity and match record."""
    draft_node: DocumentNode
    logical_node_id: str
    is_new_logical_node: bool
    source_node_version_id: Optional[str]
    match_method: str
    confidence: float
    status: str  # "matched", "added", "unresolved"
    unresolved_candidates: List[Dict[str, Any]] = field(default_factory=list)
    notes: Optional[str] = None
    content_hash: str = ""
    canonical_heading: Optional[str] = None
    canonical_path: Optional[str] = None


class CrossVersionNodeMatcher:
    """Hierarchical node matcher linking incoming parsed document nodes to prior version lineage."""

    def match_draft_against_prior_version(
        self,
        prior_node_versions: List[NodeVersionORM],
        draft: DocumentDraft,
        document_id: str,
    ) -> List[MatchedNodePlan]:
        """Align nodes in draft against prior version nodes."""
        # Pre-compute content hashes for all draft nodes
        draft_hashes: Dict[str, str] = {}
        for node in draft.flatten():
            structured_dict = None
            if node.table_data:
                structured_dict = {
                    "headers": node.table_data.headers,
                    "rows": node.table_data.rows,
                }
            elif "list_items" in node.metadata:
                structured_dict = {"items": node.metadata["list_items"]}

            draft_hashes[node.id] = compute_node_content_hash(
                node_type=node.node_type.value,
                heading=node.title,
                section_number=node.section_number,
                text=node.content,
                structured_data=structured_dict,
            )

        # If no prior version exists, this is an initial baseline ingestion
        if not prior_node_versions:
            return self._build_initial_version_plans(draft, draft_hashes)

        # Build index and tree structures for prior version
        v1_by_id = {nv.id: nv for nv in prior_node_versions}
        v1_children_by_parent: Dict[Optional[str], List[NodeVersionORM]] = {}
        for nv in prior_node_versions:
            pid = nv.parent_node_version_id
            v1_children_by_parent.setdefault(pid, []).append(nv)
        for children in v1_children_by_parent.values():
            children.sort(key=lambda n: n.source_order)

        # Root of prior version
        root_v1_candidates = v1_children_by_parent.get(None, [])
        v1_root = root_v1_candidates[0] if root_v1_candidates else None

        # Build map of v1 sections by normalized heading under each parent
        # Map: (parent_logical_id, norm_heading) -> list of NodeVersionORM
        v1_sections_by_parent_heading: Dict[Tuple[Optional[str], str], List[NodeVersionORM]] = {}
        # Also build a global map of sections for detecting moved/renamed candidates
        v1_all_sections_by_heading: Dict[str, List[NodeVersionORM]] = {}
        for nv in prior_node_versions:
            if nv.node_type == NodeType.SECTION.value and nv.heading:
                parent_nv = v1_by_id.get(nv.parent_node_version_id) if nv.parent_node_version_id else None
                parent_log_id = parent_nv.logical_node_id if parent_nv else None
                nh = normalize_heading(nv.heading)
                v1_sections_by_parent_heading.setdefault((parent_log_id, nh), []).append(nv)
                v1_all_sections_by_heading.setdefault(nh, []).append(nv)

        matched_plans: List[MatchedNodePlan] = []
        # draft_node_id -> assigned logical_node_id
        assigned_logical_ids: Dict[str, str] = {}
        # draft_node_id -> assigned NodeVersionORM ID from v1 (if matched)
        assigned_v1_node_ids: Dict[str, str] = {}

        # Traversal queues: match top-down
        # First match root
        draft_root = draft.root
        if v1_root:
            assigned_logical_ids[draft_root.id] = v1_root.logical_node_id
            assigned_v1_node_ids[draft_root.id] = v1_root.id
            matched_plans.append(
                MatchedNodePlan(
                    draft_node=draft_root,
                    logical_node_id=v1_root.logical_node_id,
                    is_new_logical_node=False,
                    source_node_version_id=v1_root.id,
                    match_method="EXACT_ROOT",
                    confidence=1.0,
                    status="matched",
                    content_hash=draft_hashes[draft_root.id],
                    canonical_heading="root",
                    canonical_path="/",
                )
            )
        else:
            new_log_id = generate_id("log")
            assigned_logical_ids[draft_root.id] = new_log_id
            matched_plans.append(
                MatchedNodePlan(
                    draft_node=draft_root,
                    logical_node_id=new_log_id,
                    is_new_logical_node=True,
                    source_node_version_id=None,
                    match_method="NEW_ROOT",
                    confidence=1.0,
                    status="matched",
                    content_hash=draft_hashes[draft_root.id],
                    canonical_heading="root",
                    canonical_path="/",
                )
            )

        # Track used v1 node IDs to avoid duplicate assignment within the same parent
        used_v1_node_ids: Set[str] = set()
        if v1_root:
            used_v1_node_ids.add(v1_root.id)

        # Breadth-first or top-down recursive matching of children
        def _match_children_of(draft_parent: DocumentNode) -> None:
            parent_log_id = assigned_logical_ids.get(draft_parent.id)
            parent_v1_id = assigned_v1_node_ids.get(draft_parent.id)

            # Available v1 children under parent_v1_id
            available_v1_children = (
                [c for c in v1_children_by_parent.get(parent_v1_id, []) if c.id not in used_v1_node_ids]
                if parent_v1_id
                else []
            )

            # Separate draft children into sections and non-sections
            draft_sections = [c for c in draft_parent.children if c.node_type == NodeType.SECTION]
            draft_non_sections = [c for c in draft_parent.children if c.node_type != NodeType.SECTION]

            # 1. Match Section Children
            for draft_sec in draft_sections:
                norm_h = normalize_heading(draft_sec.title)
                canonical_path = f"{draft_parent.id}/{draft_sec.section_number or norm_h}"

                # Look for matching section under same parent
                v1_candidates_under_parent = [
                    c for c in available_v1_children
                    if c.node_type == NodeType.SECTION.value and normalize_heading(c.heading) == norm_h
                ]

                if len(v1_candidates_under_parent) == 1:
                    matched_v1 = v1_candidates_under_parent[0]
                    used_v1_node_ids.add(matched_v1.id)
                    assigned_logical_ids[draft_sec.id] = matched_v1.logical_node_id
                    assigned_v1_node_ids[draft_sec.id] = matched_v1.id

                    sec_match = (
                        (draft_sec.section_number or "").strip() == (matched_v1.section_number or "").strip()
                    )
                    method = "EXACT_PARENT_AND_HEADING" if sec_match else "MATCHED_PARENT_HEADING_RENUMBERED"
                    conf = 1.0 if sec_match else 0.95

                    matched_plans.append(
                        MatchedNodePlan(
                            draft_node=draft_sec,
                            logical_node_id=matched_v1.logical_node_id,
                            is_new_logical_node=False,
                            source_node_version_id=matched_v1.id,
                            match_method=method,
                            confidence=conf,
                            status="matched",
                            content_hash=draft_hashes[draft_sec.id],
                            canonical_heading=draft_sec.title,
                            canonical_path=canonical_path,
                        )
                    )
                elif len(v1_candidates_under_parent) > 1:
                    # Sibling occurrence matching under same parent
                    # Find which occurrence index this draft_sec is among identical headings
                    identical_draft_siblings = [
                        s for s in draft_sections if normalize_heading(s.title) == norm_h
                    ]
                    occ_idx = identical_draft_siblings.index(draft_sec)
                    matched_v1 = (
                        v1_candidates_under_parent[occ_idx]
                        if occ_idx < len(v1_candidates_under_parent)
                        else v1_candidates_under_parent[0]
                    )
                    used_v1_node_ids.add(matched_v1.id)
                    assigned_logical_ids[draft_sec.id] = matched_v1.logical_node_id
                    assigned_v1_node_ids[draft_sec.id] = matched_v1.id

                    matched_plans.append(
                        MatchedNodePlan(
                            draft_node=draft_sec,
                            logical_node_id=matched_v1.logical_node_id,
                            is_new_logical_node=False,
                            source_node_version_id=matched_v1.id,
                            match_method="MATCHED_PARENT_HEADING_OCCURRENCE",
                            confidence=0.90,
                            status="matched",
                            content_hash=draft_hashes[draft_sec.id],
                            canonical_heading=draft_sec.title,
                            canonical_path=canonical_path,
                        )
                    )
                else:
                    # No match under same parent. Check if this heading exists under a DIFFERENT parent in v1.
                    # Rule: "Keep repeated headings under different parents distinct.
                    # Leave uncertain renames or moves unresolved for review; do not silently link them."
                    other_parent_candidates = [
                        c for c in v1_all_sections_by_heading.get(norm_h, [])
                        if c.id not in used_v1_node_ids
                    ]

                    # Check for section number match with different heading (potential rename/move)
                    sec_num = (draft_sec.section_number or "").strip()
                    renamed_candidates = [
                        c for c in prior_node_versions
                        if c.node_type == NodeType.SECTION.value
                        and (c.section_number or "").strip() == sec_num
                        and c.id not in used_v1_node_ids
                    ] if sec_num else []

                    if other_parent_candidates or renamed_candidates:
                        # Uncertain move or rename! Flag as unresolved.
                        candidates_meta = []
                        for c in other_parent_candidates:
                            candidates_meta.append({
                                "candidate_node_version_id": c.id,
                                "heading": c.heading,
                                "section_number": c.section_number,
                                "reason": "Heading exists under a different parent in prior version",
                            })
                        for c in renamed_candidates:
                            candidates_meta.append({
                                "candidate_node_version_id": c.id,
                                "heading": c.heading,
                                "section_number": c.section_number,
                                "reason": "Section number matches but heading changed/moved",
                            })

                        new_log_id = generate_id("log")
                        assigned_logical_ids[draft_sec.id] = new_log_id
                        matched_plans.append(
                            MatchedNodePlan(
                                draft_node=draft_sec,
                                logical_node_id=new_log_id,
                                is_new_logical_node=True,
                                source_node_version_id=None,
                                match_method="UNRESOLVED",
                                confidence=0.0,
                                status="unresolved",
                                unresolved_candidates=candidates_meta,
                                notes="Uncertain rename or move detected; requires human review",
                                content_hash=draft_hashes[draft_sec.id],
                                canonical_heading=draft_sec.title,
                                canonical_path=canonical_path,
                            )
                        )
                    else:
                        # Purely new section node (e.g., Section 5.3 Data Export)
                        new_log_id = generate_id("log")
                        assigned_logical_ids[draft_sec.id] = new_log_id
                        matched_plans.append(
                            MatchedNodePlan(
                                draft_node=draft_sec,
                                logical_node_id=new_log_id,
                                is_new_logical_node=True,
                                source_node_version_id=None,
                                match_method="NEW_NODE",
                                confidence=1.0,
                                status="added",
                                content_hash=draft_hashes[draft_sec.id],
                                canonical_heading=draft_sec.title,
                                canonical_path=canonical_path,
                            )
                        )

                # Recursively match children of this section
                _match_children_of(draft_sec)

            # 2. Match Non-Section Content Children (Paragraphs, Tables, Lists) under this parent
            v1_non_sections = [
                c for c in available_v1_children if c.node_type != NodeType.SECTION.value
            ]
            # Match tables by occurrence index
            draft_tables = [c for c in draft_non_sections if c.node_type == NodeType.TABLE]
            v1_tables = [c for c in v1_non_sections if c.node_type == NodeType.TABLE.value]
            for idx, d_tbl in enumerate(draft_tables):
                canonical_path = f"{draft_parent.id}/table_{idx}"
                if idx < len(v1_tables):
                    matched_tbl = v1_tables[idx]
                    used_v1_node_ids.add(matched_tbl.id)
                    assigned_logical_ids[d_tbl.id] = matched_tbl.logical_node_id
                    assigned_v1_node_ids[d_tbl.id] = matched_tbl.id
                    matched_plans.append(
                        MatchedNodePlan(
                            draft_node=d_tbl,
                            logical_node_id=matched_tbl.logical_node_id,
                            is_new_logical_node=False,
                            source_node_version_id=matched_tbl.id,
                            match_method="MATCHED_PARENT_TABLE_POSITION",
                            confidence=1.0,
                            status="matched",
                            content_hash=draft_hashes[d_tbl.id],
                            canonical_heading=f"Table {idx+1}",
                            canonical_path=canonical_path,
                        )
                    )
                else:
                    new_log_id = generate_id("log")
                    assigned_logical_ids[d_tbl.id] = new_log_id
                    matched_plans.append(
                        MatchedNodePlan(
                            draft_node=d_tbl,
                            logical_node_id=new_log_id,
                            is_new_logical_node=True,
                            source_node_version_id=None,
                            match_method="NEW_NODE",
                            confidence=1.0,
                            status="added",
                            content_hash=draft_hashes[d_tbl.id],
                            canonical_heading=f"Table {idx+1}",
                            canonical_path=canonical_path,
                        )
                    )

            # Match paragraphs by occurrence index under parent
            draft_paras = [c for c in draft_non_sections if c.node_type == NodeType.PARAGRAPH]
            v1_paras = [c for c in v1_non_sections if c.node_type == NodeType.PARAGRAPH.value]
            for idx, d_para in enumerate(draft_paras):
                canonical_path = f"{draft_parent.id}/para_{idx}"
                if idx < len(v1_paras):
                    matched_para = v1_paras[idx]
                    used_v1_node_ids.add(matched_para.id)
                    assigned_logical_ids[d_para.id] = matched_para.logical_node_id
                    assigned_v1_node_ids[d_para.id] = matched_para.id
                    matched_plans.append(
                        MatchedNodePlan(
                            draft_node=d_para,
                            logical_node_id=matched_para.logical_node_id,
                            is_new_logical_node=False,
                            source_node_version_id=matched_para.id,
                            match_method="MATCHED_PARENT_PARAGRAPH_POSITION",
                            confidence=1.0,
                            status="matched",
                            content_hash=draft_hashes[d_para.id],
                            canonical_heading=None,
                            canonical_path=canonical_path,
                        )
                    )
                else:
                    new_log_id = generate_id("log")
                    assigned_logical_ids[d_para.id] = new_log_id
                    matched_plans.append(
                        MatchedNodePlan(
                            draft_node=d_para,
                            logical_node_id=new_log_id,
                            is_new_logical_node=True,
                            source_node_version_id=None,
                            match_method="NEW_NODE",
                            confidence=1.0,
                            status="added",
                            content_hash=draft_hashes[d_para.id],
                            canonical_heading=None,
                            canonical_path=canonical_path,
                        )
                    )

            # Match lists / other non-sections by occurrence index
            draft_others = [
                c for c in draft_non_sections
                if c.node_type not in (NodeType.TABLE, NodeType.PARAGRAPH)
            ]
            v1_others = [
                c for c in v1_non_sections
                if c.node_type not in (NodeType.TABLE.value, NodeType.PARAGRAPH.value)
            ]
            for idx, d_other in enumerate(draft_others):
                canonical_path = f"{draft_parent.id}/other_{idx}"
                if idx < len(v1_others):
                    matched_other = v1_others[idx]
                    used_v1_node_ids.add(matched_other.id)
                    assigned_logical_ids[d_other.id] = matched_other.logical_node_id
                    assigned_v1_node_ids[d_other.id] = matched_other.id
                    matched_plans.append(
                        MatchedNodePlan(
                            draft_node=d_other,
                            logical_node_id=matched_other.logical_node_id,
                            is_new_logical_node=False,
                            source_node_version_id=matched_other.id,
                            match_method="MATCHED_PARENT_OTHER_POSITION",
                            confidence=1.0,
                            status="matched",
                            content_hash=draft_hashes[d_other.id],
                            canonical_heading=None,
                            canonical_path=canonical_path,
                        )
                    )
                else:
                    new_log_id = generate_id("log")
                    assigned_logical_ids[d_other.id] = new_log_id
                    matched_plans.append(
                        MatchedNodePlan(
                            draft_node=d_other,
                            logical_node_id=new_log_id,
                            is_new_logical_node=True,
                            source_node_version_id=None,
                            match_method="NEW_NODE",
                            confidence=1.0,
                            status="added",
                            content_hash=draft_hashes[d_other.id],
                            canonical_heading=None,
                            canonical_path=canonical_path,
                        )
                    )

                if d_other.children:
                    _match_children_of(d_other)


        # Execute recursive traversal from root
        _match_children_of(draft.root)
        return matched_plans

    def _build_initial_version_plans(
        self, draft: DocumentDraft, draft_hashes: Dict[str, str]
    ) -> List[MatchedNodePlan]:
        """Build initial plans when ingesting the very first version of a document."""
        plans: List[MatchedNodePlan] = []
        for node in draft.flatten():
            log_id = generate_id("log")
            norm_h = normalize_heading(node.title)
            plans.append(
                MatchedNodePlan(
                    draft_node=node,
                    logical_node_id=log_id,
                    is_new_logical_node=True,
                    source_node_version_id=None,
                    match_method="INITIAL_VERSION",
                    confidence=1.0,
                    status="matched",
                    content_hash=draft_hashes[node.id],
                    canonical_heading=node.title,
                    canonical_path=f"/{node.section_number or norm_h}" if node.section_number or norm_h else "/",
                )
            )
        return plans
