"""Pydantic schemas for versioned document browsing, search, and diff APIs."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from src.app.domain.models.diff import DiffStatus, NodeDiffResult


class VersionSummary(BaseModel):
    """Summary metadata of an ingested document revision."""
    id: str = Field(description="Persistent version ID")
    version_label: str = Field(description="Revision label (e.g., 'v1', 'v2')")
    file_hash: str = Field(description="Deterministic SHA-256 fingerprint of source PDF")
    filename: str = Field(description="Source file name")
    total_pages: int = Field(description="Total page count")
    extraction_method: str = Field(description="Pipeline extraction method")
    warning_count: int = Field(description="Number of extraction warnings recorded")
    created_at: datetime = Field(description="UTC ingestion timestamp")


class DocumentVersionsResponse(BaseModel):
    """List of all versions registered for a document."""
    document_slug: str
    document_title: str
    versions: List[VersionSummary]


class SectionSummary(BaseModel):
    """Summary of a top-level section in a document version."""
    id: str = Field(description="Persistent node-version ID")
    logical_node_id: str = Field(description="Cross-version logical node ID")
    section_number: Optional[str] = Field(default=None, description="Declared section number")
    heading: Optional[str] = Field(default=None, description="Heading title")
    declared_depth: int = Field(default=1, description="Declared hierarchy depth")
    source_order: int = Field(description="Source reading order index")
    page_start: int = Field(description="Starting page number")
    page_end: int = Field(description="Ending page number")
    content_hash: str = Field(description="Content hash")
    child_count: int = Field(default=0, description="Number of direct child nodes")


class BrowseSectionsResponse(BaseModel):
    """Response payload for browsing top-level sections of a document version."""
    document_slug: str
    document_title: str
    version_id: str = Field(description="ID of the browsed version")
    version_label: str = Field(description="Label of the browsed version")
    is_latest: bool = Field(description="True if browsed version is the latest ingested revision")
    total_sections: int
    sections: List[SectionSummary]


class ChildNodeSummary(BaseModel):
    """Summary of a child node attached to a parent node."""
    id: str = Field(description="Child node-version persistent ID")
    logical_node_id: str = Field(description="Cross-version logical node ID")
    node_type: str = Field(description="Node type (section, paragraph, table, list, etc.)")
    heading: Optional[str] = None
    section_number: Optional[str] = None
    source_order: int
    page_start: int
    page_end: int
    content_hash: str
    text_preview: str = Field(default="", description="Short snippet of text or table content")


class NodeDetailResponse(BaseModel):
    """Full detail of a specific persistent node-version."""
    id: str = Field(description="Persistent node-version ID")
    document_slug: str
    document_version_id: str
    logical_node_id: str
    parent_node_version_id: Optional[str] = None
    node_type: str
    heading: Optional[str] = None
    section_number: Optional[str] = None
    declared_depth: int
    source_order: int
    page_start: int
    page_end: int
    page_numbers: List[int] = Field(default_factory=list)
    bbox: Optional[List[float]] = None
    text: str = Field(default="", description="Full text body")
    structured_data: Optional[Dict[str, Any]] = Field(default=None, description="Structured table/list data")
    content_hash: str
    children: List[ChildNodeSummary] = Field(default_factory=list, description="Direct children nodes")


class SearchResultItem(BaseModel):
    """Individual node match result from a text or heading search."""
    node_version_id: str
    logical_node_id: str
    node_type: str
    heading: Optional[str] = None
    section_number: Optional[str] = None
    match_field: str = Field(description="Field where match was found ('heading', 'text', 'table')")
    snippet: str = Field(description="Highlighted excerpt showing query occurrence")
    page_start: int
    page_end: int
    content_hash: str


class SearchResponse(BaseModel):
    """Response payload for document version search queries."""
    document_slug: str
    version_id: str
    version_label: str
    query: str
    total_results: int
    results: List[SearchResultItem]


class DocumentDiffResponse(BaseModel):
    """Comprehensive semantic difference between two versions of a document."""
    document_slug: str
    v1_version_id: str
    v1_label: str
    v2_version_id: str
    v2_label: str
    summary_counts: Dict[str, int] = Field(
        description="Counts by DiffStatus: unchanged, changed, added, removed, unresolved"
    )
    node_diffs: List[NodeDiffResult]
