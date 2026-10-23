"""Use case for retrieving generations with retrieval-time staleness and impact assessment."""

from typing import List, Optional
from src.app.api.schemas.qa import GenerationResponse
from src.app.application.interfaces.document_repository import IDocumentRepository
from src.app.application.services.staleness_service import StalenessService
from src.app.domain.exceptions import GenerationNotFoundError, InvalidQueryError
from src.app.domain.models.qa import GenerationDocument
from src.app.infrastructure.storage.json_store import JSONDocumentStore


class GetGenerationUseCase:
    """Use case retrieving QA generations with dynamic retrieval-time staleness analysis."""

    def __init__(
        self,
        repository: IDocumentRepository,
        json_store: JSONDocumentStore,
    ) -> None:
        self.repository = repository
        self.json_store = json_store
        self.staleness_service = StalenessService(repository)

    def execute(self, generation_id: str) -> GenerationResponse:
        """Fetch generation document and dynamically assess staleness against latest manual."""
        clean_id = (generation_id or "").strip()
        index_record = self.repository.get_generation_index(clean_id)
        if index_record is None:
            raise GenerationNotFoundError(f"Generation '{generation_id}' not found.")

        doc = self.json_store.load_from_path(index_record.json_path)
        if doc is None:
            doc = self.json_store.load_generation(clean_id)

        if doc is None:
            raise GenerationNotFoundError(
                f"Generation document file for '{generation_id}' not found on storage."
            )

        # Dynamic retrieval-time staleness analysis (saved document is NOT modified)
        traceability = self.staleness_service.analyze(doc)

        return GenerationResponse(
            **doc.model_dump(),
            aggregate_status=traceability.aggregate_status,
            latest_document_version_id=traceability.latest_document_version_id,
            latest_version_label=traceability.latest_version_label,
            source_traceability=traceability.sources,
            traceability=traceability,
        )

    def find_generations(
        self,
        selection_id: Optional[str] = None,
        logical_node_id: Optional[str] = None,
        node_version_id: Optional[str] = None,
    ) -> List[GenerationResponse]:
        """Query generations matching exactly one filter: selection_id, logical_node_id, or node_version_id."""
        clean_sel = (selection_id or "").strip()
        clean_log = (logical_node_id or "").strip()
        clean_nv = (node_version_id or "").strip()

        provided = [
            name
            for name, val in [
                ("selection_id", clean_sel),
                ("logical_node_id", clean_log),
                ("node_version_id", clean_nv),
            ]
            if val
        ]

        if len(provided) != 1:
            raise InvalidQueryError(
                f"Exactly one filter must be provided: 'selection_id', 'logical_node_id', or 'node_version_id'. "
                f"Provided {len(provided)} filter(s): {provided or 'none'}."
            )

        if clean_sel:
            index_records = self.repository.get_generations_by_selection_id(clean_sel)
        elif clean_log:
            index_records = self.repository.get_generations_by_logical_node_id(clean_log)
        else:
            index_records = self.repository.get_generations_by_node_version_id(clean_nv)

        results: List[GenerationResponse] = []
        for rec in index_records:
            doc = self.json_store.load_from_path(rec.json_path)
            if doc is None:
                doc = self.json_store.load_generation(rec.id)
            if doc is None:
                continue

            traceability = self.staleness_service.analyze(doc)
            results.append(
                GenerationResponse(
                    **doc.model_dump(),
                    aggregate_status=traceability.aggregate_status,
                    latest_document_version_id=traceability.latest_document_version_id,
                    latest_version_label=traceability.latest_version_label,
                    source_traceability=traceability.sources,
                    traceability=traceability,
                )
            )

        return results
