"""Central API router aggregating endpoint modules."""

from fastapi import APIRouter
from src.app.api.routes import documents, health, selections

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(documents.router, prefix="/api/v1/documents", tags=["Documents"])
api_router.include_router(documents.router, prefix="/documents", tags=["Documents"])
api_router.include_router(selections.router, prefix="/api/v1/selections", tags=["Selections"])
api_router.include_router(selections.router, prefix="/selections", tags=["Selections"])


