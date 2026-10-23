"""Use case for ingesting, parsing, content-hashing, and persisting document versions."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from src.app.application.interfaces.document_repository import IDocumentRepository
from src.app.application.services.matcher import CrossVersionNodeMatcher
from src.app.domain.models.hashing import compute_file_sha256
from src.app.infrastructure.db.models import (
    DocumentORM,
    DocumentVersionORM,
    LogicalNodeORM,
    NodeVersionORM,
    VersionMatchORM,
    generate_id,
)
from src.app.infrastructure.pdf.parser import DocumentParser


@dataclass
class IngestionResult:
    """Outcome of an ingestion attempt."""
    document: DocumentORM
    version: DocumentVersionORM
    is_duplicate: bool
    node_count: int
    warning_count: int


class IngestDocumentUseCase:
    """Use case coordinating PDF parsing, hashing, lineage matching, and persistent storage."""

    def __init__(
        self,
        repository: IDocumentRepository,
        parser: Optional[DocumentParser] = None,
        matcher: Optional[CrossVersionNodeMatcher] = None,
    ) -> None:
        self.repository = repository
        self.parser = parser or DocumentParser()
        self.matcher = matcher or CrossVersionNodeMatcher()

    def execute(
        self,
        pdf_path: Union[str, Path],
        document_slug: str,
        document_title: str,
        version_label: str,
    ) -> IngestionResult:
        """Ingest a PDF file into the versioned repository.
        
        If an identical PDF (by SHA-256 fingerprint) was already ingested for this document,
        returns the existing version without creating duplicate records.
        """
        path = Path(pdf_path)
        if not path.exists():
            raise FileNotFoundError(f"PDF file not found at path: {path}")

        # 1. Compute deterministic SHA-256 fingerprint for source PDF
        file_hash = compute_file_sha256(path)

        # 2. Get or create stable Document manual identity
        document = self.repository.get_or_create_document(
            slug=document_slug,
            title=document_title,
        )

        # 3. Duplicate-file check: return existing version if hash already exists
        existing_version = self.repository.get_version_by_hash(document.id, file_hash)
        if existing_version is not None:
            existing_nvs = self.repository.get_node_versions_by_version_id(existing_version.id)
            return IngestionResult(
                document=document,
                version=existing_version,
                is_duplicate=True,
                node_count=len(existing_nvs),
                warning_count=len(existing_version.warnings or []),
            )

        # 4. Parse PDF through the layout/hierarchy parser
        draft = self.parser.parse(str(path), document_id=document.slug)

        # 5. Retrieve latest previous version (if any) for cross-version lineage matching
        latest_version = self.repository.get_latest_version(document.id)
        prior_nvs: List[NodeVersionORM] = []
        if latest_version is not None:
            prior_nvs = self.repository.get_node_versions_by_version_id(latest_version.id)

        # 6. Perform cross-version matching
        plans = self.matcher.match_draft_against_prior_version(
            prior_node_versions=prior_nvs,
            draft=draft,
            document_id=document.id,
        )

        # 7. Prepare persistent entities
        new_version_id = generate_id("ver")
        serialized_warnings = [
            {
                "code": w.code,
                "message": w.message,
                "page_number": w.page_number,
                "node_id": w.node_id,
            }
            for w in draft.warnings
        ]

        document_version = DocumentVersionORM(
            id=new_version_id,
            document_id=document.id,
            version_label=version_label,
            file_hash=file_hash,
            filename=path.name,
            total_pages=draft.total_pages,
            extraction_method=draft.extraction_method,
            warnings=serialized_warnings,
        )

        logical_nodes_to_create: List[LogicalNodeORM] = []
        seen_created_log_ids = set()
        for plan in plans:
            if plan.is_new_logical_node and plan.logical_node_id not in seen_created_log_ids:
                seen_created_log_ids.add(plan.logical_node_id)
                logical_nodes_to_create.append(
                    LogicalNodeORM(
                        id=plan.logical_node_id,
                        document_id=document.id,
                        node_type=plan.draft_node.node_type.value,
                        canonical_heading=plan.canonical_heading,
                        canonical_path=plan.canonical_path,
                    )
                )

        # Pre-assign persistent IDs for all NodeVersion records to link parent-child hierarchy
        draft_id_to_persistent_nv_id: Dict[str, str] = {
            plan.draft_node.id: generate_id("nv") for plan in plans
        }

        node_versions_to_create: List[NodeVersionORM] = []
        version_matches_to_create: List[VersionMatchORM] = []

        for plan in plans:
            d_node = plan.draft_node
            persistent_nv_id = draft_id_to_persistent_nv_id[d_node.id]
            parent_nv_id = (
                draft_id_to_persistent_nv_id.get(d_node.parent_id)
                if d_node.parent_id
                else None
            )

            structured_dict = None
            if d_node.table_data:
                structured_dict = {
                    "headers": d_node.table_data.headers,
                    "rows": d_node.table_data.rows,
                }
            elif "list_items" in d_node.metadata:
                structured_dict = {"items": d_node.metadata["list_items"]}

            page_nums = d_node.page_location.page_numbers or [d_node.page_location.page_number]
            bbox_list = list(d_node.page_location.bbox) if d_node.page_location.bbox else None

            nv = NodeVersionORM(
                id=persistent_nv_id,
                document_version_id=new_version_id,
                logical_node_id=plan.logical_node_id,
                parent_node_version_id=parent_nv_id,
                node_type=d_node.node_type.value,
                heading=d_node.title,
                section_number=d_node.section_number,
                declared_depth=d_node.depth,
                source_order=d_node.source_order,
                page_start=d_node.page_location.page_number,
                page_end=max(page_nums),
                page_numbers=page_nums,
                bbox=bbox_list,
                text=d_node.content,
                structured_data=structured_dict,
                content_hash=plan.content_hash,
                draft_node_id=d_node.id,
            )
            node_versions_to_create.append(nv)

            vm = VersionMatchORM(
                id=generate_id("vm"),
                target_document_version_id=new_version_id,
                target_node_version_id=persistent_nv_id,
                source_node_version_id=plan.source_node_version_id,
                logical_node_id=plan.logical_node_id,
                match_method=plan.match_method,
                confidence=plan.confidence,
                status=plan.status,
                unresolved_candidates=plan.unresolved_candidates,
                notes=plan.notes,
            )
            version_matches_to_create.append(vm)

        # 8. Atomically persist version snapshot
        saved_version = self.repository.save_ingested_version(
            document_version=document_version,
            logical_nodes_to_create=logical_nodes_to_create,
            node_versions_to_create=node_versions_to_create,
            version_matches_to_create=version_matches_to_create,
        )

        return IngestionResult(
            document=document,
            version=saved_version,
            is_duplicate=False,
            node_count=len(node_versions_to_create),
            warning_count=len(serialized_warnings),
        )
