"""Repositories exports."""

from src.app.infrastructure.db.repositories.document_repository import (
    SQLAlchemyDocumentRepository,
)

__all__ = ["SQLAlchemyDocumentRepository"]
