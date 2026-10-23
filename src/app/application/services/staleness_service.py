"""Retrieval-time staleness analyzer comparing saved generation snapshots to the latest manual revision."""

from typing import Any, Dict, List, Optional, Set
from src.app.application.interfaces.document_repository import IDocumentRepository
from src.app.application.services.impact_analyzer import IMPACT_DISCLAIMER, classify_impact
from src.app.application.use_cases.diff_versions import generate_text_diff
from src.app.domain.models.qa import GenerationDocument, SourceSnapshot
from pydantic import BaseModel, Field


class SourceTraceabilityReport(BaseModel):
    """Detailed staleness comparison for a single source node in the generation context."""
    pinned_node_version_id: str = Field(description="Original pinned NodeVersion ID at generation time")
    logical_node_id: str = Field(description="Persistent logical node identity across versions")
    original_content_hash: str = Field(description="Content hash of the pinned node snapshot")
    latest_node_version_id: Optional[str] = Field(default=None, description="Matching NodeVersion ID in latest manual")
    latest_content_hash: Optional[str] = Field(default=None, description="Current content hash in latest manual")
    status: str = Field(description="Status: 'current', 'stale', 'removed', or 'unknown'")
    diff_summary: Optional[str] = Field(default=None, description="Unified text diff summary if content changed")
    impact_tags: List[str] = Field(default_factory=list, description="Automated review hint tags")
    heading: Optional[str] = Field(default=None, description="Section heading")
    section_number: Optional[str] = Field(default=None, description="Section number")


class GenerationTraceability(BaseModel):
    """Full retrieval-time staleness and impact assessment for a generation document."""
    aggregate_status: str = Field(description="Aggregate staleness status ('unknown' > 'removed' > 'stale' > 'current')")
    latest_document_version_id: Optional[str] = Field(default=None, description="Latest version ID compared against")
    latest_version_label: Optional[str] = Field(default=None, description="Latest version label (e.g. 'v2')")
    disclaimer: str = Field(default=IMPACT_DISCLAIMER, description="Regulatory advisory disclaimer")
    sources: List[SourceTraceabilityReport] = Field(default_factory=list, description="Per-source staleness reports")


def format_content_for_diff(text: str, structured_data: Optional[Dict[str, Any]] = None) -> str:
    """Format textual or structured table data into line format for text differencing."""
    if structured_data and "rows" in structured_data:
        rows = structured_data.get("rows", [])
        headers = structured_data.get("headers", [])
        lines = []
        if headers:
            lines.append(" | ".join(str(h) for h in headers))
            lines.append("-" * 40)
        for row in rows:
            lines.append(" | ".join(str(cell) for cell in row))
        return "\n".join(lines)
    return (text or "").strip()


class StalenessService:
    """Service dynamically evaluating generation staleness against the latest document version."""

    def __init__(self, repository: IDocumentRepository) -> None:
        self.repository = repository

    def analyze(self, doc: GenerationDocument) -> GenerationTraceability:
        """Compare generation source snapshots and hashes with the latest ingested version."""
        # 1. Resolve document family and baseline version
        selection = self.repository.get_selection_by_id(doc.selection_id)
        doc_id: Optional[str] = None
        pinned_version_id: Optional[str] = None
        pinned_version_label: str = "v1"

        if selection and selection.document_version:
            doc_id = selection.document_version.document_id
            pinned_version_id = selection.document_version_id
            pinned_version_label = selection.document_version.version_label
        elif doc.source_snapshots:
            first_nv = self.repository.get_node_version_by_id(doc.source_snapshots[0].node_version_id)
            if first_nv and first_nv.document_version:
                doc_id = first_nv.document_version.document_id
                pinned_version_id = first_nv.document_version_id
                pinned_version_label = first_nv.document_version.version_label

        if not doc_id:
            # Document identity cannot be resolved
            return GenerationTraceability(
                aggregate_status="unknown",
                latest_document_version_id=None,
                latest_version_label=None,
                sources=[
                    SourceTraceabilityReport(
                        pinned_node_version_id=s.node_version_id,
                        logical_node_id=s.logical_node_id,
                        original_content_hash=s.content_hash,
                        status="unknown",
                        diff_summary="Document lineage unavailable.",
                        heading=s.heading,
                        section_number=s.section_number,
                    )
                    for s in doc.source_snapshots
                ],
            )

        # 2. Fetch latest version of this document
        latest_ver = self.repository.get_latest_version(doc_id)
        if latest_ver is None:
            return GenerationTraceability(
                aggregate_status="unknown",
                latest_document_version_id=None,
                latest_version_label=None,
                sources=[
                    SourceTraceabilityReport(
                        pinned_node_version_id=s.node_version_id,
                        logical_node_id=s.logical_node_id,
                        original_content_hash=s.content_hash,
                        status="unknown",
                        diff_summary="Latest document version unavailable.",
                        heading=s.heading,
                        section_number=s.section_number,
                    )
                    for s in doc.source_snapshots
                ],
            )

        # 3. Compare snapshots against latest ingested document version (latest_ver)
        latest_matches = self.repository.get_version_matches(latest_ver.id)
        unresolved_target_nvs: Set[str] = {
            m.target_node_version_id for m in latest_matches if m.status == "unresolved"
        }
        unresolved_source_nvs: Set[str] = {
            m.source_node_version_id for m in latest_matches if m.status == "unresolved" and m.source_node_version_id
        }
        unresolved_log_ids: Set[str] = {
            m.logical_node_id for m in latest_matches if m.status == "unresolved" and m.logical_node_id
        }
        unresolved_candidates: Set[str] = set()
        for m in latest_matches:
            if m.status == "unresolved":
                for c in m.unresolved_candidates:
                    if isinstance(c, dict):
                        if c.get("logical_node_id"):
                            unresolved_candidates.add(str(c["logical_node_id"]))
                        if c.get("node_version_id"):
                            unresolved_candidates.add(str(c["node_version_id"]))
                    else:
                        unresolved_candidates.add(str(c))

        source_reports: List[SourceTraceabilityReport] = []

        for s in doc.source_snapshots:
            latest_nv = self.repository.get_node_version_by_logical_id(latest_ver.id, s.logical_node_id)
            is_unresolved = (
                (latest_nv and latest_nv.id in unresolved_target_nvs)
                or s.node_version_id in unresolved_source_nvs
                or s.logical_node_id in unresolved_log_ids
                or s.logical_node_id in unresolved_candidates
                or s.node_version_id in unresolved_candidates
            )

            if latest_nv is not None:
                if is_unresolved:
                    # Target node matching flagged as unresolved
                    source_status = "unknown"
                    diff_summary = f"Matching is unresolved or ambiguous in latest version {latest_ver.version_label}."
                    impact_tags = []
                elif s.content_hash == latest_nv.content_hash:
                    # Content unchanged
                    source_status = "current"
                    diff_summary = f"Content unchanged (hash: {latest_nv.content_hash[:8]}...)"
                    impact_tags = []
                else:
                    # Content changed -> Stale
                    source_status = "stale"
                    text1 = format_content_for_diff(s.text, s.structured_data)
                    text2 = format_content_for_diff(latest_nv.text, latest_nv.structured_data)
                    diff_summary = generate_text_diff(
                        text1,
                        text2,
                        label1=pinned_version_label,
                        label2=latest_ver.version_label,
                    )
                    impact_tags = classify_impact(text1, text2, diff_summary)

                source_reports.append(
                    SourceTraceabilityReport(
                        pinned_node_version_id=s.node_version_id,
                        logical_node_id=s.logical_node_id,
                        original_content_hash=s.content_hash,
                        latest_node_version_id=latest_nv.id,
                        latest_content_hash=latest_nv.content_hash,
                        status=source_status,
                        diff_summary=diff_summary,
                        impact_tags=impact_tags,
                        heading=latest_nv.heading or s.heading,
                        section_number=latest_nv.section_number or s.section_number,
                    )
                )

            else:
                # Node not found in latest version
                if s.logical_node_id in unresolved_candidates or s.node_version_id in unresolved_candidates:
                    source_status = "unknown"
                    diff_summary = f"Matching candidate unresolved in latest version {latest_ver.version_label}."
                    impact_tags = []
                else:
                    # Confidently absent in latest version
                    source_status = "removed"
                    diff_summary = (
                        f"Logical node '{s.logical_node_id}' removed in latest version {latest_ver.version_label} "
                        f"(previously present in {pinned_version_label})."
                    )
                    impact_tags = ["NODE_REMOVED_IN_LATEST_VERSION"]

                source_reports.append(
                    SourceTraceabilityReport(
                        pinned_node_version_id=s.node_version_id,
                        logical_node_id=s.logical_node_id,
                        original_content_hash=s.content_hash,
                        latest_node_version_id=None,
                        latest_content_hash=None,
                        status=source_status,
                        diff_summary=diff_summary,
                        impact_tags=impact_tags,
                        heading=s.heading,
                        section_number=s.section_number,
                    )
                )

        # 5. Compute aggregate status according to priority rule:
        # unknown > removed > stale > current
        statuses = {r.status for r in source_reports}
        if "unknown" in statuses:
            aggregate_status = "unknown"
        elif "removed" in statuses:
            aggregate_status = "removed"
        elif "stale" in statuses:
            aggregate_status = "stale"
        else:
            aggregate_status = "current"

        return GenerationTraceability(
            aggregate_status=aggregate_status,
            latest_document_version_id=latest_ver.id,
            latest_version_label=latest_ver.version_label,
            sources=source_reports,
        )
