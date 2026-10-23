"""API route handlers for retrieving persisted QA generations."""

from fastapi import APIRouter, Depends, HTTPException, status
from src.app.api.dependencies import get_get_generation_use_case
from src.app.api.schemas.qa import GenerationResponse
from src.app.application.use_cases.get_generation import GetGenerationUseCase
from src.app.domain.exceptions import GenerationNotFoundError

router = APIRouter(tags=["Generations"])


@router.get(
    "/{generation_id}",
    response_model=GenerationResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve a persisted QA generation document",
)
def get_generation(
    generation_id: str,
    use_case: GetGenerationUseCase = Depends(get_get_generation_use_case),
) -> GenerationResponse:
    """Retrieve full append-only JSON generation document including exact source snapshots and test cases."""
    try:
        doc = use_case.execute(generation_id=generation_id)
        return GenerationResponse(**doc.model_dump())
    except GenerationNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
