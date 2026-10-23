"""Use case for retrieving a persisted QA generation document."""

from src.app.application.interfaces.document_repository import IDocumentRepository
from src.app.domain.exceptions import GenerationNotFoundError
from src.app.domain.models.qa import GenerationDocument
from src.app.infrastructure.storage.json_store import JSONDocumentStore


class GetGenerationUseCase:
    """Use case retrieving a generation document using the SQLite index and JSON store."""

    def __init__(
        self,
        repository: IDocumentRepository,
        json_store: JSONDocumentStore,
    ) -> None:
        self.repository = repository
        self.json_store = json_store

    def execute(self, generation_id: str) -> GenerationDocument:
        """Fetch generation document by persistent generation ID."""
        clean_id = (generation_id or "").strip()
        index_record = self.repository.get_generation_index(clean_id)
        if index_record is None:
            raise GenerationNotFoundError(f"Generation '{generation_id}' not found.")

        doc = self.json_store.load_from_path(index_record.json_path)
        if doc is None:
            # Fallback to direct resolution
            doc = self.json_store.load_generation(clean_id)

        if doc is None:
            raise GenerationNotFoundError(
                f"Generation document file for '{generation_id}' not found on storage."
            )

        return doc
