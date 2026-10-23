"""Tests for application settings and configuration loading."""

import os
from src.app.config import Settings


def test_default_settings():
    """Verify default configuration attributes."""
    settings = Settings()
    assert settings.app_name == "Regulatory Manual QA Backend"
    assert settings.app_env == "development"
    assert settings.app_version == "0.1.0"
    assert settings.debug is False
    assert settings.port == 8000
    assert "sqlite" in settings.database_url
    assert settings.json_store_path == "./data/generations"
    assert settings.llm_provider == "mock"


def test_custom_settings_override():
    """Verify that settings can be customized."""
    custom = Settings(
        app_name="Custom Tester",
        app_env="staging",
        debug=True,
        port=9000,
        database_url="sqlite:///./test.db",
        json_store_path="./custom_store",
        llm_provider="openai",
        llm_model="gpt-4o-mini",
    )
    assert custom.app_name == "Custom Tester"
    assert custom.app_env == "staging"
    assert custom.debug is True
    assert custom.port == 9000
    assert custom.database_url == "sqlite:///./test.db"
    assert custom.json_store_path == "./custom_store"
    assert custom.llm_provider == "openai"
    assert custom.llm_model == "gpt-4o-mini"


def test_environment_variable_override(monkeypatch):
    """Verify that environment variables override defaults."""
    monkeypatch.setenv("APP_NAME", "Env Loaded Backend")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEBUG", "true")

    settings = Settings()
    assert settings.app_name == "Env Loaded Backend"
    assert settings.app_env == "production"
    assert settings.debug is True
