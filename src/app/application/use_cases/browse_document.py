"""Use case for browsing document versions, top-level sections, and node snapshots."""

from typing import List, Optional
from src.app.api.schemas.document import (
    BrowseSectionsResponse,
    ChildNodeSummary,
    DocumentVersionsResponse,
    NodeDetailResponse,
    SectionSummary,
    VersionSummary,
)
from src.app.application.interfaces.document_repository import IDocumentRepository
from src.app.domain.exceptions import (
    CrossDocumentMismatchError,
    DocumentNotFoundError,
    NodeNotFoundError,
    VersionNotFoundError,
)
from src.app.domain.models.document import NodeType


class BrowseDocumentUseCase:
    """Use case for querying and browsing versioned document trees."""

    def __init__(self, repository: IDocumentRepository) -> None:
        self.repository = repository

    def list_versions(self, document_slug: str) -> DocumentVersionsResponse:
        """List all ingested versions for a document slug."""
        doc = self.repository.get_document_by_slug(document_slug)
        if doc is None:
            raise DocumentNotFoundError(f"Document '{document_slug}' not found")

        summaries = [
            VersionSummary(
                id=v.id,
                version_label=v.version_label,
                file_hash=v.file_hash,
                filename=v.filename,
                total_pages=v.total_pages,
                extraction_method=v.extraction_method,
                warning_count=len(v.warnings or []),
                created_at=v.created_at,
            )
            for v in doc.versions
        ]
        return DocumentVersionsResponse(
            document_slug=doc.slug,
            document_title=doc.title,
            versions=summaries,
        )

    def browse_sections(
        self, document_slug: str, version_id: Optional[str] = None
    ) -> BrowseSectionsResponse:
        """Browse top-level sections for a document, defaulting to the latest version."""
        doc = self.repository.get_document_by_slug(document_slug)
        if doc is None:
            raise DocumentNotFoundError(f"Document '{document_slug}' not found")

        latest_ver = self.repository.get_latest_version(doc.id)
        if latest_ver is None:
            raise VersionNotFoundError(f"No versions found for document '{document_slug}'")

        if version_id is not None:
            ver = self.repository.get_version_by_id(version_id)
            if ver is None or ver.document_id != doc.id:
                raise VersionNotFoundError(
                    f"Version '{version_id}' not found for document '{document_slug}'"
                )
            is_latest = (ver.id == latest_ver.id)
        else:
            ver = latest_ver
            is_latest = True

        all_nodes = self.repository.get_node_versions_by_version_id(ver.id)

        # Identify top-level sections (sections directly under document root or declared depth 1)
        root_nodes = [n for n in all_nodes if n.node_type == NodeType.DOCUMENT_ROOT.value]
        root_id = root_nodes[0].id if root_nodes else None

        top_sections = [
            n for n in all_nodes
            if n.node_type == NodeType.SECTION.value
            and (n.declared_depth == 1 or n.parent_node_version_id == root_id or n.parent_node_version_id is None)
        ]
        top_sections.sort(key=lambda s: s.source_order)

        # Pre-count children for each section
        children_counts = {}
        for n in all_nodes:
            if n.parent_node_version_id:
                children_counts[n.parent_node_version_id] = (
                    children_counts.get(n.parent_node_version_id, 0) + 1
                )

        section_summaries = [
            SectionSummary(
                id=s.id,
                logical_node_id=s.logical_node_id,
                section_number=s.section_number,
                heading=s.heading,
                declared_depth=s.declared_depth,
                source_order=s.source_order,
                page_start=s.page_start,
                page_end=s.page_end,
                content_hash=s.content_hash,
                child_count=children_counts.get(s.id, 0),
            )
            for s in top_sections
        ]

        return BrowseSectionsResponse(
            document_slug=doc.slug,
            document_title=doc.title,
            version_id=ver.id,
            version_label=ver.version_label,
            is_latest=is_latest,
            total_sections=len(section_summaries),
            sections=section_summaries,
        )

    def get_node_detail(self, document_slug: str, node_version_id: str) -> NodeDetailResponse:
        """Retrieve full details of a specific node-version snapshot."""
        doc = self.repository.get_document_by_slug(document_slug)
        if doc is None:
            raise DocumentNotFoundError(f"Document '{document_slug}' not found")

        nv = self.repository.get_node_version_by_id(node_version_id)
        if nv is None:
            raise NodeNotFoundError(f"Node '{node_version_id}' not found")

        ver = self.repository.get_version_by_id(nv.document_version_id)
        if ver is None or ver.document_id != doc.id:
            raise CrossDocumentMismatchError(
                f"Node '{node_version_id}' does not belong to document '{document_slug}'"
            )

        # Fetch direct children
        all_nodes = self.repository.get_node_versions_by_version_id(ver.id)
        direct_children = [n for n in all_nodes if n.parent_node_version_id == nv.id]
        direct_children.sort(key=lambda c: c.source_order)

        child_summaries = []
        for c in direct_children:
            preview = ""
            if c.text:
                preview = c.text[:120].strip()
            elif c.structured_data and "rows" in c.structured_data:
                preview = f"Table with {len(c.structured_data['rows'])} rows"

            child_summaries.append(
                ChildNodeSummary(
                    id=c.id,
                    logical_node_id=c.logical_node_id,
                    node_type=c.node_type,
                    heading=c.heading,
                    section_number=c.section_number,
                    source_order=c.source_order,
                    page_start=c.page_start,
                    page_end=c.page_end,
                    content_hash=c.content_hash,
                    text_preview=preview,
                )
            )

        return NodeDetailResponse(
            id=nv.id,
            document_slug=doc.slug,
            document_version_id=nv.document_version_id,
            logical_node_id=nv.logical_node_id,
            parent_node_version_id=nv.parent_node_version_id,
            node_type=nv.node_type,
            heading=nv.heading,
            section_number=nv.section_number,
            declared_depth=nv.declared_depth,
            source_order=nv.source_order,
            page_start=nv.page_start,
            page_end=nv.page_end,
            page_numbers=nv.page_numbers or [nv.page_start],
            bbox=nv.bbox,
            text=nv.text,
            structured_data=nv.structured_data,
            content_hash=nv.content_hash,
            children=child_summaries,
        )
