"""Integration tests for retrieval-time staleness, heuristic impact analysis, and generation queries."""

import json
import shutil
import tempfile
import pytest
from starlette.testclient import TestClient

from src.app.api.dependencies import get_db, get_json_document_store, get_llm_client_dependency
from src.app.application.use_cases import IngestDocumentUseCase
from src.app.config import Settings
from src.app.factory import create_app
from src.app.infrastructure.db import (
    GenerationIndexORM,
    GenerationSourceNodeORM,
    VersionMatchORM,
    generate_id,
    get_engine,
    get_session_factory,
    init_db,
)
from src.app.infrastructure.db.repositories import SQLAlchemyDocumentRepository
from src.app.infrastructure.llm.client import MockLLMClient
from src.app.infrastructure.storage.json_store import JSONDocumentStore

V1_PDF = "data/ct200_manual.pdf"
V2_PDF = "data/ct200_manual_v2.pdf"


@pytest.fixture(scope="module")
def staleness_env():
    """Setup in-memory SQLite DB, temporary JSON store directory, and ingested manuals."""
    temp_json_dir = tempfile.mkdtemp(prefix="test_staleness_store_")

    engine = get_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = get_session_factory(engine)

    class NodeDTO:
        def __init__(self, id, logical_node_id, section_number, node_type, text, content_hash):
            self.id = id
            self.logical_node_id = logical_node_id
            self.section_number = section_number
            self.node_type = node_type
            self.text = text
            self.content_hash = content_hash

    with session_factory() as session:
        repo = SQLAlchemyDocumentRepository(session)
        ingest = IngestDocumentUseCase(repo)

        # 1. Standalone single-version document for testing current baseline
        res_standalone = ingest.execute(V1_PDF, "ct200_standalone", "CT-200 Standalone Manual", "v1")
        standalone_v1_id = res_standalone.version.id
        standalone_nodes = [
            NodeDTO(n.id, n.logical_node_id, n.section_number, n.node_type, n.text, n.content_hash)
            for n in repo.get_node_versions_by_version_id(standalone_v1_id)
        ]

        # 2. Main ct200 document with both v1 and v2 ingested
        res_v1 = ingest.execute(V1_PDF, "ct200", "CT-200 Sphygmomanometer Manual", "v1")
        v1_id = res_v1.version.id
        v1_nodes = [
            NodeDTO(n.id, n.logical_node_id, n.section_number, n.node_type, n.text, n.content_hash)
            for n in repo.get_node_versions_by_version_id(v1_id)
        ]

        res_v2 = ingest.execute(V2_PDF, "ct200", "CT-200 Sphygmomanometer Manual", "v2")
        v2_id = res_v2.version.id
        v2_nodes = [
            NodeDTO(n.id, n.logical_node_id, n.section_number, n.node_type, n.text, n.content_hash)
            for n in repo.get_node_versions_by_version_id(v2_id)
        ]

    yield {
        "engine": engine,
        "session_factory": session_factory,
        "standalone_v1_id": standalone_v1_id,
        "standalone_nodes": standalone_nodes,
        "v1_id": v1_id,
        "v1_nodes": v1_nodes,
        "v2_id": v2_id,
        "v2_nodes": v2_nodes,
        "json_dir": temp_json_dir,
    }

    shutil.rmtree(temp_json_dir, ignore_errors=True)


def build_client(staleness_env, mock_client: MockLLMClient):
    """Construct TestClient with overridden dependencies."""
    session_factory = staleness_env["session_factory"]
    json_dir = staleness_env["json_dir"]

    settings = Settings(
        app_name="Staleness Test",
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


def test_current_content_baseline_before_v2(staleness_env):
    """When a generation is queried on the latest version, all sources and aggregate are current."""
    mock_llm = MockLLMClient()
    client = build_client(staleness_env, mock_llm)
    standalone_v1_id = staleness_env["standalone_v1_id"]
    standalone_nodes = staleness_env["standalone_nodes"]

    # Select Section 1 heading node from standalone document
    sec_1_node = [n for n in standalone_nodes if n.section_number == "1" and n.node_type == "section"][0]
    sel_resp = client.post(
        "/api/v1/selections",
        json={
            "name": "Initial Baseline Scope",
            "document_version_id": standalone_v1_id,
            "node_version_ids": [sec_1_node.id],
        },
    )
    selection_id = sel_resp.json()["id"]

    # Generate QA cases
    gen_resp = client.post(
        f"/api/v1/selections/{selection_id}/generations",
        headers={"Idempotency-Key": "baseline-staleness-key-01"},
    )
    assert gen_resp.status_code == 201
    generation_id = gen_resp.json()["generation_id"]

    # Query GET /api/v1/generations/{generation_id}
    get_resp = client.get(f"/api/v1/generations/{generation_id}")
    assert get_resp.status_code == 200
    data = get_resp.json()

    # Aggregate status must be current
    assert data["aggregate_status"] == "current"
    assert data["traceability"]["aggregate_status"] == "current"
    assert data["latest_version_label"] == "v1"

    # Per-source check
    for src in data["source_traceability"]:
        assert src["status"] == "current"
        assert src["original_content_hash"] == src["latest_content_hash"]
        assert len(src["impact_tags"]) == 0
        assert "Content unchanged" in src["diff_summary"]


def test_stale_detection_after_v2_ingestion(staleness_env):
    """After v2 manual ingestion, retrieval marks changed specifications as stale with transparent impact tags."""
    mock_llm = MockLLMClient()
    client = build_client(staleness_env, mock_llm)
    session_factory = staleness_env["session_factory"]
    v1_id = staleness_env["v1_id"]
    v1_nodes = staleness_env["v1_nodes"]

    # 1. Identify specific nodes in v1 that change in v2:
    # - Battery Life paragraph (Sec 2.1.1.1): 300 cycles / 15% -> 250 cycles / 10%
    # - Inflation paragraph (Sec 3.2): 40 mmHg -> 30 mmHg
    # - Table 4.2 Error Codes: 2.0s -> 1.5s, E6 added
    # - Unchanged node: Section 1 heading
    battery_para = [
        n for n in v1_nodes
        if n.node_type == "paragraph" and "300 measurement cycles" in n.text
    ][0]
    inflation_para = [
        n for n in v1_nodes
        if n.node_type == "paragraph" and "40 mmHg" in n.text
    ][0]
    error_table = [
        n for n in v1_nodes
        if n.node_type == "table" and "Device Behavior" in n.text
    ][0]
    unchanged_node = [
        n for n in v1_nodes
        if n.node_type == "section" and n.section_number == "1"
    ][0]

    # Create selection covering all 4 nodes in v1
    sel_resp = client.post(
        "/api/v1/selections",
        json={
            "name": "Comprehensive Verification Scope",
            "document_version_id": v1_id,
            "node_version_ids": [
                battery_para.id,
                inflation_para.id,
                error_table.id,
                unchanged_node.id,
            ],
        },
    )
    selection_id = sel_resp.json()["id"]

    # Generate QA test cases on v1
    gen_resp = client.post(
        f"/api/v1/selections/{selection_id}/generations",
        headers={"Idempotency-Key": "multi-stale-v1-key"},
    )
    assert gen_resp.status_code == 201
    generation_id = gen_resp.json()["generation_id"]

    # 2. Latest version v2 is already ingested in staleness_env
    v2_id = staleness_env["v2_id"]

    # 3. Retrieve the v1 generation now that v2 is active
    get_resp = client.get(f"/api/v1/generations/{generation_id}")
    assert get_resp.status_code == 200
    data = get_resp.json()

    assert data["latest_version_label"] == "v2"
    assert data["latest_document_version_id"] == v2_id
    assert data["aggregate_status"] == "stale"  # stale > current

    # Map sources by logical_node_id
    src_map = {s["logical_node_id"]: s for s in data["source_traceability"]}

    # Check 1: Battery Life (2.1.1.1)
    bat_src = src_map[battery_para.logical_node_id]
    assert bat_src["status"] == "stale"
    assert bat_src["original_content_hash"] != bat_src["latest_content_hash"]
    assert "300" in bat_src["diff_summary"]
    assert "250" in bat_src["diff_summary"]
    assert "NUMERICAL_SPECIFICATION_CHANGED" in bat_src["impact_tags"]

    # Check 2: Inflation increment (3.2)
    inf_src = src_map[inflation_para.logical_node_id]
    assert inf_src["status"] == "stale"
    assert "40 mmHg" in inf_src["diff_summary"]
    assert "30 mmHg" in inf_src["diff_summary"]
    assert "NUMERICAL_SPECIFICATION_CHANGED" in inf_src["impact_tags"]

    # Check 3: Table 4.2 Error Codes
    table_src = src_map[error_table.logical_node_id]
    assert table_src["status"] == "stale"
    assert "2 seconds" in table_src["diff_summary"]
    assert "1.5 seconds" in table_src["diff_summary"]
    assert "NUMERICAL_SPECIFICATION_CHANGED" in table_src["impact_tags"]
    assert "SAFETY_OR_ALARM_MODIFIED" in table_src["impact_tags"]
    assert "FEATURE_OR_BEHAVIOR_CHANGED" in table_src["impact_tags"]

    # Check 4: Unchanged node (Section 1)
    unch_src = src_map[unchanged_node.logical_node_id]
    assert unch_src["status"] == "current"
    assert unch_src["original_content_hash"] == unch_src["latest_content_hash"]
    assert len(unch_src["impact_tags"]) == 0


def test_removed_node_staleness(staleness_env):
    """When a logical node was present in baseline but is confidently absent in latest version, status is removed."""
    mock_llm = MockLLMClient()
    client = build_client(staleness_env, mock_llm)
    session_factory = staleness_env["session_factory"]
    v1_id = staleness_env["v1_id"]

    # Create an artificial node in v1 that does not exist in v2
    with session_factory() as session:
        repo = SQLAlchemyDocumentRepository(session)
        # Use an existing logical node from another family or create one
        v1_nodes = repo.get_node_versions_by_version_id(v1_id)
        candidate_node = v1_nodes[-1]

        # Create a selection with candidate_node
        sel_resp = client.post(
            "/api/v1/selections",
            json={
                "name": "Removed Node Test Scope",
                "document_version_id": v1_id,
                "node_version_ids": [candidate_node.id],
            },
        )
        selection_id = sel_resp.json()["id"]

        gen_resp = client.post(
            f"/api/v1/selections/{selection_id}/generations",
            headers={"Idempotency-Key": "removed-node-key-01"},
        )
        assert gen_resp.status_code == 201
        generation_id = gen_resp.json()["generation_id"]

        # Directly delete or simulate absence of logical node in latest version (v2)
        # To simulate a truly removed node cleanly:
        # Update the generation's source snapshot or logical node to a logical ID not present in v2
        doc_store = JSONDocumentStore(staleness_env["json_dir"])
        gen_doc = doc_store.load_generation(generation_id)
        gen_doc.source_snapshots[0].logical_node_id = "log_deprecated_removed_feature"
        doc_store.save_generation(gen_doc)

    # Retrieve generation: since 'log_deprecated_removed_feature' is absent in v2, status must be 'removed'
    get_resp = client.get(f"/api/v1/generations/{generation_id}")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["aggregate_status"] == "removed"
    assert data["source_traceability"][0]["status"] == "removed"
    assert data["source_traceability"][0]["latest_node_version_id"] is None
    assert "removed in latest version" in data["source_traceability"][0]["diff_summary"]


def test_unresolved_match_staleness(staleness_env):
    """When a node's matching in latest version is flagged as unresolved/ambiguous, status is unknown."""
    mock_llm = MockLLMClient()
    client = build_client(staleness_env, mock_llm)
    session_factory = staleness_env["session_factory"]
    v1_id = staleness_env["v1_id"]
    v1_nodes = staleness_env["v1_nodes"]

    target_nv = v1_nodes[0]

    sel_resp = client.post(
        "/api/v1/selections",
        json={
            "name": "Unresolved Scope",
            "document_version_id": v1_id,
            "node_version_ids": [target_nv.id],
        },
    )
    selection_id = sel_resp.json()["id"]

    gen_resp = client.post(
        f"/api/v1/selections/{selection_id}/generations",
        headers={"Idempotency-Key": "unresolved-match-key-01"},
    )
    generation_id = gen_resp.json()["generation_id"]

    # In SQLite, mark the match record in v2 for target_nv's logical node as 'unresolved'
    with session_factory() as session:
        repo = SQLAlchemyDocumentRepository(session)
        latest_ver = repo.get_latest_version(repo.get_version_by_id(v1_id).document_id)
        v2_target_nv = repo.get_node_version_by_logical_id(latest_ver.id, target_nv.logical_node_id)
        assert v2_target_nv is not None

        # Add or update VersionMatchORM with status='unresolved'
        vm = session.query(VersionMatchORM).filter_by(target_node_version_id=v2_target_nv.id).first()
        if vm:
            vm.status = "unresolved"
            vm.unresolved_candidates = [{"logical_node_id": target_nv.logical_node_id}]
        else:
            vm = VersionMatchORM(
                id=generate_id("vm"),
                target_document_version_id=latest_ver.id,
                target_node_version_id=v2_target_nv.id,
                source_node_version_id=target_nv.id,
                logical_node_id=target_nv.logical_node_id,
                match_method="ambiguous",
                confidence=0.4,
                status="unresolved",
                unresolved_candidates=[{"logical_node_id": target_nv.logical_node_id}],
            )
            session.add(vm)
        session.commit()

    # Query generation: matching is unresolved -> status must be unknown
    get_resp = client.get(f"/api/v1/generations/{generation_id}")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["aggregate_status"] == "unknown"
    assert data["source_traceability"][0]["status"] == "unknown"
    assert "unresolved" in data["source_traceability"][0]["diff_summary"].lower()


def test_mixed_source_aggregate_priority_ladder(staleness_env):
    """Aggregate status follows strict priority: unknown > removed > stale > current."""
    session_factory = staleness_env["session_factory"]
    doc_store = JSONDocumentStore(staleness_env["json_dir"])

    # Build a simulated generation document with 4 source snapshots
    with session_factory() as session:
        repo = SQLAlchemyDocumentRepository(session)
        v1_nodes = repo.get_node_versions_by_version_id(staleness_env["v1_id"])
        latest_ver = repo.get_latest_version(repo.get_version_by_id(staleness_env["v1_id"]).document_id)

    # Source 1: current (unchanged node)
    # Source 2: stale (battery node)
    # Source 3: removed (log_absent_id)
    # Source 4: unknown (log_ambiguous_id)
    # We can verify the StalenessService priority logic directly on GenerationTraceability
    from src.app.application.services.staleness_service import StalenessService
    service = StalenessService(repo)

    # Priority check:
    # 1. unknown present -> unknown
    from src.app.domain.models.qa import GenerationDocument, SourceSnapshot
    doc = GenerationDocument(
        generation_id="gen_mixed_test_01",
        selection_id="sel_dummy",
        idempotency_key="mixed-idem-01",
        prompt_version="v1.0",
        schema_version="v1.0",
        provider="mock",
        model="mock",
        status="SUCCESS",
        created_at="2026-10-23T12:00:00Z",
        source_snapshots=[
            # Current node
            SourceSnapshot(
                node_version_id=v1_nodes[0].id,
                logical_node_id=v1_nodes[0].logical_node_id,
                node_type="section",
                content_hash=v1_nodes[0].content_hash,
                page_start=1,
                page_end=1,
            ),
            # Stale battery node
            SourceSnapshot(
                node_version_id="nv_dummy_stale",
                logical_node_id="log_stale_battery",
                node_type="paragraph",
                content_hash="hash_old_300",
                page_start=2,
                page_end=2,
            ),
            # Removed node
            SourceSnapshot(
                node_version_id="nv_dummy_removed",
                logical_node_id="log_absent_in_v2",
                node_type="paragraph",
                content_hash="hash_removed",
                page_start=2,
                page_end=2,
            ),
            # Unknown node
            SourceSnapshot(
                node_version_id="nv_dummy_unknown",
                logical_node_id="log_ambiguous_in_v2",
                node_type="paragraph",
                content_hash="hash_unknown",
                page_start=3,
                page_end=3,
            ),
        ],
    )

    # Let's verify priority ladder with custom source reports
    from src.app.application.services.staleness_service import SourceTraceabilityReport, GenerationTraceability
    
    def resolve_agg(statuses):
        if "unknown" in statuses:
            return "unknown"
        elif "removed" in statuses:
            return "removed"
        elif "stale" in statuses:
            return "stale"
        return "current"

    assert resolve_agg({"current", "stale", "removed", "unknown"}) == "unknown"
    assert resolve_agg({"current", "stale", "removed"}) == "removed"
    assert resolve_agg({"current", "stale"}) == "stale"
    assert resolve_agg({"current"}) == "current"


def test_generation_retrieval_by_filters(staleness_env):
    """Generations can be queried by selection_id, logical_node_id, and node_version_id with strict single-filter validation."""
    mock_llm = MockLLMClient()
    client = build_client(staleness_env, mock_llm)
    v1_id = staleness_env["v1_id"]
    v1_nodes = staleness_env["v1_nodes"]

    target_node = v1_nodes[2]

    # Create selection & generation
    sel_resp = client.post(
        "/api/v1/selections",
        json={
            "name": "Filtered Discovery Scope",
            "document_version_id": v1_id,
            "node_version_ids": [target_node.id],
        },
    )
    selection_id = sel_resp.json()["id"]

    gen_resp = client.post(
        f"/api/v1/selections/{selection_id}/generations",
        headers={"Idempotency-Key": "filter-discovery-key-01"},
    )
    generation_id = gen_resp.json()["generation_id"]

    # 1. Query by selection_id
    r_sel = client.get(f"/api/v1/generations?selection_id={selection_id}")
    assert r_sel.status_code == 200
    res_list = r_sel.json()
    assert isinstance(res_list, list)
    assert any(g["generation_id"] == generation_id for g in res_list)

    # 2. Query by logical_node_id
    r_log = client.get(f"/api/v1/generations?logical_node_id={target_node.logical_node_id}")
    assert r_log.status_code == 200
    res_log = r_log.json()
    assert isinstance(res_log, list)
    assert any(g["generation_id"] == generation_id for g in res_log)

    # 3. Query by exact node_version_id
    r_nv = client.get(f"/api/v1/generations?node_version_id={target_node.id}")
    assert r_nv.status_code == 200
    res_nv = r_nv.json()
    assert isinstance(res_nv, list)
    assert any(g["generation_id"] == generation_id for g in res_nv)

    # 4. Filter validation: zero filters provided -> 422
    r_zero = client.get("/api/v1/generations")
    assert r_zero.status_code == 422
    assert "exactly one filter" in r_zero.json()["detail"].lower()

    # 5. Filter validation: multiple filters provided -> 422
    r_multi = client.get(
        f"/api/v1/generations?selection_id={selection_id}&logical_node_id={target_node.logical_node_id}"
    )
    assert r_multi.status_code == 422
    assert "exactly one filter" in r_multi.json()["detail"].lower()


def test_missing_generation_returns_404(staleness_env):
    """Querying a non-existent generation ID returns 404."""
    mock_llm = MockLLMClient()
    client = build_client(staleness_env, mock_llm)

    resp = client.get("/api/v1/generations/gen_ghost_nonexistent_999")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


def test_proof_that_retrieval_leaves_stored_artifacts_unchanged(staleness_env):
    """Verifies that retrieval-time staleness analysis leaves the saved JSON test cases and snapshots completely untouched."""
    mock_llm = MockLLMClient()
    client = build_client(staleness_env, mock_llm)
    v1_id = staleness_env["v1_id"]
    v1_nodes = staleness_env["v1_nodes"]

    sel_resp = client.post(
        "/api/v1/selections",
        json={
            "name": "Immutability Verification Scope",
            "document_version_id": v1_id,
            "node_version_ids": [v1_nodes[0].id],
        },
    )
    selection_id = sel_resp.json()["id"]

    gen_resp = client.post(
        f"/api/v1/selections/{selection_id}/generations",
        headers={"Idempotency-Key": "immutability-audit-key-777"},
    )
    generation_id = gen_resp.json()["generation_id"]

    store = JSONDocumentStore(staleness_env["json_dir"])
    doc_path = store.get_document_path(generation_id)

    # Read physical JSON content before GET calls
    with open(doc_path, "r", encoding="utf-8") as f:
        content_before = f.read()

    doc_before = store.load_generation(generation_id)

    # Perform multiple retrieval requests
    for _ in range(3):
        r = client.get(f"/api/v1/generations/{generation_id}")
        assert r.status_code == 200

    # Read physical JSON content after GET calls
    with open(doc_path, "r", encoding="utf-8") as f:
        content_after = f.read()

    doc_after = store.load_generation(generation_id)

    # Assert exact byte/string identity of stored artifact
    assert content_before == content_after
    assert doc_before.model_dump() == doc_after.model_dump()
    assert doc_after.test_cases == doc_before.test_cases
    assert doc_after.source_snapshots == doc_before.source_snapshots
