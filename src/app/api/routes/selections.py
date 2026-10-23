"""API route handlers for version-pinned selections."""

from fastapi import APIRouter, Depends, HTTPException, status
from src.app.api.dependencies import (
    get_create_selection_use_case,
    get_get_selection_use_case,
)
from src.app.api.schemas.selection import CreateSelectionRequest, SelectionResponse
from src.app.application.use_cases.manage_selections import (
    CreateSelectionUseCase,
    GetSelectionUseCase,
)
from src.app.domain.exceptions import (
    InvalidQueryError,
    SelectionNotFoundError,
)

router = APIRouter(tags=["Selections"])


@router.post(
    "",
    response_model=SelectionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a named, version-pinned node selection",
)
def create_selection(
    payload: CreateSelectionRequest,
    use_case: CreateSelectionUseCase = Depends(get_create_selection_use_case),
) -> SelectionResponse:
    """Validate and persist an ordered selection of nodes pinned to a specific document version."""
    try:
        return use_case.execute(
            name=payload.name,
            document_version_id=payload.document_version_id,
            node_version_ids=payload.node_version_ids,
        )
    except InvalidQueryError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get(
    "/{selection_id}",
    response_model=SelectionResponse,
    summary="Retrieve a version-pinned selection with full node snapshots",
)
def get_selection(
    selection_id: str,
    use_case: GetSelectionUseCase = Depends(get_get_selection_use_case),
) -> SelectionResponse:
    """Retrieve the selection and its pinned version, ordered nodes, headings, exact text, and content hashes."""
    try:
        return use_case.execute(selection_id=selection_id)
    except SelectionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
