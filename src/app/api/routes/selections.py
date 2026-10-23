from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from typing import Optional
from src.app.api.dependencies import (
    get_create_selection_use_case,
    get_generate_qa_use_case,
    get_get_selection_use_case,
)
from src.app.api.schemas.qa import GenerationResponse
from src.app.api.schemas.selection import CreateSelectionRequest, SelectionResponse
from src.app.application.use_cases.generate_qa import GenerateQAUseCase
from src.app.application.use_cases.manage_selections import (
    CreateSelectionUseCase,
    GetSelectionUseCase,
)
from src.app.domain.exceptions import (
    ConfigurationError,
    InvalidQueryError,
    LLMProviderError,
    LLMValidationError,
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


@router.post(
    "/{selection_id}/generations",
    response_model=GenerationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate validated QA test cases from a version-pinned selection",
)
def generate_qa_cases(
    selection_id: str,
    response: Response,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    use_case: GenerateQAUseCase = Depends(get_generate_qa_use_case),
) -> GenerationResponse:
    """Synthesize 3-5 executable QA test cases derived from pinned selection context."""
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(
            status_code=422,
            detail="Header 'Idempotency-Key' is required and must not be empty.",
        )
    try:
        doc = use_case.execute(
            selection_id=selection_id,
            idempotency_key=idempotency_key.strip(),
        )
        return GenerationResponse(**doc.model_dump())
    except SelectionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except LLMValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except LLMProviderError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except ConfigurationError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
