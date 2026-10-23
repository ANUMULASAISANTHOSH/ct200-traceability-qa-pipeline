"""Application factory module for FastAPI."""

from typing import Optional
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.app.config import Settings, get_settings
from src.app.api.router import api_router


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    """Create and configure a new FastAPI application instance."""
    app_settings = settings or get_settings()

    app = FastAPI(
        title=app_settings.app_name,
        version=app_settings.app_version,
        docs_url="/docs" if app_settings.debug or app_settings.app_env != "production" else None,
        redoc_url="/redoc" if app_settings.debug or app_settings.app_env != "production" else None,
    )

    # Basic CORS support
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include Central API Router
    app.include_router(api_router)

    return app
