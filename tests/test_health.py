"""Tests for the health check endpoint."""

from starlette.testclient import TestClient


def test_health_check_returns_200_and_expected_payload(client: TestClient):
    """Test that GET /health returns 200 and matches the expected schema."""
    response = client.get("/health")
    assert response.status_code == 200

    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["app_name"] == "Regulatory Manual QA Backend (Test)"
    assert payload["app_version"] == "0.1.0-test"
    assert payload["app_env"] == "test"
