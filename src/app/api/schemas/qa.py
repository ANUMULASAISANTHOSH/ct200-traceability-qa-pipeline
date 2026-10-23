"""Pydantic schemas for QA test case generation and retrieval-time staleness API endpoints."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from src.app.application.services.staleness_service import (
    GenerationTraceability,
    SourceTraceabilityReport,
)
from src.app.domain.models.qa import (
    GenerationDocument,
    SourceSnapshot,
    TestCase,
    TestStep,
    ValidationAttempt,
)


class GenerationResponse(BaseModel):
    """API response model representing a persisted QA generation with retrieval-time staleness."""
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

    # Dynamic retrieval-time staleness & traceability assessment
    aggregate_status: Optional[str] = Field(
        default=None,
        description="Aggregate staleness status ('unknown' > 'removed' > 'stale' > 'current')",
    )
    latest_document_version_id: Optional[str] = Field(
        default=None,
        description="Latest document version ID compared against",
    )
    latest_version_label: Optional[str] = Field(
        default=None,
        description="Latest document version label (e.g., 'v2')",
    )
    source_traceability: List[SourceTraceabilityReport] = Field(
        default_factory=list,
        description="Per-source staleness and diff reports",
    )
    traceability: Optional[GenerationTraceability] = Field(
        default=None,
        description="Structured traceability report containing aggregate and per-source statuses",
    )
