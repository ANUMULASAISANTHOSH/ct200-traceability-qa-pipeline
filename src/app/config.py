"""Application configuration using Pydantic Settings."""

from functools import lru_cache
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    """Central configuration for the backend application."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Core App
    app_name: str = Field(default="Regulatory Manual QA Backend", description="Application display name")
    app_env: str = Field(default="development", description="Environment: development, test, production")
    app_version: str = Field(default="0.1.0", description="Backend service version")
    debug: bool = Field(default=False, description="Debug mode flag")
    host: str = Field(default="0.0.0.0", description="Server host binding")
    port: int = Field(default=8000, description="Server port")

    # Relational SQLite Database
    database_url: str = Field(
        default="sqlite:///./data/app.db",
        description="SQLAlchemy database URL for metadata and node registry",
    )

    # Append-Only JSON Document Store
    json_store_path: str = Field(
        default="./data/generations",
        description="Filesystem path for append-only JSON generations store",
    )

    # LLM Settings
    llm_provider: str = Field(default="mock", description="LLM provider: mock, openai, anthropic")
    llm_api_key: Optional[str] = Field(default=None, description="API Key for LLM provider")
    llm_model: str = Field(default="gpt-4o", description="Target model name")


@lru_cache()
def get_settings() -> Settings:
    """Return a cached instance of application settings."""
    return Settings()
