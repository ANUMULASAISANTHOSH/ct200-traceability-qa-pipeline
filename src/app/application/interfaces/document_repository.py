"""Repository interface for document persistence and lineage queries."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from src.app.infrastructure.db.models import (
    DocumentORM,
    DocumentVersionORM,
    GenerationIndexORM,
    LogicalNodeORM,
    NodeVersionORM,
    SelectionItemORM,
    SelectionORM,
    VersionMatchORM,
)



class IDocumentRepository(ABC):
    """Abstract port for accessing and storing documents, versions, and nodes."""

    @abstractmethod
    def get_document_by_slug(self, slug: str) -> Optional[DocumentORM]:
        """Retrieve a document by its unique slug."""
        pass

    @abstractmethod
    def get_or_create_document(self, slug: str, title: str) -> DocumentORM:
        """Find an existing document by slug or create a new one."""
        pass

    @abstractmethod
    def get_version_by_hash(self, document_id: str, file_hash: str) -> Optional[DocumentVersionORM]:
        """Check if an identical PDF has already been ingested for this document."""
        pass

    @abstractmethod
    def get_version_by_id(self, version_id: str) -> Optional[DocumentVersionORM]:
        """Fetch a specific version by its persistent ID."""
        pass

    @abstractmethod
    def get_node_version_by_id(self, node_version_id: str) -> Optional[NodeVersionORM]:
        """Fetch a specific node version snapshot by its persistent ID."""
        pass


    @abstractmethod
    def get_version_by_label(self, document_id: str, label: str) -> Optional[DocumentVersionORM]:
        """Fetch a version by document ID and version label (e.g., 'v1')."""
        pass

    @abstractmethod
    def get_latest_version(self, document_id: str) -> Optional[DocumentVersionORM]:
        """Fetch the most recently ingested version for a document."""
        pass

    @abstractmethod
    def get_node_versions_by_version_id(self, version_id: str) -> List[NodeVersionORM]:
        """Retrieve all node snapshots for a given version in source order."""
        pass

    @abstractmethod
    def get_node_version_by_logical_id(
        self, version_id: str, logical_node_id: str
    ) -> Optional[NodeVersionORM]:
        """Find the node snapshot for a specific logical node in a version."""
        pass

    @abstractmethod
    def get_logical_nodes_by_document_id(self, document_id: str) -> List[LogicalNodeORM]:
        """List all logical nodes associated with a document."""
        pass

    @abstractmethod
    def get_logical_node_by_id(self, logical_node_id: str) -> Optional[LogicalNodeORM]:
        """Fetch a logical node by its persistent ID."""
        pass

    @abstractmethod
    def save_ingested_version(
        self,
        document_version: DocumentVersionORM,
        logical_nodes_to_create: List[LogicalNodeORM],
        node_versions_to_create: List[NodeVersionORM],
        version_matches_to_create: List[VersionMatchORM],
    ) -> DocumentVersionORM:
        """Atomically persist a new document version with its nodes and matches."""
        pass

    @abstractmethod
    def get_version_matches(self, version_id: str) -> List[VersionMatchORM]:
        """Fetch all match records for a target version."""
        pass

    @abstractmethod
    def create_selection(
        self, selection: SelectionORM, items: List[SelectionItemORM]
    ) -> SelectionORM:
        """Atomically persist a version-pinned selection and its ordered items."""
        pass

    @abstractmethod
    def get_selection_by_id(self, selection_id: str) -> Optional[SelectionORM]:
        """Retrieve a selection by persistent ID."""
        pass

    @abstractmethod
    def save_generation_index(self, record: GenerationIndexORM) -> GenerationIndexORM:
        """Atomically persist a generation index record."""
        pass

    @abstractmethod
    def get_generation_index(self, generation_id: str) -> Optional[GenerationIndexORM]:
        """Retrieve a generation index record by generation ID."""
        pass

    @abstractmethod
    def get_generation_by_idempotency_key(
        self, selection_id: str, idempotency_key: str
    ) -> Optional[GenerationIndexORM]:
        """Retrieve a generation index record by selection ID and idempotency key."""
        pass

