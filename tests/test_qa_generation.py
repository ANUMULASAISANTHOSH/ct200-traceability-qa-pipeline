"""Integration tests for validated QA test case generation, section expansion, retry, and JSON persistence."""

import json
import os
import shutil
import tempfile
import pytest
from starlette.testclient import TestClient

from src.app.api.dependencies import get_db, get_json_document_store, get_llm_client_dependency
from src.app.application.use_cases import IngestDocumentUseCase
from src.app.config import Settings
from src.app.domain.exceptions import ConfigurationError, LLMProviderError
from src.app.factory import create_app
from src.app.infrastructure.db import get_engine, get_session_factory, init_db
from src.app.infrastructure.db.repositories import SQLAlchemyDocumentRepository
from src.app.infrastructure.llm.client import MockLLMClient, OpenAILLMClient
from src.app.infrastructure.storage.json_store import JSONDocumentStore

V1_PDF = "data/ct200_manual.pdf"


@pytest.fixture(scope="module")
def qa_test_env():
    """Setup in-memory SQLite DB, temporary JSON store directory, and ingested v1 manual."""
    temp_json_dir = tempfile.mkdtemp(prefix="test_generations_")

    engine = get_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = get_session_factory(engine)

    with session_factory() as session:
        repo = SQLAlchemyDocumentRepository(session)
        ingest = IngestDocumentUseCase(repo)
        res = ingest.execute(V1_PDF, "ct200", "CT-200 Sphygmomanometer Manual", "v1")
        v1_id = res.version.id
        v1_nodes = repo.get_node_versions_by_version_id(v1_id)

    yield {
        "engine": engine,
        "session_factory": session_factory,
        "v1_id": v1_id,
        "v1_nodes": v1_nodes,
        "json_dir": temp_json_dir,
    }

    shutil.rmtree(temp_json_dir, ignore_errors=True)


def build_test_client(qa_test_env, mock_client: MockLLMClient):
    """Construct TestClient with overridden DB, store, and LLM mock."""
    session_factory = qa_test_env["session_factory"]
    json_dir = qa_test_env["json_dir"]

    settings = Settings(
        app_name="QA Backend Test",
        app_env="test",
        database_url="sqlite:///:memory:",
        json_store_path=json_dir,
    )
    app = create_app(settings)

    def override_get_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def override_json_store():
        return JSONDocumentStore(base_path=json_dir)

    def override_llm_client():
        return mock_client

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_json_document_store] = override_json_store
    app.dependency_overrides[get_llm_client_dependency] = override_llm_client

    return TestClient(app)


def test_fake_client_valid_generation_and_storage(qa_test_env):
    """Test generating 3-5 valid executable QA cases with default MockLLMClient and verifying JSON persistence."""
    mock_llm = MockLLMClient()
    client = build_test_client(qa_test_env, mock_llm)
    v1_id = qa_test_env["v1_id"]
    v1_nodes = qa_test_env["v1_nodes"]

    # 1. Create selection with 2 specific nodes
    target_nodes = [v1_nodes[0].id, v1_nodes[1].id]
    sel_resp = client.post(
        "/api/v1/selections",
        json={
            "name": "Power and Battery Scope",
            "document_version_id": v1_id,
            "node_version_ids": target_nodes,
        },
    )
    assert sel_resp.status_code == 201
    selection_id = sel_resp.json()["id"]

    # 2. Request QA generation
    idempotency_key = "test-key-001"
    gen_resp = client.post(
        f"/api/v1/selections/{selection_id}/generations",
        headers={"Idempotency-Key": idempotency_key},
    )
    assert gen_resp.status_code == 201
    data = gen_resp.json()

    # Verify response schema
    assert data["generation_id"].startswith("gen_")
    assert data["selection_id"] == selection_id
    assert data["idempotency_key"] == idempotency_key
    assert data["status"] == "SUCCESS"
    assert data["prompt_version"] == "v1.0"
    assert data["schema_version"] == "v1.0"
    assert data["provider"] == "mock"

    # Enforce 3 to 5 test cases limit
    cases = data["test_cases"]
    assert 3 <= len(cases) <= 5

    # Check completeness of each test case
    valid_context_ids = {s["node_version_id"] for s in data["source_snapshots"]}
    for tc in cases:
        assert tc["id"].startswith("TC-")
        assert len(tc["title"]) > 0
        assert len(tc["preconditions"]) > 0
        assert len(tc["steps"]) > 0
        for step in tc["steps"]:
            assert step["step_number"] >= 1
            assert len(step["action"]) > 0
            assert len(step["expected_result"]) > 0
        assert len(tc["node_references"]) > 0
        for ref in tc["node_references"]:
            assert ref in valid_context_ids

    # 3. Retrieve generation by generation_id
    generation_id = data["generation_id"]
    get_resp = client.get(f"/api/v1/generations/{generation_id}")
    assert get_resp.status_code == 200
    retrieved = get_resp.json()
    assert retrieved["generation_id"] == generation_id
    assert retrieved["status"] == "SUCCESS"
    assert len(retrieved["test_cases"]) == len(cases)

    # 4. Check JSON document exists physically on disk
    store = JSONDocumentStore(qa_test_env["json_dir"])
    doc_path = store.get_document_path(generation_id)
    assert doc_path.is_file()
    with open(doc_path, "r", encoding="utf-8") as f:
        file_json = json.load(f)
    assert file_json["generation_id"] == generation_id
    assert len(file_json["source_snapshots"]) > 0


def test_section_expansion_and_deduplication(qa_test_env):
    """Test that selecting a section expands to include descendants, and direct descendant selection is deduplicated."""
    mock_llm = MockLLMClient()
    client = build_test_client(qa_test_env, mock_llm)
    v1_id = qa_test_env["v1_id"]
    v1_nodes = qa_test_env["v1_nodes"]

    # Find Section 2.1 and Section 2.1.1.1
    sec_2_1 = [n for n in v1_nodes if n.section_number == "2.1"][0]
    sec_2_1_1_1 = [n for n in v1_nodes if n.section_number == "2.1.1.1"][0]

    # Select Section 2.1 FIRST, and also directly select 2.1.1.1 (which is a descendant)
    sel_resp = client.post(
        "/api/v1/selections",
        json={
            "name": "Section Expansion & Deduplication Test",
            "document_version_id": v1_id,
            "node_version_ids": [sec_2_1.id, sec_2_1_1_1.id],
        },
    )
    assert sel_resp.status_code == 201
    selection_id = sel_resp.json()["id"]

    # Generate QA cases
    gen_resp = client.post(
        f"/api/v1/selections/{selection_id}/generations",
        headers={"Idempotency-Key": "dedup-expansion-key-01"},
    )
    assert gen_resp.status_code == 201
    data = gen_resp.json()

    # Inspect source snapshots included in context
    snapshot_node_ids = [s["node_version_id"] for s in data["source_snapshots"]]

    # Section 2.1.1.1 must appear exactly ONCE
    assert snapshot_node_ids.count(sec_2_1_1_1.id) == 1
    # Section 2.1 must appear
    assert sec_2_1.id in snapshot_node_ids
    # Descendants (e.g. paragraphs under 2.1 / 2.1.1.1) must be included
    assert len(snapshot_node_ids) > 2


def test_duplicate_idempotency_returns_cached_without_llm_call(qa_test_env):
    """Submitting the identical Idempotency-Key for the same selection returns the existing generation without LLM calls."""
    mock_llm = MockLLMClient()
    client = build_test_client(qa_test_env, mock_llm)
    v1_id = qa_test_env["v1_id"]
    v1_nodes = qa_test_env["v1_nodes"]

    sel_resp = client.post(
        "/api/v1/selections",
        json={
            "name": "Idempotency Test Scope",
            "document_version_id": v1_id,
            "node_version_ids": [v1_nodes[0].id],
        },
    )
    selection_id = sel_resp.json()["id"]

    idem_key = "idemp-cache-key-999"

    # First call: triggers LLM generation
    r1 = client.post(
        f"/api/v1/selections/{selection_id}/generations",
        headers={"Idempotency-Key": idem_key},
    )
    assert r1.status_code == 201
    gen_id_1 = r1.json()["generation_id"]
    call_count_1 = mock_llm.call_count
    assert call_count_1 >= 1

    # Second call with SAME key: must return cached result directly
    r2 = client.post(
        f"/api/v1/selections/{selection_id}/generations",
        headers={"Idempotency-Key": idem_key},
    )
    assert r2.status_code == 201
    assert r2.json()["generation_id"] == gen_id_1
    assert mock_llm.call_count == call_count_1  # No additional LLM call!


def test_different_idempotency_key_triggers_new_generation(qa_test_env):
    """Submitting a different Idempotency-Key triggers a new generation even for the same selection."""
    mock_llm = MockLLMClient()
    client = build_test_client(qa_test_env, mock_llm)
    v1_id = qa_test_env["v1_id"]
    v1_nodes = qa_test_env["v1_nodes"]

    sel_resp = client.post(
        "/api/v1/selections",
        json={
            "name": "Different Key Scope",
            "document_version_id": v1_id,
            "node_version_ids": [v1_nodes[0].id],
        },
    )
    selection_id = sel_resp.json()["id"]

    r1 = client.post(
        f"/api/v1/selections/{selection_id}/generations",
        headers={"Idempotency-Key": "key-alpha"},
    )
    assert r1.status_code == 201
    gen_id_1 = r1.json()["generation_id"]

    r2 = client.post(
        f"/api/v1/selections/{selection_id}/generations",
        headers={"Idempotency-Key": "key-beta"},
    )
    assert r2.status_code == 201
    gen_id_2 = r2.json()["generation_id"]

    assert gen_id_1 != gen_id_2


def test_missing_or_blank_idempotency_key_rejected(qa_test_env):
    """Requests missing or providing empty Idempotency-Key headers are rejected with 422."""
    mock_llm = MockLLMClient()
    client = build_test_client(qa_test_env, mock_llm)
    v1_id = qa_test_env["v1_id"]
    v1_nodes = qa_test_env["v1_nodes"]

    sel_resp = client.post(
        "/api/v1/selections",
        json={
            "name": "Missing Header Scope",
            "document_version_id": v1_id,
            "node_version_ids": [v1_nodes[0].id],
        },
    )
    selection_id = sel_resp.json()["id"]

    # Missing header
    r_missing = client.post(f"/api/v1/selections/{selection_id}/generations")
    assert r_missing.status_code == 422

    # Blank header
    r_blank = client.post(
        f"/api/v1/selections/{selection_id}/generations",
        headers={"Idempotency-Key": "   "},
    )
    assert r_blank.status_code == 422


def test_malformed_json_retry_recovery(qa_test_env):
    """When attempt 1 returns malformed JSON, system retries once with feedback and succeeds on attempt 2."""
    v1_id = qa_test_env["v1_id"]
    v1_nodes = qa_test_env["v1_nodes"]

    # Setup canned responses: 1st is malformed JSON, 2nd is valid suite
    valid_suite = {
        "test_cases": [
            {
                "id": "TC-001",
                "title": "Startup Self-Test Verification",
                "preconditions": ["Device connected to AC power"],
                "steps": [
                    {
                        "step_number": 1,
                        "action": "Press power key",
                        "expected_result": "Self test passes",
                    }
                ],
                "node_references": [v1_nodes[0].id],
                "safety_critical": False,
            },
            {
                "id": "TC-002",
                "title": "Low Battery Warning Verification",
                "preconditions": ["Battery simulator attached"],
                "steps": [
                    {
                        "step_number": 1,
                        "action": "Drop voltage below threshold",
                        "expected_result": "Yellow indicator flashes",
                    }
                ],
                "node_references": [v1_nodes[0].id],
                "safety_critical": True,
            },
            {
                "id": "TC-003",
                "title": "Overpressure Cutoff Verification",
                "preconditions": ["Cuff simulator connected"],
                "steps": [
                    {
                        "step_number": 1,
                        "action": "Exceed 300 mmHg",
                        "expected_result": "Rapid exhaust valve opens",
                    }
                ],
                "node_references": [v1_nodes[0].id],
                "safety_critical": True,
            },
        ]
    }

    mock_llm = MockLLMClient(
        canned_responses=[
            "{ malformed json missing closing brace: [",
            json.dumps(valid_suite),
        ]
    )
    client = build_test_client(qa_test_env, mock_llm)

    sel_resp = client.post(
        "/api/v1/selections",
        json={
            "name": "Retry Recovery Scope",
            "document_version_id": v1_id,
            "node_version_ids": [v1_nodes[0].id],
        },
    )
    selection_id = sel_resp.json()["id"]

    gen_resp = client.post(
        f"/api/v1/selections/{selection_id}/generations",
        headers={"Idempotency-Key": "retry-recovery-key-1"},
    )
    assert gen_resp.status_code == 201
    data = gen_resp.json()
    assert data["status"] == "SUCCESS"
    assert len(data["validation_attempts"]) == 2
    assert "Malformed JSON" in data["validation_attempts"][0]["errors"][0]
    assert len(data["validation_attempts"][1]["errors"]) == 0
    assert len(data["test_cases"]) == 3


def test_schema_error_one_retry_recovery(qa_test_env):
    """When attempt 1 violates schema (only 2 test cases), retry provides feedback and succeeds."""
    v1_id = qa_test_env["v1_id"]
    v1_nodes = qa_test_env["v1_nodes"]

    # Canned response 1: Only 2 test cases (violates min_length=3)
    invalid_schema_suite = {
        "test_cases": [
            {
                "id": "TC-001",
                "title": "Case 1",
                "preconditions": ["P1"],
                "steps": [{"step_number": 1, "action": "A1", "expected_result": "R1"}],
                "node_references": [v1_nodes[0].id],
                "safety_critical": False,
            },
            {
                "id": "TC-002",
                "title": "Case 2",
                "preconditions": ["P2"],
                "steps": [{"step_number": 1, "action": "A2", "expected_result": "R2"}],
                "node_references": [v1_nodes[0].id],
                "safety_critical": False,
            },
        ]
    }

    # Canned response 2: 3 valid cases
    valid_suite = {
        "test_cases": [
            {
                "id": "TC-001",
                "title": "Case 1",
                "preconditions": ["P1"],
                "steps": [{"step_number": 1, "action": "A1", "expected_result": "R1"}],
                "node_references": [v1_nodes[0].id],
                "safety_critical": False,
            },
            {
                "id": "TC-002",
                "title": "Case 2",
                "preconditions": ["P2"],
                "steps": [{"step_number": 1, "action": "A2", "expected_result": "R2"}],
                "node_references": [v1_nodes[0].id],
                "safety_critical": False,
            },
            {
                "id": "TC-003",
                "title": "Case 3",
                "preconditions": ["P3"],
                "steps": [{"step_number": 1, "action": "A3", "expected_result": "R3"}],
                "node_references": [v1_nodes[0].id],
                "safety_critical": True,
            },
        ]
    }

    mock_llm = MockLLMClient(
        canned_responses=[
            json.dumps(invalid_schema_suite),
            json.dumps(valid_suite),
        ]
    )
    client = build_test_client(qa_test_env, mock_llm)

    sel_resp = client.post(
        "/api/v1/selections",
        json={
            "name": "Schema Error Scope",
            "document_version_id": v1_id,
            "node_version_ids": [v1_nodes[0].id],
        },
    )
    selection_id = sel_resp.json()["id"]

    gen_resp = client.post(
        f"/api/v1/selections/{selection_id}/generations",
        headers={"Idempotency-Key": "schema-retry-key-2"},
    )
    assert gen_resp.status_code == 201
    data = gen_resp.json()
    assert data["status"] == "SUCCESS"
    assert len(data["validation_attempts"]) == 2
    err_msg = data["validation_attempts"][0]["errors"][0]
    assert "List should have at least 3 items" in err_msg or "Expected between 3 and 5 test cases" in err_msg


def test_invalid_source_references_retry_recovery(qa_test_env):
    """When attempt 1 references a non-existent or out-of-context node, retry feedback prompts valid reference."""
    v1_id = qa_test_env["v1_id"]
    v1_nodes = qa_test_env["v1_nodes"]

    # Canned response 1: references out-of-context 'nv_ghost_node_xyz'
    bad_ref_suite = {
        "test_cases": [
            {
                "id": "TC-001",
                "title": "Bad Reference Case",
                "preconditions": ["P1"],
                "steps": [{"step_number": 1, "action": "A1", "expected_result": "R1"}],
                "node_references": ["nv_ghost_node_xyz"],
                "safety_critical": False,
            },
            {
                "id": "TC-002",
                "title": "Case 2",
                "preconditions": ["P2"],
                "steps": [{"step_number": 1, "action": "A2", "expected_result": "R2"}],
                "node_references": [v1_nodes[0].id],
                "safety_critical": False,
            },
            {
                "id": "TC-003",
                "title": "Case 3",
                "preconditions": ["P3"],
                "steps": [{"step_number": 1, "action": "A3", "expected_result": "R3"}],
                "node_references": [v1_nodes[0].id],
                "safety_critical": False,
            },
        ]
    }

    # Canned response 2: corrected references
    good_suite = {
        "test_cases": [
            {
                "id": "TC-001",
                "title": "Corrected Case",
                "preconditions": ["P1"],
                "steps": [{"step_number": 1, "action": "A1", "expected_result": "R1"}],
                "node_references": [v1_nodes[0].id],
                "safety_critical": False,
            },
            {
                "id": "TC-002",
                "title": "Case 2",
                "preconditions": ["P2"],
                "steps": [{"step_number": 1, "action": "A2", "expected_result": "R2"}],
                "node_references": [v1_nodes[0].id],
                "safety_critical": False,
            },
            {
                "id": "TC-003",
                "title": "Case 3",
                "preconditions": ["P3"],
                "steps": [{"step_number": 1, "action": "A3", "expected_result": "R3"}],
                "node_references": [v1_nodes[0].id],
                "safety_critical": False,
            },
        ]
    }

    mock_llm = MockLLMClient(
        canned_responses=[
            json.dumps(bad_ref_suite),
            json.dumps(good_suite),
        ]
    )
    client = build_test_client(qa_test_env, mock_llm)

    sel_resp = client.post(
        "/api/v1/selections",
        json={
            "name": "Bad Ref Scope",
            "document_version_id": v1_id,
            "node_version_ids": [v1_nodes[0].id],
        },
    )
    selection_id = sel_resp.json()["id"]

    gen_resp = client.post(
        f"/api/v1/selections/{selection_id}/generations",
        headers={"Idempotency-Key": "bad-ref-key-3"},
    )
    assert gen_resp.status_code == 201
    data = gen_resp.json()
    assert data["status"] == "SUCCESS"
    assert "outside the generation context" in data["validation_attempts"][0]["errors"][0]


def test_terminal_failure_persists_record_and_masks_partial_cases(qa_test_env):
    """When both attempts fail validation, a failed record is persisted with status=FAILED, error returned, and no partial cases exposed."""
    v1_id = qa_test_env["v1_id"]
    v1_nodes = qa_test_env["v1_nodes"]

    mock_llm = MockLLMClient(
        canned_responses=[
            "{ broken json attempt 1",
            "{ broken json attempt 2",
        ]
    )
    client = build_test_client(qa_test_env, mock_llm)

    sel_resp = client.post(
        "/api/v1/selections",
        json={
            "name": "Terminal Failure Scope",
            "document_version_id": v1_id,
            "node_version_ids": [v1_nodes[0].id],
        },
    )
    selection_id = sel_resp.json()["id"]

    gen_resp = client.post(
        f"/api/v1/selections/{selection_id}/generations",
        headers={"Idempotency-Key": "term-fail-key-4"},
    )
    # Returns 422 error
    assert gen_resp.status_code == 422
    assert "validation after 2 attempts" in gen_resp.json()["detail"].lower()

    # Verify SQLite index has recorded the failed record
    session_factory = qa_test_env["session_factory"]
    with session_factory() as session:
        repo = SQLAlchemyDocumentRepository(session)
        failed_record = repo.get_generation_by_idempotency_key(selection_id, "term-fail-key-4")
        assert failed_record is not None
        assert failed_record.status == "FAILED"
        assert failed_record.test_case_count == 0

        # Retrieve through GET /api/v1/generations/{generation_id}
        get_resp = client.get(f"/api/v1/generations/{failed_record.id}")
        assert get_resp.status_code == 200
        failed_doc = get_resp.json()
        assert failed_doc["status"] == "FAILED"
        assert failed_doc["test_cases"] is None  # Partial cases NOT exposed!
        assert len(failed_doc["validation_attempts"]) == 2
        assert failed_doc["error_message"] is not None


def test_real_provider_without_credentials_raises_clear_config_error():
    """Requesting the OpenAI real provider adapter without an API key raises ConfigurationError immediately."""
    with pytest.raises(ConfigurationError) as exc_info:
        OpenAILLMClient(api_key=None, model="gpt-4o")
    assert "LLM_API_KEY is not configured" in str(exc_info.value)

    with pytest.raises(ConfigurationError) as exc_info2:
        OpenAILLMClient(api_key="   ", model="gpt-4o")
    assert "LLM_API_KEY is not configured" in str(exc_info2.value)
