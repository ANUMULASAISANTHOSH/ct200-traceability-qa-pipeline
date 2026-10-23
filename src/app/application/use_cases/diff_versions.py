"""Use case and service for cross-version semantic difference analysis."""

import difflib
from typing import Any, Dict, List, Optional
from src.app.application.interfaces.document_repository import IDocumentRepository
from src.app.domain.models.diff import DiffStatus, DocumentDiffReport, NodeDiffResult
from src.app.infrastructure.db.models import NodeVersionORM, VersionMatchORM


def format_node_text_for_diff(node: NodeVersionORM) -> str:
    """Format node text or structured tabular data into lines for diff comparison."""
    if node.structured_data and "rows" in node.structured_data:
        rows = node.structured_data.get("rows", [])
        headers = node.structured_data.get("headers", [])
        lines = []
        if headers:
            lines.append(" | ".join(headers))
            lines.append("-" * 40)
        for row in rows:
            lines.append(" | ".join(str(cell) for cell in row))
        return "\n".join(lines)
    return (node.text or "").strip()


def generate_text_diff(text1: str, text2: str, label1: str = "v1", label2: str = "v2") -> str:
    """Generate a clean unified text difference between two strings."""
    lines1 = [line + "\n" for line in text1.splitlines()]
    lines2 = [line + "\n" for line in text2.splitlines()]
    diff = list(difflib.unified_diff(lines1, lines2, fromfile=label1, tofile=label2, lineterm="\n"))
    if not diff:
        return "No text changes detected."
    # Trim file header lines for a cleaner summary
    trimmed = [line for line in diff if not line.startswith("---") and not line.startswith("+++")]
    return "".join(trimmed).strip()


class DiffVersionsUseCase:
    """Service comparing document versions for a specific logical node or across the entire document."""

    def __init__(self, repository: IDocumentRepository) -> None:
        self.repository = repository

    def diff_logical_node(
        self,
        v1_version_id: str,
        v2_version_id: str,
        logical_node_id: str,
    ) -> NodeDiffResult:
        """Compare two versions for one logical node and report status and text-diff summary."""
        v1_nv = self.repository.get_node_version_by_logical_id(v1_version_id, logical_node_id)
        v2_nv = self.repository.get_node_version_by_logical_id(v2_version_id, logical_node_id)

        if v1_nv is None and v2_nv is None:
            raise ValueError(
                f"Logical node '{logical_node_id}' not found in either version '{v1_version_id}' or '{v2_version_id}'"
            )

        # Check if v2 node has match record indicating unresolved status
        v2_matches = self.repository.get_version_matches(v2_version_id)
        match_for_node: Optional[VersionMatchORM] = None
        if v2_nv is not None:
            for vm in v2_matches:
                if vm.target_node_version_id == v2_nv.id:
                    match_for_node = vm
                    break

        eff_node = v2_nv if v2_nv is not None else v1_nv
        assert eff_node is not None
        eff_heading = eff_node.heading
        eff_sec = eff_node.section_number
        if eff_node.parent:
            if not eff_heading:
                eff_heading = eff_node.parent.heading
            if not eff_sec:
                eff_sec = eff_node.parent.section_number


        # Case 1: Added in v2
        if v1_nv is None and v2_nv is not None:
            if match_for_node and match_for_node.status == "unresolved":
                return NodeDiffResult(
                    logical_node_id=logical_node_id,
                    node_type=v2_nv.node_type,
                    status=DiffStatus.UNRESOLVED,
                    heading=eff_heading,
                    section_number=eff_sec,
                    v1_content_hash=None,
                    v2_content_hash=v2_nv.content_hash,
                    v1_node_version_id=None,
                    v2_node_version_id=v2_nv.id,
                    diff_summary=(
                        f"Unresolved candidate match requires review. "
                        f"Candidates: {match_for_node.unresolved_candidates}"
                    ),
                    metadata={"unresolved_candidates": match_for_node.unresolved_candidates},
                )

            return NodeDiffResult(
                logical_node_id=logical_node_id,
                node_type=v2_nv.node_type,
                status=DiffStatus.ADDED,
                heading=eff_heading,
                section_number=eff_sec,
                v1_content_hash=None,
                v2_content_hash=v2_nv.content_hash,
                v1_node_version_id=None,
                v2_node_version_id=v2_nv.id,
                diff_summary=f"Node added in target version: {eff_heading or eff_sec or v2_nv.node_type}",
            )

        # Case 2: Removed in v2
        if v1_nv is not None and v2_nv is None:
            return NodeDiffResult(
                logical_node_id=logical_node_id,
                node_type=v1_nv.node_type,
                status=DiffStatus.REMOVED,
                heading=eff_heading,
                section_number=eff_sec,
                v1_content_hash=v1_nv.content_hash,
                v2_content_hash=None,
                v1_node_version_id=v1_nv.id,
                v2_node_version_id=None,
                diff_summary=f"Node removed in target version (previously in baseline: '{eff_heading or eff_sec}')",
            )

        # Case 3: Present in both versions
        assert v1_nv is not None and v2_nv is not None

        if match_for_node and match_for_node.status == "unresolved":
            return NodeDiffResult(
                logical_node_id=logical_node_id,
                node_type=v2_nv.node_type,
                status=DiffStatus.UNRESOLVED,
                heading=eff_heading,
                section_number=eff_sec,
                v1_content_hash=v1_nv.content_hash,
                v2_content_hash=v2_nv.content_hash,
                v1_node_version_id=v1_nv.id,
                v2_node_version_id=v2_nv.id,
                diff_summary=f"Unresolved ambiguous match; candidates: {match_for_node.unresolved_candidates}",
                metadata={"unresolved_candidates": match_for_node.unresolved_candidates},
            )

        if v1_nv.content_hash == v2_nv.content_hash:
            return NodeDiffResult(
                logical_node_id=logical_node_id,
                node_type=v2_nv.node_type,
                status=DiffStatus.UNCHANGED,
                heading=eff_heading,
                section_number=eff_sec,
                v1_content_hash=v1_nv.content_hash,
                v2_content_hash=v2_nv.content_hash,
                v1_node_version_id=v1_nv.id,
                v2_node_version_id=v2_nv.id,
                diff_summary=f"Content unchanged (hash: {v1_nv.content_hash[:8]}...)",
            )


        # Content changed
        text1 = format_node_text_for_diff(v1_nv)
        text2 = format_node_text_for_diff(v2_nv)
        summary = generate_text_diff(text1, text2)

        return NodeDiffResult(
            logical_node_id=logical_node_id,
            node_type=v2_nv.node_type,
            status=DiffStatus.CHANGED,
            heading=eff_heading,
            section_number=eff_sec,
            v1_content_hash=v1_nv.content_hash,
            v2_content_hash=v2_nv.content_hash,
            v1_node_version_id=v1_nv.id,
            v2_node_version_id=v2_nv.id,
            diff_summary=summary,
        )


    def diff_versions(
        self,
        v1_version_id: str,
        v2_version_id: str,
    ) -> DocumentDiffReport:
        """Compare all logical nodes across two versions and return a comprehensive diff report."""
        v1_ver = self.repository.get_version_by_id(v1_version_id)
        if not v1_ver:
            raise ValueError(f"Version '{v1_version_id}' not found")
        v2_ver = self.repository.get_version_by_id(v2_version_id)
        if not v2_ver:
            raise ValueError(f"Version '{v2_version_id}' not found")

        v1_nvs = self.repository.get_node_versions_by_version_id(v1_version_id)
        v2_nvs = self.repository.get_node_versions_by_version_id(v2_version_id)

        # Collect all unique logical node IDs across both versions in order
        all_logical_ids: List[str] = []
        seen = set()
        # Order by v2 source order first, then v1 remaining
        for nv in v2_nvs:
            if nv.logical_node_id not in seen:
                seen.add(nv.logical_node_id)
                all_logical_ids.append(nv.logical_node_id)
        for nv in v1_nvs:
            if nv.logical_node_id not in seen:
                seen.add(nv.logical_node_id)
                all_logical_ids.append(nv.logical_node_id)

        node_diffs: List[NodeDiffResult] = []
        counts = {
            DiffStatus.UNCHANGED.value: 0,
            DiffStatus.CHANGED.value: 0,
            DiffStatus.ADDED.value: 0,
            DiffStatus.REMOVED.value: 0,
            DiffStatus.UNRESOLVED.value: 0,
        }

        for log_id in all_logical_ids:
            diff_result = self.diff_logical_node(v1_version_id, v2_version_id, log_id)
            node_diffs.append(diff_result)
            counts[diff_result.status.value] += 1

        return DocumentDiffReport(
            document_id=v1_ver.document_id,
            v1_version_id=v1_version_id,
            v1_label=v1_ver.version_label,
            v2_version_id=v2_version_id,
            v2_label=v2_ver.version_label,
            node_diffs=node_diffs,
            summary_counts=counts,
        )
