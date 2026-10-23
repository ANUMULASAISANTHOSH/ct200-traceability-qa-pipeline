"""Use case for searching node headings and content within a document version."""

import json
from typing import List, Optional
from src.app.api.schemas.document import SearchResponse, SearchResultItem
from src.app.application.interfaces.document_repository import IDocumentRepository
from src.app.domain.exceptions import (
    DocumentNotFoundError,
    InvalidQueryError,
    VersionNotFoundError,
)
from src.app.domain.models.document import NodeType

VALID_NODE_TYPES = {t.value for t in NodeType if t != NodeType.DOCUMENT_ROOT}


def build_snippet(text: str, query: str, window: int = 120) -> str:
    """Generate a clean contextual snippet surrounding the query match."""
    if not text:
        return ""
    lower_text = text.lower()
    idx = lower_text.find(query.lower())
    if idx == -1:
        return text[:window].strip()

    start = max(0, idx - 40)
    end = min(len(text), idx + len(query) + 60)
    snippet = text[start:end].strip()
    if start > 0:
        snippet = f"...{snippet}"
    if end < len(text):
        snippet = f"{snippet}..."
    return snippet


class SearchNodesUseCase:
    """Use case for executing scoped searches across document version nodes."""

    def __init__(self, repository: IDocumentRepository) -> None:
        self.repository = repository

    def execute(
        self,
        document_slug: str,
        query: str,
        version_id: Optional[str] = None,
        node_type: Optional[str] = None,
        limit: int = 50,
    ) -> SearchResponse:
        """Search node headings, text, and tables within a document version."""
        # 1. Parameter Validation
        if limit <= 0 or limit > 100:
            raise InvalidQueryError("Limit must be an integer between 1 and 100")

        if node_type is not None:
            norm_type = node_type.strip().lower()
            if norm_type not in VALID_NODE_TYPES:
                raise InvalidQueryError(
                    f"Invalid node_type '{node_type}'. Must be one of: {', '.join(sorted(VALID_NODE_TYPES))}"
                )
        else:
            norm_type = None

        # 2. Document & Version Resolution
        doc = self.repository.get_document_by_slug(document_slug)
        if doc is None:
            raise DocumentNotFoundError(f"Document '{document_slug}' not found")

        if version_id is not None:
            ver = self.repository.get_version_by_id(version_id)
            if ver is None or ver.document_id != doc.id:
                raise VersionNotFoundError(
                    f"Version '{version_id}' not found for document '{document_slug}'"
                )
        else:
            ver = self.repository.get_latest_version(doc.id)
            if ver is None:
                raise VersionNotFoundError(f"No versions found for document '{document_slug}'")

        clean_query = query.strip()
        if not clean_query:
            return SearchResponse(
                document_slug=doc.slug,
                version_id=ver.id,
                version_label=ver.version_label,
                query=query,
                total_results=0,
                results=[],
            )

        q_lower = clean_query.lower()
        all_nodes = self.repository.get_node_versions_by_version_id(ver.id)

        # 3. Matching and Ranking
        candidates = []
        for n in all_nodes:
            if n.node_type == NodeType.DOCUMENT_ROOT.value:
                continue

            if norm_type is not None and n.node_type != norm_type:
                continue

            matched_field = None
            snippet = ""
            rank_score = 99

            # Check heading
            if n.heading and q_lower in n.heading.lower():
                matched_field = "heading"
                snippet = n.heading
                rank_score = 1 if n.heading.lower().startswith(q_lower) else 2
            # Check text body
            elif n.text and q_lower in n.text.lower():
                matched_field = "text"
                snippet = build_snippet(n.text, clean_query)
                rank_score = 3
            # Check structured table rows
            elif n.structured_data and "rows" in n.structured_data:
                rows = n.structured_data.get("rows", [])
                for r in rows:
                    row_str = " | ".join(str(cell) for cell in r)
                    if q_lower in row_str.lower():
                        matched_field = "table"
                        snippet = build_snippet(row_str, clean_query)
                        rank_score = 3
                        break

            if matched_field is not None:
                candidates.append((
                    rank_score,
                    n.source_order,
                    SearchResultItem(
                        node_version_id=n.id,
                        logical_node_id=n.logical_node_id,
                        node_type=n.node_type,
                        heading=n.heading,
                        section_number=n.section_number,
                        match_field=matched_field,
                        snippet=snippet,
                        page_start=n.page_start,
                        page_end=n.page_end,
                        content_hash=n.content_hash,
                    ),
                ))

        # Sort by rank score, then physical source order
        candidates.sort(key=lambda item: (item[0], item[1]))
        matched_results = [item[2] for item in candidates[:limit]]

        return SearchResponse(
            document_slug=doc.slug,
            version_id=ver.id,
            version_label=ver.version_label,
            query=clean_query,
            total_results=len(matched_results),
            results=matched_results,
        )
