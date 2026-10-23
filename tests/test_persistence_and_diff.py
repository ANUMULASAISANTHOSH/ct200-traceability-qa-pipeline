"""Unit and integration tests for document persistence, versioning, lineage matching, and diffing."""

from typing import Generator
import pytest
from sqlalchemy.orm import Session

from src.app.domain.models.diff import DiffStatus
from src.app.domain.models.document import DocumentDraft, DocumentNode, NodeType, PageLocation
from src.app.infrastructure.db import get_engine, get_session_factory, init_db
from src.app.infrastructure.db.models import DocumentVersionORM, NodeVersionORM
from src.app.infrastructure.db.repositories import SQLAlchemyDocumentRepository
from src.app.application.use_cases import IngestDocumentUseCase, DiffVersionsUseCase

V1_PDF = "data/ct200_manual.pdf"
V2_PDF = "data/ct200_manual_v2.pdf"


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """Provide an isolated, clean SQLite in-memory database session."""
    engine = get_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = get_session_factory(engine)
    with session_factory() as session:
        yield session


@pytest.fixture
def repository(db_session: Session) -> SQLAlchemyDocumentRepository:
    return SQLAlchemyDocumentRepository(db_session)


@pytest.fixture
def ingest_use_case(repository: SQLAlchemyDocumentRepository) -> IngestDocumentUseCase:
    return IngestDocumentUseCase(repository)


@pytest.fixture
def diff_use_case(repository: SQLAlchemyDocumentRepository) -> DiffVersionsUseCase:
    return DiffVersionsUseCase(repository)


def test_identical_pdf_reingestion_returns_existing_version(
    ingest_use_case: IngestDocumentUseCase,
    repository: SQLAlchemyDocumentRepository,
):
    """Re-ingesting the identical PDF for the same document returns the existing version without duplicates."""
    res1 = ingest_use_case.execute(
        pdf_path=V1_PDF,
        document_slug="ct200",
        document_title="CT-200 Sphygmomanometer Manual",
        version_label="v1",
    )
    assert res1.is_duplicate is False
    v1_id = res1.version.id

    # Re-ingest identical PDF
    res2 = ingest_use_case.execute(
        pdf_path=V1_PDF,
        document_slug="ct200",
        document_title="CT-200 Sphygmomanometer Manual",
        version_label="v1_attempt_2",
    )
    assert res2.is_duplicate is True
    assert res2.version.id == v1_id

    # Verify no duplicate versions in database
    versions = repository.session.query(DocumentVersionORM).all()
    assert len(versions) == 1
    assert versions[0].id == v1_id


def test_immutable_v1_after_v2_ingestion(
    ingest_use_case: IngestDocumentUseCase,
    repository: SQLAlchemyDocumentRepository,
):
    """Ingesting v2 does not mutate v1 metadata, node records, or hashes."""
    res_v1 = ingest_use_case.execute(V1_PDF, "ct200", "CT-200 Manual", "v1")
    v1_id = res_v1.version.id

    v1_nodes_before = repository.get_node_versions_by_version_id(v1_id)
    v1_snapshot_before = [
        (nv.id, nv.logical_node_id, nv.content_hash, nv.text, nv.source_order)
        for nv in v1_nodes_before
    ]
    v1_warnings_before = list(res_v1.version.warnings)

    # Ingest v2
    res_v2 = ingest_use_case.execute(V2_PDF, "ct200", "CT-200 Manual", "v2")
    assert res_v2.is_duplicate is False
    assert res_v2.version.id != v1_id

    # Re-fetch v1 and assert complete immutability
    v1_after = repository.get_version_by_id(v1_id)
    assert v1_after is not None
    assert v1_after.id == v1_id
    assert v1_after.file_hash == res_v1.version.file_hash
    assert v1_after.warnings == v1_warnings_before

    v1_nodes_after = repository.get_node_versions_by_version_id(v1_id)
    v1_snapshot_after = [
        (nv.id, nv.logical_node_id, nv.content_hash, nv.text, nv.source_order)
        for nv in v1_nodes_after
    ]
    assert v1_snapshot_before == v1_snapshot_after


def test_unchanged_nodes_retaining_logical_identity(
    ingest_use_case: IngestDocumentUseCase,
    diff_use_case: DiffVersionsUseCase,
    repository: SQLAlchemyDocumentRepository,
):
    """Unchanged sections retain their logical identity, content hash, and report UNCHANGED."""
    res_v1 = ingest_use_case.execute(V1_PDF, "ct200", "CT-200 Manual", "v1")
    res_v2 = ingest_use_case.execute(V2_PDF, "ct200", "CT-200 Manual", "v2")

    # Section 1.1 "Intended Use" is identical in v1 and v2
    v1_sec_1_1 = [
        nv for nv in repository.get_node_versions_by_version_id(res_v1.version.id)
        if nv.section_number == "1.1"
    ][0]
    v2_sec_1_1 = [
        nv for nv in repository.get_node_versions_by_version_id(res_v2.version.id)
        if nv.section_number == "1.1"
    ][0]

    assert v1_sec_1_1.logical_node_id == v2_sec_1_1.logical_node_id
    assert v1_sec_1_1.content_hash == v2_sec_1_1.content_hash

    diff_sec_1_1 = diff_use_case.diff_logical_node(
        res_v1.version.id, res_v2.version.id, v1_sec_1_1.logical_node_id
    )
    assert diff_sec_1_1.status == DiffStatus.UNCHANGED
    assert "Content unchanged" in diff_sec_1_1.diff_summary


def test_changed_body_text_retaining_identity_but_changing_hash(
    ingest_use_case: IngestDocumentUseCase,
    diff_use_case: DiffVersionsUseCase,
    repository: SQLAlchemyDocumentRepository,
):
    """A node whose text changes retains its logical identity but receives a new content hash."""
    res_v1 = ingest_use_case.execute(V1_PDF, "ct200", "CT-200 Manual", "v1")
    res_v2 = ingest_use_case.execute(V2_PDF, "ct200", "CT-200 Manual", "v2")

    # Paragraph under Section 2.1.1.1 (Battery Life)
    v1_sec = [
        nv for nv in repository.get_node_versions_by_version_id(res_v1.version.id)
        if nv.section_number == "2.1.1.1"
    ][0]
    v1_para = [
        nv for nv in repository.get_node_versions_by_version_id(res_v1.version.id)
        if nv.parent_node_version_id == v1_sec.id and nv.node_type == NodeType.PARAGRAPH.value
    ][0]

    v2_sec = [
        nv for nv in repository.get_node_versions_by_version_id(res_v2.version.id)
        if nv.section_number == "2.1.1.1"
    ][0]
    v2_para = [
        nv for nv in repository.get_node_versions_by_version_id(res_v2.version.id)
        if nv.parent_node_version_id == v2_sec.id and nv.node_type == NodeType.PARAGRAPH.value
    ][0]

    # Logical identity must be preserved
    assert v1_para.logical_node_id == v2_para.logical_node_id
    # Content hash must differ due to 300 -> 250 cycles change
    assert v1_para.content_hash != v2_para.content_hash

    diff_res = diff_use_case.diff_logical_node(
        res_v1.version.id, res_v2.version.id, v1_para.logical_node_id
    )
    assert diff_res.status == DiffStatus.CHANGED
    assert "-" in diff_res.diff_summary
    assert "+" in diff_res.diff_summary



def test_repeated_headings_under_different_parents_stay_distinct(
    ingest_use_case: IngestDocumentUseCase,
    repository: SQLAlchemyDocumentRepository,
):
    """Heading 'Error Codes' under Section 4 and Section 7 maintain distinct logical identities."""
    res_v1 = ingest_use_case.execute(V1_PDF, "ct200", "CT-200 Manual", "v1")
    res_v2 = ingest_use_case.execute(V2_PDF, "ct200", "CT-200 Manual", "v2")

    v1_nodes = repository.get_node_versions_by_version_id(res_v1.version.id)
    v2_nodes = repository.get_node_versions_by_version_id(res_v2.version.id)

    v1_4_2 = [n for n in v1_nodes if n.section_number == "4.2"][0]
    v1_7_1 = [n for n in v1_nodes if n.section_number == "7.1"][0]

    # In v1, both share the title 'Error Codes' but must have distinct logical IDs
    assert "Error Codes" in v1_4_2.heading
    assert "Error Codes" in v1_7_1.heading
    assert v1_4_2.logical_node_id != v1_7_1.logical_node_id

    # In v2, Section 4.2 matches Section 4.2, Section 7.1 matches Section 7.1
    v2_4_2 = [n for n in v2_nodes if n.section_number == "4.2"][0]
    v2_7_1 = [n for n in v2_nodes if n.section_number == "7.1"][0]

    assert v2_4_2.logical_node_id == v1_4_2.logical_node_id
    assert v2_7_1.logical_node_id == v1_7_1.logical_node_id
    assert v2_4_2.logical_node_id != v2_7_1.logical_node_id


def test_added_and_removed_nodes_reporting(
    ingest_use_case: IngestDocumentUseCase,
    diff_use_case: DiffVersionsUseCase,
    repository: SQLAlchemyDocumentRepository,
):
    """Section 5.3 added in v2 is reported as ADDED, and synthetic missing node is reported as REMOVED."""
    res_v1 = ingest_use_case.execute(V1_PDF, "ct200", "CT-200 Manual", "v1")
    res_v2 = ingest_use_case.execute(V2_PDF, "ct200", "CT-200 Manual", "v2")

    v2_nodes = repository.get_node_versions_by_version_id(res_v2.version.id)
    sec_5_3 = [n for n in v2_nodes if n.section_number == "5.3"][0]

    diff_5_3 = diff_use_case.diff_logical_node(
        res_v1.version.id, res_v2.version.id, sec_5_3.logical_node_id
    )
    assert diff_5_3.status == DiffStatus.ADDED
    assert "Node added in target version" in diff_5_3.diff_summary

    # Diff entire document: verify aggregate counts include added and removed
    report = diff_use_case.diff_versions(res_v1.version.id, res_v2.version.id)
    assert report.summary_counts[DiffStatus.ADDED.value] >= 1
    assert report.summary_counts[DiffStatus.REMOVED.value] >= 1
    assert report.summary_counts[DiffStatus.UNCHANGED.value] > 0
    assert report.summary_counts[DiffStatus.CHANGED.value] > 0


def test_uncertain_matches_flagged_as_unresolved(
    ingest_use_case: IngestDocumentUseCase,
    diff_use_case: DiffVersionsUseCase,
    repository: SQLAlchemyDocumentRepository,
):
    """Ambiguous sections with conflicting candidates are flagged as unresolved and not linked."""
    res_v1 = ingest_use_case.execute(V1_PDF, "ct200", "CT-200 Manual", "v1")

    # Ingest a synthetic draft v3 with an ambiguous section under Section 1
    # that borrows heading from Section 4.2 under Section 4
    v2_draft = ingest_use_case.parser.parse(V2_PDF, document_id="ct200")
    # Add an ambiguous section
    ambiguous_sec = DocumentNode(
        id="synthetic_ambiguous",
        node_type=NodeType.SECTION,
        title="Error Codes",  # Exists in v1 under 4.2 and 7.1, but here inserted under Section 1!
        section_number="1.99",
        depth=2,
        page_location=PageLocation(page_number=1),
    )
    # Insert under Section 1
    sec1 = [n for n in v2_draft.flatten() if n.section_number == "1"][0]
    sec1.add_child(ambiguous_sec)

    plans = ingest_use_case.matcher.match_draft_against_prior_version(
        prior_node_versions=repository.get_node_versions_by_version_id(res_v1.version.id),
        draft=v2_draft,
        document_id="ct200",
    )

    ambiguous_plan = [p for p in plans if p.draft_node.id == "synthetic_ambiguous"][0]
    assert ambiguous_plan.status == "unresolved"
    assert ambiguous_plan.match_method == "UNRESOLVED"
    assert ambiguous_plan.confidence == 0.0
    assert len(ambiguous_plan.unresolved_candidates) > 0
    assert ambiguous_plan.is_new_logical_node is True  # Did not silently link!


def test_actual_ct200_changes_v1_to_v2(
    ingest_use_case: IngestDocumentUseCase,
    diff_use_case: DiffVersionsUseCase,
    repository: SQLAlchemyDocumentRepository,
):
    """Verify all 5 verified domain changes between CT-200 v1 and v2 manuals:
    1. Battery values: 300 cycles / 15% -> 250 cycles / 10%
    2. Inflation increment: 40 mmHg -> 30 mmHg
    3. Overpressure timing: >2.0s -> >1.5s in Table 4.2
    4. E6 error code: Bluetooth sync failure added to Table 4.2
    5. Section 5.3: Data Export CSV export added
    """
    res_v1 = ingest_use_case.execute(V1_PDF, "ct200", "CT-200 Manual", "v1")
    res_v2 = ingest_use_case.execute(V2_PDF, "ct200", "CT-200 Manual", "v2")

    report = diff_use_case.diff_versions(res_v1.version.id, res_v2.version.id)

    # 1. Battery values in Section 2.1.1.1
    battery_diffs = [
        d for d in report.node_diffs
        if d.section_number == "2.1.1.1" and d.node_type == NodeType.PARAGRAPH.value
    ]
    assert len(battery_diffs) == 1
    b_diff = battery_diffs[0]
    assert b_diff.status == DiffStatus.CHANGED
    assert "-" in b_diff.diff_summary
    assert "+" in b_diff.diff_summary

    assert "300" in b_diff.diff_summary
    assert "250" in b_diff.diff_summary
    assert "15%" in b_diff.diff_summary
    assert "10%" in b_diff.diff_summary

    # 2. Inflation increment in Section 3.2
    inflation_diffs = [
        d for d in report.node_diffs
        if d.section_number == "3.2" and d.node_type == NodeType.PARAGRAPH.value
    ]
    assert len(inflation_diffs) == 1
    inf_diff = inflation_diffs[0]
    assert inf_diff.status == DiffStatus.CHANGED
    assert "40 mmHg" in inf_diff.diff_summary
    assert "30 mmHg" in inf_diff.diff_summary

    # 3 & 4. Table 4.2 Error Codes Table (Overpressure timing and E6 addition)
    table_diffs = [
        d for d in report.node_diffs
        if d.section_number == "4.2" and d.node_type == NodeType.TABLE.value
    ]
    assert len(table_diffs) == 1
    t_diff = table_diffs[0]
    assert t_diff.status == DiffStatus.CHANGED
    # Overpressure timing change
    assert "2 seconds" in t_diff.diff_summary
    assert "1.5 seconds" in t_diff.diff_summary

    # E6 addition
    assert "E6" in t_diff.diff_summary
    assert "Bluetooth sync failure" in t_diff.diff_summary

    # 5. Section 5.3 Data Export
    sec_5_3_diffs = [
        d for d in report.node_diffs
        if d.section_number == "5.3" and d.node_type == NodeType.SECTION.value
    ]
    assert len(sec_5_3_diffs) == 1
    assert sec_5_3_diffs[0].status == DiffStatus.ADDED
    assert "5.3 Data Export" in sec_5_3_diffs[0].heading

    para_5_3_diffs = [
        d for d in report.node_diffs
        if d.section_number == "5.3" and d.node_type == NodeType.PARAGRAPH.value
    ]
    assert len(para_5_3_diffs) == 1
    assert para_5_3_diffs[0].status == DiffStatus.ADDED


def test_parser_warnings_preserved_in_version_storage(
    ingest_use_case: IngestDocumentUseCase,
):
    """Extraction warnings emitted by DocumentParser are stored in DocumentVersion.warnings."""
    res_v1 = ingest_use_case.execute(V1_PDF, "ct200", "CT-200 Manual", "v1")
    warnings = res_v1.version.warnings
    assert len(warnings) > 0

    codes = [w["code"] for w in warnings]
    assert "MISSING_PARENT_SECTION" in codes
    assert "OUT_OF_ORDER_SECTION" in codes
    assert "FALSE_TABLE_FRAGMENT_MERGED" in codes

    # Ensure warning node references are serialized
    for w in warnings:
        assert "code" in w
        assert "message" in w
        assert "page_number" in w
        assert "node_id" in w
