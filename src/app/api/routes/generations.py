"""API route handlers for retrieving persisted QA generations and staleness reports."""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from src.app.api.dependencies import get_get_generation_use_case
from src.app.api.schemas.qa import GenerationResponse
from src.app.application.use_cases.get_generation import GetGenerationUseCase
from src.app.domain.exceptions import GenerationNotFoundError, InvalidQueryError

router = APIRouter(tags=["Generations"])


@router.get(
    "",
    response_model=List[GenerationResponse],
    status_code=status.HTTP_200_OK,
    summary="Query generations by selection or node filter",
)
def list_generations(
    selection_id: Optional[str] = Query(None, description="Filter generations by selection ID"),
    logical_node_id: Optional[str] = Query(None, description="Filter generations by logical node ID"),
    node_version_id: Optional[str] = Query(None, description="Filter generations by exact node-version ID"),
    use_case: GetGenerationUseCase = Depends(get_get_generation_use_case),
) -> List[GenerationResponse]:
    """Retrieve generations filtered by exactly one criteria: selection_id, logical_node_id, or node_version_id."""
    try:
        return use_case.find_generations(
            selection_id=selection_id,
            logical_node_id=logical_node_id,
            node_version_id=node_version_id,
        )
    except InvalidQueryError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get(
    "/{generation_id}",
    response_model=GenerationResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve a persisted QA generation document with retrieval-time staleness analysis",
)
def get_generation(
    generation_id: str,
    use_case: GetGenerationUseCase = Depends(get_get_generation_use_case),
) -> GenerationResponse:
    """Retrieve full append-only JSON generation document with dynamic retrieval-time staleness comparison."""
    try:
        return use_case.execute(generation_id=generation_id)
    except GenerationNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
