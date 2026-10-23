"""Use cases for creating and retrieving version-pinned selections."""

from typing import Dict, List
from src.app.api.schemas.selection import SelectedNodeItem, SelectionResponse
from src.app.application.interfaces.document_repository import IDocumentRepository
from src.app.domain.exceptions import (
    InvalidQueryError,
    SelectionNotFoundError,
)
from src.app.infrastructure.db.models import (
    NodeVersionORM,
    SelectionItemORM,
    SelectionORM,
    generate_id,
)


class CreateSelectionUseCase:
    """Use case validating and persisting a version-pinned selection."""

    def __init__(self, repository: IDocumentRepository) -> None:
        self.repository = repository

    def execute(
        self,
        name: str,
        document_version_id: str,
        node_version_ids: List[str],
    ) -> SelectionResponse:
        """Validate and create a named, version-pinned selection."""
        # 1. Basic validation
        clean_name = (name or "").strip()
        if not clean_name:
            raise InvalidQueryError("Selection name must not be empty")

        if not node_version_ids:
            raise InvalidQueryError("node_version_ids must contain at least one node ID")

        # 2. Duplicate node IDs check
        if len(node_version_ids) != len(set(node_version_ids)):
            raise InvalidQueryError("Duplicate node_version_ids are not allowed in a selection")

        # 3. Document version existence
        doc_ver = self.repository.get_version_by_id(document_version_id)
        if doc_ver is None:
            raise InvalidQueryError(f"Document version '{document_version_id}' does not exist")

        # 4. Validate every node exists and belongs to the specified version
        node_map: Dict[str, NodeVersionORM] = {}
        for nid in node_version_ids:
            nv = self.repository.get_node_version_by_id(nid)
            if nv is None:
                raise InvalidQueryError(f"Node '{nid}' does not exist")

            if nv.document_version_id != document_version_id:
                raise InvalidQueryError(
                    f"Node '{nid}' belongs to version '{nv.document_version_id}', not '{document_version_id}' "
                    f"(mixed versions and cross-document nodes are rejected)"
                )
            node_map[nid] = nv

        # 5. Build entities preserving submitted order
        selection_id = generate_id("sel")
        selection = SelectionORM(
            id=selection_id,
            document_version_id=document_version_id,
            name=clean_name,
        )

        items: List[SelectionItemORM] = []
        response_nodes: List[SelectedNodeItem] = []
        for pos, nid in enumerate(node_version_ids):
            nv = node_map[nid]
            item = SelectionItemORM(
                id=generate_id("item"),
                selection_id=selection_id,
                node_version_id=nid,
                position=pos,
                content_hash=nv.content_hash,
            )
            items.append(item)

            response_nodes.append(
                SelectedNodeItem(
                    node_version_id=nv.id,
                    logical_node_id=nv.logical_node_id,
                    position=pos,
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
                )
            )

        saved_selection = self.repository.create_selection(selection, items)

        return SelectionResponse(
            id=saved_selection.id,
            name=saved_selection.name,
            document_version_id=saved_selection.document_version_id,
            document_slug=doc_ver.document.slug,
            document_title=doc_ver.document.title,
            version_label=doc_ver.version_label,
            created_at=saved_selection.created_at,
            total_nodes=len(response_nodes),
            nodes=response_nodes,
        )


class GetSelectionUseCase:
    """Use case retrieving a version-pinned selection and its snapshot nodes."""

    def __init__(self, repository: IDocumentRepository) -> None:
        self.repository = repository

    def execute(self, selection_id: str) -> SelectionResponse:
        """Retrieve selection by persistent ID with all pinned node details."""
        selection = self.repository.get_selection_by_id(selection_id)
        if selection is None:
            raise SelectionNotFoundError(f"Selection '{selection_id}' not found")

        doc_ver = selection.document_version
        response_nodes: List[SelectedNodeItem] = []

        # Sort items by position to guarantee preserved order
        sorted_items = sorted(selection.items, key=lambda it: it.position)
        for item in sorted_items:
            nv = item.node_version
            response_nodes.append(
                SelectedNodeItem(
                    node_version_id=nv.id,
                    logical_node_id=nv.logical_node_id,
                    position=item.position,
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
                    content_hash=item.content_hash,
                )
            )

        return SelectionResponse(
            id=selection.id,
            name=selection.name,
            document_version_id=selection.document_version_id,
            document_slug=doc_ver.document.slug,
            document_title=doc_ver.document.title,
            version_label=doc_ver.version_label,
            created_at=selection.created_at,
            total_nodes=len(response_nodes),
            nodes=response_nodes,
        )
