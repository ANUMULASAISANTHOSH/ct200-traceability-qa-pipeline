"""Pydantic schemas for version-pinned selection requests and responses."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class CreateSelectionRequest(BaseModel):
    """Payload to create a version-pinned selection."""
    name: str = Field(..., min_length=1, max_length=256, description="Descriptive label for the selection")
    document_version_id: str = Field(..., min_length=1, description="Persistent ID of the document version to pin")
    node_version_ids: List[str] = Field(
        ..., min_length=1, description="Ordered list of persistent node-version IDs to include"
    )


class SelectedNodeItem(BaseModel):
    """Detailed node snapshot pinned within a selection."""
    node_version_id: str = Field(description="Exact pinned NodeVersion ID")
    logical_node_id: str = Field(description="Underlying cross-version logical node ID")
    position: int = Field(description="0-indexed position within the ordered selection")
    node_type: str = Field(description="Node type (section, paragraph, table, list, etc.)")
    heading: Optional[str] = None
    section_number: Optional[str] = None
    declared_depth: int
    source_order: int
    page_start: int
    page_end: int
    page_numbers: List[int] = Field(default_factory=list)
    bbox: Optional[List[float]] = None
    text: str = Field(default="", description="Exact snapshot body text")
    structured_data: Optional[Dict[str, Any]] = Field(default=None, description="Structured table/list data")
    content_hash: str = Field(description="Deterministic content hash of this node at selection time")


class SelectionResponse(BaseModel):
    """Response payload for a retrieved version-pinned selection."""
    id: str = Field(description="Persistent selection ID")
    name: str = Field(description="Selection label")
    document_version_id: str = Field(description="Pinned document version ID")
    document_slug: str = Field(description="Parent document slug")
    document_title: str = Field(description="Parent document title")
    version_label: str = Field(description="Pinned version label (e.g., 'v1')")
    created_at: datetime = Field(description="UTC timestamp of selection creation")
    total_nodes: int = Field(description="Count of nodes in selection")
    nodes: List[SelectedNodeItem] = Field(
        default_factory=list, description="Ordered list of pinned node snapshots"
    )
