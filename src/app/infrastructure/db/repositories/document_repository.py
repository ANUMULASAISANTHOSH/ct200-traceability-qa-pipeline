"""SQLAlchemy implementation of the Document repository."""

from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.orm import Session
from src.app.application.interfaces.document_repository import IDocumentRepository
from src.app.infrastructure.db.models import (
    DocumentORM,
    DocumentVersionORM,
    LogicalNodeORM,
    NodeVersionORM,
    VersionMatchORM,
    generate_id,
)


class SQLAlchemyDocumentRepository(IDocumentRepository):
    """SQLAlchemy adapter implementing IDocumentRepository."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_document_by_slug(self, slug: str) -> Optional[DocumentORM]:
        stmt = select(DocumentORM).where(DocumentORM.slug == slug)
        return self.session.execute(stmt).scalar_one_or_none()

    def get_or_create_document(self, slug: str, title: str) -> DocumentORM:
        doc = self.get_document_by_slug(slug)
        if doc is None:
            doc = DocumentORM(id=generate_id("doc"), slug=slug, title=title)
            self.session.add(doc)
            self.session.flush()
        return doc

    def get_version_by_hash(self, document_id: str, file_hash: str) -> Optional[DocumentVersionORM]:
        stmt = select(DocumentVersionORM).where(
            DocumentVersionORM.document_id == document_id,
            DocumentVersionORM.file_hash == file_hash,
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def get_version_by_id(self, version_id: str) -> Optional[DocumentVersionORM]:
        stmt = select(DocumentVersionORM).where(DocumentVersionORM.id == version_id)
        return self.session.execute(stmt).scalar_one_or_none()

    def get_version_by_label(self, document_id: str, label: str) -> Optional[DocumentVersionORM]:
        stmt = select(DocumentVersionORM).where(
            DocumentVersionORM.document_id == document_id,
            DocumentVersionORM.version_label == label,
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def get_latest_version(self, document_id: str) -> Optional[DocumentVersionORM]:
        stmt = (
            select(DocumentVersionORM)
            .where(DocumentVersionORM.document_id == document_id)
            .order_by(DocumentVersionORM.created_at.desc())
        )
        return self.session.execute(stmt).scalars().first()

    def get_node_versions_by_version_id(self, version_id: str) -> List[NodeVersionORM]:
        stmt = (
            select(NodeVersionORM)
            .where(NodeVersionORM.document_version_id == version_id)
            .order_by(NodeVersionORM.source_order.asc())
        )
        return list(self.session.execute(stmt).scalars().all())

    def get_node_version_by_logical_id(
        self, version_id: str, logical_node_id: str
    ) -> Optional[NodeVersionORM]:
        stmt = select(NodeVersionORM).where(
            NodeVersionORM.document_version_id == version_id,
            NodeVersionORM.logical_node_id == logical_node_id,
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def get_logical_nodes_by_document_id(self, document_id: str) -> List[LogicalNodeORM]:
        stmt = (
            select(LogicalNodeORM)
            .where(LogicalNodeORM.document_id == document_id)
            .order_by(LogicalNodeORM.created_at.asc())
        )
        return list(self.session.execute(stmt).scalars().all())

    def get_logical_node_by_id(self, logical_node_id: str) -> Optional[LogicalNodeORM]:
        stmt = select(LogicalNodeORM).where(LogicalNodeORM.id == logical_node_id)
        return self.session.execute(stmt).scalar_one_or_none()

    def save_ingested_version(
        self,
        document_version: DocumentVersionORM,
        logical_nodes_to_create: List[LogicalNodeORM],
        node_versions_to_create: List[NodeVersionORM],
        version_matches_to_create: List[VersionMatchORM],
    ) -> DocumentVersionORM:
        for log_node in logical_nodes_to_create:
            self.session.add(log_node)
        self.session.flush()

        self.session.add(document_version)
        self.session.flush()

        for nv in node_versions_to_create:
            self.session.add(nv)
        self.session.flush()

        for vm in version_matches_to_create:
            self.session.add(vm)
        self.session.flush()

        self.session.commit()
        self.session.refresh(document_version)
        return document_version

    def get_version_matches(self, version_id: str) -> List[VersionMatchORM]:
        stmt = select(VersionMatchORM).where(
            VersionMatchORM.target_document_version_id == version_id
        )
        return list(self.session.execute(stmt).scalars().all())
