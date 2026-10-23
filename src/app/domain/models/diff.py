"""Domain models for cross-version semantic difference reports."""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class DiffStatus(str, Enum):
    """Status indicating how a logical node evolved between two document versions."""
    UNCHANGED = "unchanged"
    CHANGED = "changed"
    ADDED = "added"
    REMOVED = "removed"
    UNRESOLVED = "unresolved"


class NodeDiffResult(BaseModel):
    """Difference analysis for a single logical node across two document versions."""
    logical_node_id: str = Field(description="Persistent identifier of the logical node")
    node_type: str = Field(description="Node type (e.g., section, paragraph, table)")
    status: DiffStatus = Field(description="Diff classification status")
    heading: Optional[str] = Field(default=None, description="Heading title of the node")
    section_number: Optional[str] = Field(default=None, description="Declared section number")
    v1_content_hash: Optional[str] = Field(default=None, description="Content hash in baseline version")
    v2_content_hash: Optional[str] = Field(default=None, description="Content hash in target version")
    v1_node_version_id: Optional[str] = Field(default=None, description="Baseline NodeVersion ID")
    v2_node_version_id: Optional[str] = Field(default=None, description="Target NodeVersion ID")
    diff_summary: str = Field(default="", description="Human-readable text/unified diff summary of changes")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional contextual details")


class DocumentDiffReport(BaseModel):
    """Aggregate difference report comparing two versions of a document."""
    document_id: str = Field(description="Document identity")
    v1_version_id: str = Field(description="Baseline version ID")
    v1_label: str = Field(description="Baseline version label (e.g., 'v1')")
    v2_version_id: str = Field(description="Target version ID")
    v2_label: str = Field(description="Target version label (e.g., 'v2')")
    node_diffs: List[NodeDiffResult] = Field(default_factory=list, description="Per-node diff results")
    summary_counts: Dict[str, int] = Field(
        default_factory=dict,
        description="Count of nodes by diff status (unchanged, changed, added, removed, unresolved)",
    )
