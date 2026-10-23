"""FastAPI dependency providers for database sessions and application use cases."""

from functools import lru_cache
from typing import Generator
from fastapi import Depends
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from src.app.application.use_cases.browse_document import BrowseDocumentUseCase
from src.app.application.use_cases.diff_versions import DiffVersionsUseCase
from src.app.application.use_cases.search_nodes import SearchNodesUseCase
from src.app.config import Settings, get_settings
from src.app.infrastructure.db.repositories.document_repository import (
    SQLAlchemyDocumentRepository,
)
from src.app.infrastructure.db.session import get_engine, get_session_factory, init_db


@lru_cache()
def _get_engine_and_factory(database_url: str) -> tuple[Engine, sessionmaker[Session]]:
    engine = get_engine(database_url)
    init_db(engine)
    factory = get_session_factory(engine)
    return engine, factory


def get_db(settings: Settings = Depends(get_settings)) -> Generator[Session, None, None]:
    """Provide a scoped SQLAlchemy database session."""
    _, factory = _get_engine_and_factory(settings.database_url)
    session = factory()
    try:
        yield session
    finally:
        session.close()


def get_repository(
    session: Session = Depends(get_db),
) -> SQLAlchemyDocumentRepository:
    """Provide a document repository adapter."""
    return SQLAlchemyDocumentRepository(session)


def get_browse_use_case(
    repo: SQLAlchemyDocumentRepository = Depends(get_repository),
) -> BrowseDocumentUseCase:
    """Provide the browse document use case."""
    return BrowseDocumentUseCase(repo)


def get_search_use_case(
    repo: SQLAlchemyDocumentRepository = Depends(get_repository),
) -> SearchNodesUseCase:
    """Provide the search nodes use case."""
    return SearchNodesUseCase(repo)


def get_diff_use_case(
    repo: SQLAlchemyDocumentRepository = Depends(get_repository),
) -> DiffVersionsUseCase:
    """Provide the cross-version diff use case."""
    return DiffVersionsUseCase(repo)
