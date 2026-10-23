"""Pytest fixtures for backend tests."""

import pytest
from starlette.testclient import TestClient
from src.app.config import Settings, get_settings
from src.app.factory import create_app


@pytest.fixture
def test_settings() -> Settings:
    """Provide isolated settings for testing."""
    return Settings(
        app_name="Regulatory Manual QA Backend (Test)",
        app_env="test",
        app_version="0.1.0-test",
        debug=True,
        database_url="sqlite:///:memory:",
        json_store_path="./data/test_generations",
        llm_provider="mock",
    )


@pytest.fixture
def app(test_settings: Settings):
    """Provide a FastAPI application configured for testing."""
    app_instance = create_app(test_settings)
    app_instance.dependency_overrides[get_settings] = lambda: test_settings
    yield app_instance
    app_instance.dependency_overrides.clear()


@pytest.fixture
def client(app) -> TestClient:
    """Provide a synchronous TestClient for testing endpoints."""
    with TestClient(app) as test_client:
        yield test_client
