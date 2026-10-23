"""API route handlers for versioned document browsing, search, and diffing."""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from src.app.api.dependencies import (
    get_browse_use_case,
    get_diff_use_case,
    get_repository,
    get_search_use_case,
)
from src.app.api.schemas.document import (
    BrowseSectionsResponse,
    DocumentDiffResponse,
    DocumentVersionsResponse,
    NodeDetailResponse,
    SearchResponse,
)
from src.app.application.use_cases.browse_document import BrowseDocumentUseCase
from src.app.application.use_cases.diff_versions import DiffVersionsUseCase
from src.app.application.use_cases.search_nodes import SearchNodesUseCase
from src.app.domain.exceptions import (
    CrossDocumentMismatchError,
    DocumentNotFoundError,
    InvalidQueryError,
    NodeNotFoundError,
    VersionNotFoundError,
)
from src.app.domain.models.diff import NodeDiffResult
from src.app.infrastructure.db.repositories.document_repository import (
    SQLAlchemyDocumentRepository,
)

router = APIRouter(tags=["Documents"])


@router.get(
    "/{document_slug}/versions",
    response_model=DocumentVersionsResponse,
    summary="List all versions for a document",
)
def list_versions(
    document_slug: str,
    browse_use_case: BrowseDocumentUseCase = Depends(get_browse_use_case),
) -> DocumentVersionsResponse:
    """Retrieve all ingested revisions for the specified document."""
    try:
        return browse_use_case.list_versions(document_slug)
    except DocumentNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get(
    "/{document_slug}/sections",
    response_model=BrowseSectionsResponse,
    summary="Browse top-level sections for a document version",
)
def browse_sections(
    document_slug: str,
    version_id: Optional[str] = Query(
        None,
        description="Explicit persistent version ID to browse. Defaults to the latest version if omitted.",
    ),
    browse_use_case: BrowseDocumentUseCase = Depends(get_browse_use_case),
) -> BrowseSectionsResponse:
    """Browse the root section hierarchy. Defaults to the latest version if version_id is not specified."""
    try:
        return browse_use_case.browse_sections(document_slug, version_id=version_id)
    except DocumentNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except VersionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get(
    "/{document_slug}/nodes/{node_version_id}",
    response_model=NodeDetailResponse,
    summary="Retrieve full detail of a specific node-version snapshot",
)
def get_node_detail(
    document_slug: str,
    node_version_id: str,
    browse_use_case: BrowseDocumentUseCase = Depends(get_browse_use_case),
) -> NodeDetailResponse:
    """Retrieve complete metadata, full text, content hash, structured table data, and child nodes."""
    try:
        return browse_use_case.get_node_detail(document_slug, node_version_id=node_version_id)
    except DocumentNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except NodeNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except CrossDocumentMismatchError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get(
    "/{document_slug}/search",
    response_model=SearchResponse,
    summary="Search headings and text within a document version",
)
def search_nodes(
    document_slug: str,
    query: str = Query(..., min_length=1, description="Search term"),
    version_id: Optional[str] = Query(
        None, description="Persistent version ID to search within. Defaults to latest version."
    ),
    node_type: Optional[str] = Query(
        None, description="Optional node-type filter: section, paragraph, table, list, list_item"
    ),
    limit: int = Query(
        50, ge=1, le=100, description="Maximum number of search results to return"
    ),
    search_use_case: SearchNodesUseCase = Depends(get_search_use_case),
) -> SearchResponse:
    """Search node headings, text, and tables with result ranking and snippet extraction."""
    try:
        return search_use_case.execute(
            document_slug=document_slug,
            query=query,
            version_id=version_id,
            node_type=node_type,
            limit=limit,
        )
    except DocumentNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except VersionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except InvalidQueryError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get(
    "/{document_slug}/diff/node",
    response_model=NodeDiffResult,
    summary="Compare one node across two document versions",
)
def diff_single_node(
    document_slug: str,
    v1_version_id: str = Query(..., description="Baseline version ID"),
    v2_version_id: str = Query(..., description="Target version ID"),
    logical_node_id: Optional[str] = Query(None, description="Logical node ID to compare"),
    node_version_id: Optional[str] = Query(None, description="Specific node-version ID to compare"),
    diff_use_case: DiffVersionsUseCase = Depends(get_diff_use_case),
    repo: SQLAlchemyDocumentRepository = Depends(get_repository),
) -> NodeDiffResult:
    """Compare a single logical node between two versions and return semantic status and text diff."""
    if v1_version_id == v2_version_id:
        raise HTTPException(
            status_code=422,
            detail="v1_version_id and v2_version_id must be distinct versions",
        )

    doc = repo.get_document_by_slug(document_slug)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document '{document_slug}' not found")

    v1_ver = repo.get_version_by_id(v1_version_id)
    if v1_ver is None or v1_ver.document_id != doc.id:
        raise HTTPException(
            status_code=404,
            detail=f"Version '{v1_version_id}' not found for document '{document_slug}'",
        )

    v2_ver = repo.get_version_by_id(v2_version_id)
    if v2_ver is None or v2_ver.document_id != doc.id:
        raise HTTPException(
            status_code=404,
            detail=f"Version '{v2_version_id}' not found for document '{document_slug}'",
        )

    target_logical_id = logical_node_id
    if not target_logical_id:
        if not node_version_id:
            raise HTTPException(
                status_code=422,
                detail="Either 'logical_node_id' or 'node_version_id' query parameter must be provided",
            )
        nv = repo.get_node_version_by_id(node_version_id)
        if nv is None:
            raise HTTPException(status_code=404, detail=f"Node '{node_version_id}' not found")
        if nv.document_version_id not in (v1_version_id, v2_version_id):
            raise HTTPException(
                status_code=404,
                detail=f"Node '{node_version_id}' does not belong to either compared version",
            )
        target_logical_id = nv.logical_node_id

    try:
        return diff_use_case.diff_logical_node(
            v1_version_id=v1_version_id,
            v2_version_id=v2_version_id,
            logical_node_id=target_logical_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get(
    "/{document_slug}/nodes/{node_version_id}/diff",
    response_model=NodeDiffResult,
    summary="Compare a node against another target version",
)
def diff_node_by_path_id(
    document_slug: str,
    node_version_id: str,
    target_version_id: str = Query(..., description="Target version ID to compare against"),
    diff_use_case: DiffVersionsUseCase = Depends(get_diff_use_case),
    repo: SQLAlchemyDocumentRepository = Depends(get_repository),
) -> NodeDiffResult:
    """Compare a node identified by persistent node-version ID against another version."""
    doc = repo.get_document_by_slug(document_slug)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document '{document_slug}' not found")

    nv = repo.get_node_version_by_id(node_version_id)
    if nv is None:
        raise HTTPException(status_code=404, detail=f"Node '{node_version_id}' not found")

    source_ver = repo.get_version_by_id(nv.document_version_id)
    if source_ver is None or source_ver.document_id != doc.id:
        raise HTTPException(
            status_code=404,
            detail=f"Node '{node_version_id}' does not belong to document '{document_slug}'",
        )

    target_ver = repo.get_version_by_id(target_version_id)
    if target_ver is None or target_ver.document_id != doc.id:
        raise HTTPException(
            status_code=404,
            detail=f"Target version '{target_version_id}' not found for document '{document_slug}'",
        )

    if source_ver.id == target_ver.id:
        raise HTTPException(
            status_code=422,
            detail="Cannot diff a node against the same version",
        )

    try:
        return diff_use_case.diff_logical_node(
            v1_version_id=source_ver.id,
            v2_version_id=target_ver.id,
            logical_node_id=nv.logical_node_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get(
    "/{document_slug}/diff",
    response_model=DocumentDiffResponse,
    summary="Compare two complete document versions",
)
def diff_complete_versions(
    document_slug: str,
    v1_version_id: str = Query(..., description="Baseline version ID"),
    v2_version_id: str = Query(..., description="Target version ID"),
    diff_use_case: DiffVersionsUseCase = Depends(get_diff_use_case),
    repo: SQLAlchemyDocumentRepository = Depends(get_repository),
) -> DocumentDiffResponse:
    """Compare two complete document revisions and return counts and summaries."""
    if v1_version_id == v2_version_id:
        raise HTTPException(
            status_code=422,
            detail="v1_version_id and v2_version_id must be distinct versions",
        )

    doc = repo.get_document_by_slug(document_slug)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document '{document_slug}' not found")

    v1_ver = repo.get_version_by_id(v1_version_id)
    if v1_ver is None or v1_ver.document_id != doc.id:
        raise HTTPException(
            status_code=404,
            detail=f"Version '{v1_version_id}' not found for document '{document_slug}'",
        )

    v2_ver = repo.get_version_by_id(v2_version_id)
    if v2_ver is None or v2_ver.document_id != doc.id:
        raise HTTPException(
            status_code=404,
            detail=f"Version '{v2_version_id}' not found for document '{document_slug}'",
        )

    try:
        report = diff_use_case.diff_versions(v1_version_id, v2_version_id)
        return DocumentDiffResponse(
            document_slug=doc.slug,
            v1_version_id=report.v1_version_id,
            v1_label=report.v1_label,
            v2_version_id=report.v2_version_id,
            v2_label=report.v2_label,
            summary_counts=report.summary_counts,
            node_diffs=report.node_diffs,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
