"""Integration tests for version-pinned selections and immutable lineage retrieval."""

import pytest
from starlette.testclient import TestClient
from src.app.api.dependencies import get_db
from src.app.application.use_cases import IngestDocumentUseCase
from src.app.config import Settings
from src.app.factory import create_app
from src.app.infrastructure.db import get_engine, get_session_factory, init_db
from src.app.infrastructure.db.repositories import SQLAlchemyDocumentRepository

V1_PDF = "data/ct200_manual.pdf"
V2_PDF = "data/ct200_manual_v2.pdf"


@pytest.fixture(scope="module")
def selections_test_context():
    """Setup an in-memory SQLite database pre-populated with v1 CT-200 and another document."""
    engine = get_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = get_session_factory(engine)

    with session_factory() as session:
        repo = SQLAlchemyDocumentRepository(session)
        ingest = IngestDocumentUseCase(repo)
        res_v1 = ingest.execute(V1_PDF, "ct200", "CT-200 Sphygmomanometer Manual", "v1")
        # Ingest another document family to test cross-document isolation
        res_other = ingest.execute(V1_PDF, "other_device", "Other Device Manual", "v1")

        v1_id = res_v1.version.id
        other_v1_id = res_other.version.id

        # Grab a couple of persistent v1 node IDs
        v1_nodes = repo.get_node_versions_by_version_id(v1_id)
        other_nodes = repo.get_node_versions_by_version_id(other_v1_id)

    settings = Settings(
        app_name="Regulatory Manual QA Backend (Test)",
        app_env="test",
        database_url="sqlite:///:memory:",
    )
    app = create_app(settings)

    def override_get_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        yield {
            "client": client,
            "session_factory": session_factory,
            "v1_id": v1_id,
            "v1_nodes": v1_nodes,
            "other_v1_id": other_v1_id,
            "other_nodes": other_nodes,
        }

    app.dependency_overrides.clear()


def test_create_and_retrieve_valid_selection(selections_test_context):
    """Test creating a valid version-pinned selection and retrieving its ordered nodes."""
    client: TestClient = selections_test_context["client"]
    v1_id = selections_test_context["v1_id"]
    v1_nodes = selections_test_context["v1_nodes"]

    # Select 3 specific nodes in a particular order (e.g. index 2, 0, 1)
    target_ids = [v1_nodes[2].id, v1_nodes[0].id, v1_nodes[1].id]
    payload = {
        "name": "General Verification Scope",
        "document_version_id": v1_id,
        "node_version_ids": target_ids,
    }

    # 1. Create selection
    resp_create = client.post("/api/v1/selections", json=payload)
    assert resp_create.status_code == 201
    created = resp_create.json()
    assert created["id"].startswith("sel_")
    assert created["name"] == "General Verification Scope"
    assert created["document_version_id"] == v1_id
    assert created["version_label"] == "v1"
    assert created["document_slug"] == "ct200"
    assert created["total_nodes"] == 3

    # Check submitted order preservation
    retrieved_ids = [n["node_version_id"] for n in created["nodes"]]
    assert retrieved_ids == target_ids
    assert [n["position"] for n in created["nodes"]] == [0, 1, 2]

    selection_id = created["id"]

    # 2. Retrieve selection
    resp_get = client.get(f"/api/v1/selections/{selection_id}")
    assert resp_get.status_code == 200
    retrieved = resp_get.json()
    assert retrieved["id"] == selection_id
    assert retrieved["name"] == "General Verification Scope"
    assert len(retrieved["nodes"]) == 3

    for item, expected_nv in zip(retrieved["nodes"], [v1_nodes[2], v1_nodes[0], v1_nodes[1]]):
        assert item["node_version_id"] == expected_nv.id
        assert item["logical_node_id"] == expected_nv.logical_node_id
        assert item["content_hash"] == expected_nv.content_hash
        assert item["text"] == expected_nv.text
        assert item["page_start"] == expected_nv.page_start


def test_reject_missing_nodes(selections_test_context):
    """Creating a selection with non-existent node IDs returns a 422 error."""
    client: TestClient = selections_test_context["client"]
    v1_id = selections_test_context["v1_id"]
    v1_nodes = selections_test_context["v1_nodes"]

    payload = {
        "name": "Invalid Nodes Scope",
        "document_version_id": v1_id,
        "node_version_ids": [v1_nodes[0].id, "nv_nonexistent_xyz999"],
    }
    resp = client.post("/api/v1/selections", json=payload)
    assert resp.status_code == 422
    assert "does not exist" in resp.json()["detail"]


def test_reject_duplicate_node_ids(selections_test_context):
    """Creating a selection with duplicate node IDs returns a 422 error."""
    client: TestClient = selections_test_context["client"]
    v1_id = selections_test_context["v1_id"]
    v1_nodes = selections_test_context["v1_nodes"]

    payload = {
        "name": "Duplicate Nodes Scope",
        "document_version_id": v1_id,
        "node_version_ids": [v1_nodes[0].id, v1_nodes[1].id, v1_nodes[0].id],
    }
    resp = client.post("/api/v1/selections", json=payload)
    assert resp.status_code == 422
    assert "duplicate" in resp.json()["detail"].lower()


def test_reject_cross_document_nodes(selections_test_context):
    """Creating a selection with nodes from another document returns a 422 error."""
    client: TestClient = selections_test_context["client"]
    v1_id = selections_test_context["v1_id"]
    v1_nodes = selections_test_context["v1_nodes"]
    other_nodes = selections_test_context["other_nodes"]

    payload = {
        "name": "Cross Document Scope",
        "document_version_id": v1_id,
        "node_version_ids": [v1_nodes[0].id, other_nodes[0].id],
    }
    resp = client.post("/api/v1/selections", json=payload)
    assert resp.status_code == 422
    assert "rejected" in resp.json()["detail"].lower() or "not belong" in resp.json()["detail"].lower()


def test_selection_name_as_label_permits_duplicates(selections_test_context):
    """Selections may share the same label name and receive unique persistent IDs."""
    client: TestClient = selections_test_context["client"]
    v1_id = selections_test_context["v1_id"]
    v1_nodes = selections_test_context["v1_nodes"]

    payload = {
        "name": "Regression Test Suite",
        "document_version_id": v1_id,
        "node_version_ids": [v1_nodes[0].id],
    }
    r1 = client.post("/api/v1/selections", json=payload)
    assert r1.status_code == 201

    r2 = client.post("/api/v1/selections", json=payload)
    assert r2.status_code == 201

    assert r1.json()["id"] != r2.json()["id"]
    assert r1.json()["name"] == r2.json()["name"]


def test_reject_missing_selection_id(selections_test_context):
    """Querying a non-existent selection ID returns a 404 error."""
    client: TestClient = selections_test_context["client"]
    resp = client.get("/api/v1/selections/sel_nonexistent999")
    assert resp.status_code == 404


def test_stable_retrieval_and_immutability_after_v2_ingestion(selections_test_context):
    """Verify that a selection created against v1 returns the exact v1 text and content hash even after v2 is ingested."""
    client: TestClient = selections_test_context["client"]
    session_factory = selections_test_context["session_factory"]
    v1_id = selections_test_context["v1_id"]

    # 1. Identify specific nodes in v1 that will change in v2:
    # - Section 2.1.1.1 paragraph: 300 cycles in v1 -> 250 cycles in v2
    # - Section 3.2 paragraph: 40 mmHg in v1 -> 30 mmHg in v2
    with session_factory() as session:
        repo = SQLAlchemyDocumentRepository(session)
        v1_nodes = repo.get_node_versions_by_version_id(v1_id)
        battery_para = [
            n for n in v1_nodes
            if n.node_type == "paragraph" and "300 measurement cycles" in n.text
        ][0]
        inflation_para = [
            n for n in v1_nodes
            if n.node_type == "paragraph" and "40 mmHg increments" in n.text
        ][0]

    # 2. Create version-pinned selection against v1
    create_payload = {
        "name": "Firmware 1.3 Critical Thresholds",
        "document_version_id": v1_id,
        "node_version_ids": [battery_para.id, inflation_para.id],
    }
    create_resp = client.post("/api/v1/selections", json=create_payload)
    assert create_resp.status_code == 201
    selection_id = create_resp.json()["id"]

    # Record v1 baseline snapshot values
    baseline_battery_hash = battery_para.content_hash
    baseline_battery_text = battery_para.text
    baseline_inflation_hash = inflation_para.content_hash
    baseline_inflation_text = inflation_para.text

    # 3. Now ingest v2 (where battery is 250 cycles and inflation is 30 mmHg)
    with session_factory() as session:
        repo = SQLAlchemyDocumentRepository(session)
        ingest = IngestDocumentUseCase(repo)
        res_v2 = ingest.execute(V2_PDF, "ct200", "CT-200 Sphygmomanometer Manual", "v2")
        v2_id = res_v2.version.id

    # Verify that in v2, the updated nodes have different hashes and text
    with session_factory() as session:
        repo = SQLAlchemyDocumentRepository(session)
        v2_nodes = repo.get_node_versions_by_version_id(v2_id)
        v2_battery = [n for n in v2_nodes if n.logical_node_id == battery_para.logical_node_id][0]
        assert "250 measurement cycles" in v2_battery.text
        assert v2_battery.content_hash != baseline_battery_hash

    # 4. Now test mixed-version rejection: try to create a selection mixing v1 and v2 nodes
    mixed_payload = {
        "name": "Mixed Version Invalid Scope",
        "document_version_id": v1_id,
        "node_version_ids": [battery_para.id, v2_battery.id],  # battery_para is v1, v2_battery is v2!
    }
    resp_mixed = client.post("/api/v1/selections", json=mixed_payload)
    assert resp_mixed.status_code == 422
    assert "belongs to version" in resp_mixed.json()["detail"].lower()

    # 5. Retrieve the v1 selection created in step 2:
    # Must STILL return the exact v1 text and content hash!
    retrieved_resp = client.get(f"/api/v1/selections/{selection_id}")
    assert retrieved_resp.status_code == 200
    sel_data = retrieved_resp.json()
    assert sel_data["document_version_id"] == v1_id
    assert sel_data["version_label"] == "v1"

    nodes = sel_data["nodes"]
    assert len(nodes) == 2

    # Check node 0: Battery Life
    assert nodes[0]["node_version_id"] == battery_para.id
    assert nodes[0]["content_hash"] == baseline_battery_hash
    assert nodes[0]["text"] == baseline_battery_text
    assert "300 measurement cycles" in nodes[0]["text"]
    assert "250 measurement cycles" not in nodes[0]["text"]

    # Check node 1: Inflation
    assert nodes[1]["node_version_id"] == inflation_para.id
    assert nodes[1]["content_hash"] == baseline_inflation_hash
    assert nodes[1]["text"] == baseline_inflation_text
    assert "40 mmHg increments" in nodes[1]["text"]
    assert "30 mmHg increments" not in nodes[1]["text"]
