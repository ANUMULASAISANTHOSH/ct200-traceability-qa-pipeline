"""Central API router aggregating endpoint modules."""

from fastapi import APIRouter
from src.app.api.routes import health

api_router = APIRouter()
api_router.include_router(health.router)
