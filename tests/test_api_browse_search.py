"""API integration tests for versioned browsing, search, and diff endpoints."""

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
def api_test_context():
    """Setup an in-memory SQLite database pre-populated with v1 and v2 CT-200 manuals."""
    engine = get_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = get_session_factory(engine)

    with session_factory() as session:
        repo = SQLAlchemyDocumentRepository(session)
        ingest = IngestDocumentUseCase(repo)
        res_v1 = ingest.execute(V1_PDF, "ct200", "CT-200 Sphygmomanometer Manual", "v1")
        res_v2 = ingest.execute(V2_PDF, "ct200", "CT-200 Sphygmomanometer Manual", "v2")

        # Also ingest another document family to test cross-document isolation
        ingest.execute(V1_PDF, "other_device", "Other Device Manual", "v1")

        v1_id = res_v1.version.id
        v2_id = res_v2.version.id

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
            "v1_id": v1_id,
            "v2_id": v2_id,
        }

    app.dependency_overrides.clear()


def test_list_document_versions(api_test_context):
    client: TestClient = api_test_context["client"]

    # Success case
    resp = client.get("/api/v1/documents/ct200/versions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["document_slug"] == "ct200"
    assert len(data["versions"]) == 2
    labels = [v["version_label"] for v in data["versions"]]
    assert "v1" in labels
    assert "v2" in labels

    # Missing document 404
    resp_404 = client.get("/api/v1/documents/nonexistent_device/versions")
    assert resp_404.status_code == 404
    assert "not found" in resp_404.json()["detail"].lower()


def test_browse_sections_latest_default_and_explicit_version(api_test_context):
    client: TestClient = api_test_context["client"]
    v1_id = api_test_context["v1_id"]
    v2_id = api_test_context["v2_id"]

    # 1. Default to latest version (v2)
    resp_default = client.get("/api/v1/documents/ct200/sections")
    assert resp_default.status_code == 200
    data_latest = resp_default.json()
    assert data_latest["is_latest"] is True
    assert data_latest["version_id"] == v2_id
    assert data_latest["total_sections"] == 8

    sec_numbers = [s["section_number"] for s in data_latest["sections"]]
    assert sec_numbers == ["1", "2", "3", "4", "5", "6", "7", "8"]


    # 2. Explicit older version (v1)
    resp_v1 = client.get(f"/api/v1/documents/ct200/sections?version_id={v1_id}")
    assert resp_v1.status_code == 200
    data_v1 = resp_v1.json()
    assert data_v1["is_latest"] is False
    assert data_v1["version_id"] == v1_id
    assert data_v1["version_label"] == "v1"

    # 3. Non-existent version 404
    resp_bad_ver = client.get("/api/v1/documents/ct200/sections?version_id=ver_invalid999")
    assert resp_bad_ver.status_code == 404


def test_get_node_detail_with_children(api_test_context):
    client: TestClient = api_test_context["client"]

    # First get top sections to find section 1 node ID
    sections_resp = client.get("/api/v1/documents/ct200/sections")
    sec_1_id = sections_resp.json()["sections"][0]["id"]

    # Retrieve Section 1 detail
    resp = client.get(f"/api/v1/documents/ct200/nodes/{sec_1_id}")
    assert resp.status_code == 200
    node = resp.json()
    assert node["id"] == sec_1_id
    assert node["document_slug"] == "ct200"
    assert node["node_type"] == "section"
    assert node["section_number"] == "1"
    assert "Device Overview" in node["heading"]
    assert len(node["content_hash"]) == 64
    assert len(node["children"]) > 0

    # Ensure children have persistent IDs, types, and previews
    child_types = [c["node_type"] for c in node["children"]]
    assert "section" in child_types or "paragraph" in child_types

    # Missing node ID 404
    resp_missing = client.get("/api/v1/documents/ct200/nodes/nv_doesnotexist")
    assert resp_missing.status_code == 404

    # Cross-document mismatch 404
    resp_mismatch = client.get(f"/api/v1/documents/other_device/nodes/{sec_1_id}")
    assert resp_mismatch.status_code == 404
    assert "does not belong" in resp_mismatch.json()["detail"]


def test_search_nodes_headings_and_text(api_test_context):
    client: TestClient = api_test_context["client"]
    v1_id = api_test_context["v1_id"]
    v2_id = api_test_context["v2_id"]

    # 1. Search by heading
    resp_heading = client.get("/api/v1/documents/ct200/search?query=Battery")
    assert resp_heading.status_code == 200
    res_data = resp_heading.json()
    assert res_data["total_results"] > 0
    match_fields = [r["match_field"] for r in res_data["results"]]
    assert "heading" in match_fields

    # 2. Search by body text
    resp_text = client.get("/api/v1/documents/ct200/search?query=systolic")
    assert resp_text.status_code == 200
    data_text = resp_text.json()
    assert data_text["total_results"] > 0
    assert any("systolic" in r["snippet"].lower() for r in data_text["results"])

    # 3. Version scoping: 'Data Export' exists in v2, but not v1
    resp_v1_search = client.get(f"/api/v1/documents/ct200/search?query=Data%20Export&version_id={v1_id}")
    assert resp_v1_search.json()["total_results"] == 0

    resp_v2_search = client.get(f"/api/v1/documents/ct200/search?query=Data%20Export&version_id={v2_id}")
    assert resp_v2_search.json()["total_results"] >= 1
    assert any("Data Export" in r["heading"] for r in resp_v2_search.json()["results"] if r["heading"])

    # 4. Result limit
    resp_limited = client.get("/api/v1/documents/ct200/search?query=the&limit=2")
    assert resp_limited.status_code == 200
    assert len(resp_limited.json()["results"]) <= 2

    # 5. Empty search results
    resp_empty = client.get("/api/v1/documents/ct200/search?query=xyznonexistentterm999")
    assert resp_empty.status_code == 200
    assert resp_empty.json()["total_results"] == 0
    assert resp_empty.json()["results"] == []

    # 6. Invalid query validation (422)
    resp_invalid_limit = client.get("/api/v1/documents/ct200/search?query=test&limit=0")
    assert resp_invalid_limit.status_code == 422

    resp_invalid_type = client.get("/api/v1/documents/ct200/search?query=test&node_type=invalid_type_xyz")
    assert resp_invalid_type.status_code == 422


def test_compare_single_node_across_versions(api_test_context):
    client: TestClient = api_test_context["client"]
    v1_id = api_test_context["v1_id"]
    v2_id = api_test_context["v2_id"]

    # Search for battery section to get logical_node_id
    search_resp = client.get("/api/v1/documents/ct200/search?query=Battery%20Life")
    battery_node = [
        r for r in search_resp.json()["results"]
        if r["section_number"] == "2.1.1.1" and r["node_type"] == "section"
    ][0]
    log_id = battery_node["logical_node_id"]

    # 1. Compare battery section (unchanged heading/sec)
    resp_diff = client.get(
        f"/api/v1/documents/ct200/diff/node?v1_version_id={v1_id}&v2_version_id={v2_id}&logical_node_id={log_id}"
    )
    assert resp_diff.status_code == 200
    diff_data = resp_diff.json()
    assert diff_data["logical_node_id"] == log_id
    assert diff_data["status"] == "unchanged"

    # 2. Compare using direct node path ID
    node_diff_resp = client.get(
        f"/api/v1/documents/ct200/nodes/{battery_node['node_version_id']}/diff?target_version_id={v1_id}"
    )
    assert node_diff_resp.status_code == 200

    # 3. Invalid parameters: same version diff -> 422
    resp_same_ver = client.get(
        f"/api/v1/documents/ct200/diff/node?v1_version_id={v1_id}&v2_version_id={v1_id}&logical_node_id={log_id}"
    )
    assert resp_same_ver.status_code == 422

    # 4. Missing logical and node version IDs -> 422
    resp_missing_id = client.get(
        f"/api/v1/documents/ct200/diff/node?v1_version_id={v1_id}&v2_version_id={v2_id}"
    )
    assert resp_missing_id.status_code == 422


def test_compare_complete_document_versions(api_test_context):
    client: TestClient = api_test_context["client"]
    v1_id = api_test_context["v1_id"]
    v2_id = api_test_context["v2_id"]

    # Success comparison
    resp = client.get(f"/api/v1/documents/ct200/diff?v1_version_id={v1_id}&v2_version_id={v2_id}")
    assert resp.status_code == 200
    report = resp.json()
    assert report["document_slug"] == "ct200"
    assert report["v1_version_id"] == v1_id
    assert report["v2_version_id"] == v2_id

    counts = report["summary_counts"]
    assert counts["unchanged"] > 0
    assert counts["changed"] > 0
    assert counts["added"] >= 1
    assert counts["removed"] >= 1

    # Verify that Section 5.3 is reported as added
    added_nodes = [d for d in report["node_diffs"] if d["status"] == "added"]
    assert any(d["section_number"] == "5.3" for d in added_nodes)

    # Identical versions -> 422
    resp_same = client.get(f"/api/v1/documents/ct200/diff?v1_version_id={v1_id}&v2_version_id={v1_id}")
    assert resp_same.status_code == 422

    # Non-existent version -> 404
    resp_404 = client.get(f"/api/v1/documents/ct200/diff?v1_version_id={v1_id}&v2_version_id=ver_fake999")
    assert resp_404.status_code == 404
