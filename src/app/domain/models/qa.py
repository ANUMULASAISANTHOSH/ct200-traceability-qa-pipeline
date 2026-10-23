"""Domain models for QA test case synthesis, validation, and generation documents."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class TestStep(BaseModel):
    """Individual action and expected result step in an executable test procedure."""
    model_config = ConfigDict(extra="forbid")

    step_number: int = Field(..., ge=1, description="Sequential 1-indexed step number")
    action: str = Field(..., min_length=1, description="Operator or automated action to execute")
    expected_result: str = Field(..., min_length=1, description="Verifiable expected system response")


class TestCase(BaseModel):
    """Complete, executable test case derived from manual specifications."""
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, description="Test case identifier, e.g., 'TC-001'")
    title: str = Field(..., min_length=1, description="Concise descriptive title of the test")
    preconditions: List[str] = Field(
        ..., min_length=1, description="Prerequisite conditions before test execution"
    )
    steps: List[TestStep] = Field(..., min_length=1, description="Ordered test procedure steps")
    node_references: List[str] = Field(
        ..., min_length=1, description="Source node-version IDs from context underpinning this test"
    )
    safety_critical: bool = Field(
        default=False, description="True if verifying alarm, hazard, or safety threshold"
    )


class TestSuiteOutput(BaseModel):
    """Container enforcing 3 to 5 executable test cases and strict structure."""
    model_config = ConfigDict(extra="forbid")

    test_cases: List[TestCase] = Field(
        ...,
        min_length=3,
        max_length=5,
        description="List of 3 to 5 complete, executable test cases",
    )


class SourceSnapshot(BaseModel):
    """Snapshot of a source node included in the generation context."""
    node_version_id: str
    logical_node_id: str
    node_type: str
    heading: Optional[str] = None
    section_number: Optional[str] = None
    text: str = ""
    structured_data: Optional[Dict[str, Any]] = None
    content_hash: str
    page_start: int
    page_end: int


class ValidationAttempt(BaseModel):
    """Record of an LLM generation attempt and any schema/semantic validation errors."""
    attempt: int
    raw_response: str
    errors: List[str] = Field(default_factory=list)


class GenerationDocument(BaseModel):
    """Schema for the append-only JSON file stored for each generation."""
    generation_id: str
    selection_id: str
    idempotency_key: str
    prompt_version: str
    schema_version: str
    provider: str
    model: str
    status: str  # "SUCCESS" or "FAILED"
    created_at: str
    source_snapshots: List[SourceSnapshot] = Field(default_factory=list)
    validation_attempts: List[ValidationAttempt] = Field(default_factory=list)
    test_cases: Optional[List[TestCase]] = None
    error_message: Optional[str] = None
