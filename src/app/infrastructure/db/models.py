"""SQLAlchemy ORM models for document persistence, versioning, and node lineage."""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from src.app.infrastructure.db.session import Base


def generate_id(prefix: str = "id") -> str:
    """Generate a clean, persistent surrogate identifier with a descriptive prefix."""
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class DocumentORM(Base):
    """Stable manual identity representing a product line or document family."""
    __tablename__ = "documents"

    id = Column(String(64), primary_key=True, default=lambda: generate_id("doc"))
    slug = Column(String(128), unique=True, nullable=False, index=True)
    title = Column(String(256), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    versions = relationship(
        "DocumentVersionORM",
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="DocumentVersionORM.created_at",
    )
    logical_nodes = relationship(
        "LogicalNodeORM",
        back_populates="document",
        cascade="all, delete-orphan",
    )


class DocumentVersionORM(Base):
    """Immutable snapshot of a document version derived from an ingested PDF."""
    __tablename__ = "document_versions"

    id = Column(String(64), primary_key=True, default=lambda: generate_id("ver"))
    document_id = Column(
        String(64),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_label = Column(String(64), nullable=False)
    file_hash = Column(String(64), nullable=False, index=True)  # SHA-256 of source PDF
    filename = Column(String(256), nullable=False)
    total_pages = Column(Integer, nullable=False)
    extraction_method = Column(String(64), nullable=False, default="vector_layout")
    warnings = Column(JSON, nullable=False, default=list)  # Preserved extraction warnings
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("document_id", "file_hash", name="uq_doc_version_hash"),
        UniqueConstraint("document_id", "version_label", name="uq_doc_version_label"),
    )

    # Relationships
    document = relationship("DocumentORM", back_populates="versions")
    node_versions = relationship(
        "NodeVersionORM",
        back_populates="document_version",
        cascade="all, delete-orphan",
        order_by="NodeVersionORM.source_order",
    )
    matches_as_target = relationship(
        "VersionMatchORM",
        foreign_keys="VersionMatchORM.target_document_version_id",
        back_populates="target_document_version",
        cascade="all, delete-orphan",
    )


class LogicalNodeORM(Base):
    """Stable semantic identity for a section or content node across versions."""
    __tablename__ = "logical_nodes"

    id = Column(String(64), primary_key=True, default=lambda: generate_id("log"))
    document_id = Column(
        String(64),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    node_type = Column(String(32), nullable=False)
    canonical_heading = Column(String(256), nullable=True)
    canonical_path = Column(String(512), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    document = relationship("DocumentORM", back_populates="logical_nodes")
    node_versions = relationship(
        "NodeVersionORM",
        back_populates="logical_node",
    )


class NodeVersionORM(Base):
    """Exact immutable parsed snapshot of one node for a specific document version."""
    __tablename__ = "node_versions"

    id = Column(String(64), primary_key=True, default=lambda: generate_id("nv"))
    document_version_id = Column(
        String(64),
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    logical_node_id = Column(
        String(64),
        ForeignKey("logical_nodes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    parent_node_version_id = Column(
        String(64),
        ForeignKey("node_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    node_type = Column(String(32), nullable=False)
    heading = Column(String(256), nullable=True)
    section_number = Column(String(64), nullable=True)
    declared_depth = Column(Integer, nullable=False, default=0)
    source_order = Column(Integer, nullable=False, index=True)
    page_start = Column(Integer, nullable=False)
    page_end = Column(Integer, nullable=False)
    page_numbers = Column(JSON, nullable=False, default=list)
    bbox = Column(JSON, nullable=True)
    text = Column(Text, nullable=False, default="")
    structured_data = Column(JSON, nullable=True)
    content_hash = Column(String(64), nullable=False, index=True)
    draft_node_id = Column(String(128), nullable=True)

    # Relationships
    document_version = relationship("DocumentVersionORM", back_populates="node_versions")
    logical_node = relationship("LogicalNodeORM", back_populates="node_versions")
    parent = relationship(
        "NodeVersionORM",
        remote_side=[id],
        back_populates="children",
    )
    children = relationship(
        "NodeVersionORM",
        back_populates="parent",
        cascade="all, delete-orphan",
    )


class VersionMatchORM(Base):
    """Lineage match record recording the alignment method, confidence, and candidates."""
    __tablename__ = "version_matches"

    id = Column(String(64), primary_key=True, default=lambda: generate_id("vm"))
    target_document_version_id = Column(
        String(64),
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_node_version_id = Column(
        String(64),
        ForeignKey("node_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_node_version_id = Column(
        String(64),
        ForeignKey("node_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    logical_node_id = Column(
        String(64),
        ForeignKey("logical_nodes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    match_method = Column(String(64), nullable=False)
    confidence = Column(Float, nullable=False, default=1.0)
    status = Column(String(32), nullable=False)  # "matched", "added", "unresolved"
    unresolved_candidates = Column(JSON, nullable=False, default=list)
    notes = Column(Text, nullable=True)

    # Relationships
    target_document_version = relationship(
        "DocumentVersionORM",
        foreign_keys=[target_document_version_id],
        back_populates="matches_as_target",
    )
    target_node_version = relationship(
        "NodeVersionORM",
        foreign_keys=[target_node_version_id],
    )
    source_node_version = relationship(
        "NodeVersionORM",
        foreign_keys=[source_node_version_id],
    )
    logical_node = relationship(
        "LogicalNodeORM",
        foreign_keys=[logical_node_id],
    )


class SelectionORM(Base):
    """Named collection of version-pinned node references for deterministic QA synthesis."""
    __tablename__ = "selections"

    id = Column(String(64), primary_key=True, default=lambda: generate_id("sel"))
    document_version_id = Column(
        String(64),
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(256), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    document_version = relationship("DocumentVersionORM")
    items = relationship(
        "SelectionItemORM",
        back_populates="selection",
        cascade="all, delete-orphan",
        order_by="SelectionItemORM.position",
    )


class SelectionItemORM(Base):
    """Ordered node reference within a version-pinned selection."""
    __tablename__ = "selection_items"

    id = Column(String(64), primary_key=True, default=lambda: generate_id("item"))
    selection_id = Column(
        String(64),
        ForeignKey("selections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    node_version_id = Column(
        String(64),
        ForeignKey("node_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position = Column(Integer, nullable=False, index=True)
    content_hash = Column(String(64), nullable=False)

    # Relationships
    selection = relationship("SelectionORM", back_populates="items")
    node_version = relationship("NodeVersionORM")


class GenerationIndexORM(Base):
    """SQLite index record pointing to the append-only JSON generation document."""
    __tablename__ = "generation_index"

    id = Column(String(64), primary_key=True, default=lambda: generate_id("gen"))
    selection_id = Column(
        String(64),
        ForeignKey("selections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    idempotency_key = Column(String(128), nullable=False, index=True)
    status = Column(String(32), nullable=False, index=True)  # "SUCCESS", "FAILED"
    json_path = Column(String(512), nullable=False)
    source_node_version_ids = Column(JSON, nullable=False, default=list)
    test_case_count = Column(Integer, nullable=False, default=0)
    prompt_version = Column(String(32), nullable=False)
    schema_version = Column(String(32), nullable=False)
    provider = Column(String(64), nullable=False)
    model = Column(String(64), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("selection_id", "idempotency_key", name="uq_gen_selection_idempotency"),
    )

    # Relationships
    selection = relationship("SelectionORM")

