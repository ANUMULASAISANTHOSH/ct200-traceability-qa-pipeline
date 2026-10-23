"""Central API router aggregating endpoint modules."""

from fastapi import APIRouter
from src.app.api.routes import documents, health

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(documents.router, prefix="/api/v1/documents", tags=["Documents"])
api_router.include_router(documents.router, prefix="/documents", tags=["Documents"])

