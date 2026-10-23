"""Health check endpoint route handler."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from src.app.config import Settings, get_settings

router = APIRouter(tags=["Health"])


class HealthResponse(BaseModel):
    """Schema for health response."""
    status: str
    app_name: str
    app_version: str
    app_env: str


@router.get("/health", response_model=HealthResponse)
async def health_check(settings: Settings = Depends(get_settings)) -> HealthResponse:
    """Return service health status and basic runtime configuration."""
    return HealthResponse(
        status="ok",
        app_name=settings.app_name,
        app_version=settings.app_version,
        app_env=settings.app_env,
    )
