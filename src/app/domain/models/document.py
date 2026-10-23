"""Domain models for structured document representations and parsed hierarchy."""

from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field


class NodeType(str, Enum):
    """Categorization of document structural elements."""
    DOCUMENT_ROOT = "document_root"
    SECTION = "section"
    PARAGRAPH = "paragraph"
    LIST = "list"
    LIST_ITEM = "list_item"
    TABLE = "table"


class PageLocation(BaseModel):
    """Spatial and page occurrence data for an extracted document element."""
    page_number: int = Field(description="Primary page where the element starts (1-indexed)")
    page_numbers: List[int] = Field(
        default_factory=list,
        description="List of all page numbers this element spans",
    )
    bbox: Optional[Tuple[float, float, float, float]] = Field(
        default=None,
        description="Bounding box coordinates (x0, y0, x1, y1) if available",
    )

    def model_post_init(self, __context: Any) -> None:
        if not self.page_numbers:
            self.page_numbers = [self.page_number]


class ExtractionWarning(BaseModel):
    """Warning emitted during extraction or hierarchy reconstruction."""
    code: str = Field(description="Standardized warning code")
    message: str = Field(description="Human-readable explanation of the edge case or anomaly")
    page_number: Optional[int] = Field(default=None, description="Page number where the anomaly was detected")
    node_id: Optional[str] = Field(default=None, description="Associated node ID if applicable")


class TableData(BaseModel):
    """Structured representation of a table."""
    headers: List[str] = Field(default_factory=list, description="Column header strings")
    rows: List[List[str]] = Field(default_factory=list, description="Data rows as lists of cell strings")


class DocumentNode(BaseModel):
    """Typed node representing a structural component in the document tree."""
    id: str = Field(description="Unique deterministic identifier within the document draft")
    node_type: NodeType = Field(description="Semantic node type")
    title: Optional[str] = Field(default=None, description="Section or element title")
    section_number: Optional[str] = Field(default=None, description="Declared section number (e.g., '2.1.1.1')")
    depth: int = Field(default=0, description="Declared or structural hierarchy depth")
    content: str = Field(default="", description="Textual body content")
    source_order: int = Field(default=0, description="Monotonically increasing reading order index")
    page_location: PageLocation = Field(description="Page location data")
    parent_id: Optional[str] = Field(default=None, description="Identifier of parent node in hierarchy")
    children: List["DocumentNode"] = Field(default_factory=list, description="Direct child nodes")
    table_data: Optional[TableData] = Field(default=None, description="Table rows/headers if node is TABLE")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional contextual metadata")

    def add_child(self, child: "DocumentNode") -> None:
        """Attach a child node and update its parent reference."""
        child.parent_id = self.id
        self.children.append(child)


class DocumentDraft(BaseModel):
    """Root container representing the reconstructed document AST."""
    document_id: str = Field(description="Identifier of the document (e.g., 'ct200_manual')")
    filename: str = Field(description="Source file name")
    total_pages: int = Field(description="Total page count of the source document")
    extraction_method: str = Field(
        default="vector_layout",
        description="Extraction pipeline used ('vector_layout', 'ocr', or 'hybrid')",
    )
    root: DocumentNode = Field(description="Root node of the hierarchical tree")
    warnings: List[ExtractionWarning] = Field(
        default_factory=list,
        description="Extraction and reconstruction warnings",
    )

    def flatten(self) -> List[DocumentNode]:
        """Return all nodes in depth-first pre-order traversal."""
        result: List[DocumentNode] = []

        def _traverse(node: DocumentNode) -> None:
            result.append(node)
            for child in node.children:
                _traverse(child)

        _traverse(self.root)
        return result

    def get_nodes_in_source_order(self) -> List[DocumentNode]:
        """Return all non-root nodes sorted by physical source order."""
        nodes = [n for n in self.flatten() if n.node_type != NodeType.DOCUMENT_ROOT]
        return sorted(nodes, key=lambda n: n.source_order)

    def find_node_by_id(self, node_id: str) -> Optional[DocumentNode]:
        """Find a node by its ID."""
        for node in self.flatten():
            if node.id == node_id:
                return node
        return None

    def find_nodes_by_section(self, section_number: str) -> List[DocumentNode]:
        """Find nodes matching a specific declared section number."""
        return [n for n in self.flatten() if n.section_number == section_number]

    def find_nodes_by_title(self, title: str) -> List[DocumentNode]:
        """Find nodes with matching title (case-insensitive substring or exact)."""
        title_lower = title.strip().lower()
        return [
            n for n in self.flatten()
            if n.title and title_lower in n.title.strip().lower()
        ]

    def find_nodes_by_type(self, node_type: NodeType) -> List[DocumentNode]:
        """Find all nodes of a given NodeType."""
        return [n for n in self.flatten() if n.node_type == node_type]
