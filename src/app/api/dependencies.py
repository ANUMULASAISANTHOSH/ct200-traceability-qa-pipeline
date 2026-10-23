"""FastAPI dependency providers for database sessions and application use cases."""

from functools import lru_cache
from typing import Generator
from fastapi import Depends
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from src.app.application.use_cases.browse_document import BrowseDocumentUseCase
from src.app.application.use_cases.diff_versions import DiffVersionsUseCase
from src.app.application.use_cases.manage_selections import (
    CreateSelectionUseCase,
    GetSelectionUseCase,
)
from src.app.application.use_cases.generate_qa import GenerateQAUseCase
from src.app.application.use_cases.get_generation import GetGenerationUseCase
from src.app.application.use_cases.search_nodes import SearchNodesUseCase

from src.app.config import Settings, get_settings
from src.app.infrastructure.db.repositories.document_repository import (
    SQLAlchemyDocumentRepository,
)
from src.app.infrastructure.db.session import get_engine, get_session_factory, init_db
from src.app.infrastructure.llm.client import LLMClient, get_llm_client
from src.app.infrastructure.storage.json_store import JSONDocumentStore


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


def get_json_document_store(
    settings: Settings = Depends(get_settings),
) -> JSONDocumentStore:
    """Provide the append-only JSON document store."""
    return JSONDocumentStore(base_path=settings.json_store_path)


def get_llm_client_dependency(
    settings: Settings = Depends(get_settings),
) -> LLMClient:
    """Provide configured LLM client instance."""
    return get_llm_client(settings)


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


def get_create_selection_use_case(
    repo: SQLAlchemyDocumentRepository = Depends(get_repository),
) -> CreateSelectionUseCase:
    """Provide the create selection use case."""
    return CreateSelectionUseCase(repo)


def get_get_selection_use_case(
    repo: SQLAlchemyDocumentRepository = Depends(get_repository),
) -> GetSelectionUseCase:
    """Provide the get selection use case."""
    return GetSelectionUseCase(repo)


def get_generate_qa_use_case(
    repo: SQLAlchemyDocumentRepository = Depends(get_repository),
    store: JSONDocumentStore = Depends(get_json_document_store),
    client: LLMClient = Depends(get_llm_client_dependency),
) -> GenerateQAUseCase:
    """Provide the QA generation use case."""
    return GenerateQAUseCase(repository=repo, json_store=store, llm_client=client)


def get_get_generation_use_case(
    repo: SQLAlchemyDocumentRepository = Depends(get_repository),
    store: JSONDocumentStore = Depends(get_json_document_store),
) -> GetGenerationUseCase:
    """Provide the get generation use case."""
    return GetGenerationUseCase(repository=repo, json_store=store)

