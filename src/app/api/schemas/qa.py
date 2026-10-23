"""Pydantic schemas for QA test case generation API endpoints."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from src.app.domain.models.qa import (
    GenerationDocument,
    SourceSnapshot,
    TestCase,
    TestStep,
    ValidationAttempt,
)


class GenerationResponse(BaseModel):
    """API response model representing a persisted QA generation."""
    generation_id: str = Field(description="Unique persistent generation identifier")
    selection_id: str = Field(description="Persistent ID of the source selection")
    idempotency_key: str = Field(description="Client-provided idempotency key")
    prompt_version: str = Field(description="Version of prompt template applied")
    schema_version: str = Field(description="Version of output schema enforced")
    provider: str = Field(description="LLM provider name (e.g. 'mock', 'openai')")
    model: str = Field(description="Model identifier")
    status: str = Field(description="Status of generation: 'SUCCESS' or 'FAILED'")
    created_at: str = Field(description="UTC timestamp of generation creation")
    source_snapshots: List[SourceSnapshot] = Field(
        default_factory=list,
        description="All source node snapshots included in generation context",
    )
    validation_attempts: List[ValidationAttempt] = Field(
        default_factory=list,
        description="History of generation attempts and validation errors",
    )
    test_cases: Optional[List[TestCase]] = Field(
        default=None,
        description="Validated executable test cases (null if status is FAILED)",
    )
    error_message: Optional[str] = Field(
        default=None,
        description="Diagnostic error details if generation failed",
    )
