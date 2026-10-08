"""Shared data models: source PDFs, parsed documents and chunks.

Facts produced by parsers (Primary*) are kept separate from the assembled
document (ParsedDocument) that downstream chunking consumes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypeAlias

if TYPE_CHECKING:
    from rag.ingest.tables.models import HeaderDecision, SerializedTable, StructuredTable, TableSlot


TextKind: TypeAlias = Literal["title", "paragraph", "header", "footer", "footnote",
                              "caption", "table_note", "other_text"]
WarningCode: TypeAlias = Literal[
    "source_location_unavailable", "detached_text_recovered", "detached_list_item",
    "unknown_docling_text_label", "unsupported_nontext_item", "orphan_nested_list_group",
    "formula_orig_fallback", "empty_text_node", "optional_tool_page_failed", "optional_tool_unavailable",
    "artifact_cleanup_failed",
]
ProcessingStage: TypeAlias = Literal[
    "configuration", "docling_layout_parsing", "layout_mapping", "table_extraction",
    "table_admission", "table_grouping", "table_scoring", "parsed_document_fusion",
    "table_header_detection", "table_serialization", "document_text_serialization",
    "structured_chunking", "artifact_staging", "artifact_cleanup", "index_publication",
]
ContextKind: TypeAlias = Literal["none", "overlap", "table_header", "merged_cell",
                                 "list_ancestor", "fallback_locator"]
ChunkKind: TypeAlias = Literal["text", "list", "table"]


@dataclass(frozen=True)
class SourceDocument:
    document_id: str
    document_name: str
    relative_path: str
    absolute_path: Path
    file_hash: str


@dataclass(frozen=True)
class BoundingBox:
    x0: float
    y0: float
    x1: float
    y1: float
    coordinate_space: Literal["pymupdf_page_top_left_pt_v1"] = "pymupdf_page_top_left_pt_v1"

    def __post_init__(self) -> None:
        if (not all(math.isfinite(value) for value in (self.x0, self.y0, self.x1, self.y1))
                or self.x1 < self.x0 or self.y1 < self.y0):
            raise ValueError("invalid page bounding box")

    @property
    def area(self) -> float:
        return (self.x1 - self.x0) * (self.y1 - self.y0)


@dataclass(frozen=True)
class PageSpan:
    page_number: int
    bbox: BoundingBox | None
    source_ref: str

    def __post_init__(self) -> None:
        if type(self.page_number) is not int or self.page_number <= 0:
            raise ValueError("page number must be a positive physical page")


@dataclass(frozen=True)
class WordBox:
    """Where one word of an element's text sits: text[start:end] is drawn inside bbox on that page."""
    start: int
    end: int
    page_number: int
    bbox: BoundingBox


@dataclass(frozen=True)
class ChunkRegion:
    """A place on the PDF the chunk's content comes from.

    fine: the box holds exactly this content (word, paragraph, list item, table cell).
    coarse: the content lies somewhere inside the box (or anywhere on the page when bbox is None),
    because the parser gave no finer position.
    """
    page_number: int
    bbox: BoundingBox | None
    precision: Literal["fine", "coarse"]


@dataclass(frozen=True)
class ProcessingWarning:
    code: WarningCode
    stage: ProcessingStage
    message: str
    source_refs: tuple[str, ...]


@dataclass(frozen=True)
class PrimaryText:
    element_id: str
    kind: TextKind
    text: str
    sources: tuple[PageSpan, ...]
    source_ref: str | None
    word_boxes: tuple[WordBox, ...] = ()     # per-word positions, when the parser knows them


@dataclass(frozen=True)
class ListItem:
    item_id: str
    text: str
    level: int
    ordinal: str | None
    parent_item_id: str | None
    sources: tuple[PageSpan, ...]
    source_ref: str | None


@dataclass(frozen=True)
class PrimaryList:
    element_id: str
    items: tuple[ListItem, ...]
    source_refs: tuple[str, ...]
    sources: tuple[PageSpan, ...]


@dataclass(frozen=True)
class PrimaryTablePlaceholder:
    element_id: str
    slot_id: str
    source_ref: str
    sources: tuple[PageSpan, ...]


@dataclass(frozen=True)
class NativeTableFact:
    slot_id: str
    source_ref: str
    table: StructuredTable


PrimaryElement: TypeAlias = PrimaryText | PrimaryList | PrimaryTablePlaceholder


@dataclass(frozen=True)
class PrimaryDocument:
    document_id: str
    file_hash: str
    page_count: int
    elements: tuple[PrimaryElement, ...]
    table_slots: tuple[TableSlot, ...]
    native_tables: tuple[NativeTableFact, ...]
    warnings: tuple[ProcessingWarning, ...]
    schema_version: str = "primary_document_v3"

    def __post_init__(self) -> None:
        if type(self.page_count) is not int or self.page_count <= 0:
            raise ValueError("primary document must record physical pages")
        if self.schema_version != "primary_document_v3":
            raise ValueError("unsupported primary document schema")
        ids = [item.element_id for item in self.elements]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate primary element ID")
        placeholders = [item.slot_id for item in self.elements
                        if isinstance(item, PrimaryTablePlaceholder)]
        if placeholders != [slot.slot_id for slot in self.table_slots] or placeholders != [table.slot_id for table in self.native_tables]:
            raise ValueError("table placeholders, slots and native facts disagree")


@dataclass(frozen=True)
class TextNode:
    node_id: str
    kind: TextKind
    text: str
    sources: tuple[PageSpan, ...]
    source_ref: str | None
    word_boxes: tuple[WordBox, ...] = ()


@dataclass(frozen=True)
class ListNode:
    node_id: str
    items: tuple[ListItem, ...]
    source_refs: tuple[str, ...]
    sources: tuple[PageSpan, ...]


@dataclass(frozen=True)
class TableNode:
    node_id: str
    slot_id: str
    source_ref: str
    origin: Literal["selected_winner", "docling_native_fallback"]
    table: StructuredTable
    selection_ref: str | None
    header: HeaderDecision
    serialized: SerializedTable
    sources: tuple[PageSpan, ...]


ParsedNode: TypeAlias = TextNode | ListNode | TableNode


@dataclass(frozen=True)
class ParsedDocument:
    document_id: str
    document_name: str
    relative_path: str
    file_hash: str
    nodes: tuple[ParsedNode, ...]
    warnings: tuple[ProcessingWarning, ...]
    schema_version: str = "parsed_document_v3"

    def __post_init__(self) -> None:
        if self.schema_version != "parsed_document_v3":
            raise ValueError("unsupported parsed document schema")
        ids = [node.node_id for node in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate parsed node ID")


@dataclass(frozen=True)
class ChunkSource:
    node_id: str
    page_spans: tuple[PageSpan, ...]
    source_text_start: int | None
    source_text_end: int | None
    repeated_context: bool
    context_kind: ContextKind

    def __post_init__(self) -> None:
        if (self.source_text_start is None) != (self.source_text_end is None):
            raise ValueError("source text offsets must both be present or absent")
        if self.source_text_start is not None and not 0 <= self.source_text_start <= self.source_text_end:
            raise ValueError("invalid source text interval")
        if self.repeated_context != (self.context_kind != "none"):
            raise ValueError("repeated source context mismatch")


@dataclass(frozen=True)
class DocumentChunk:
    chunk_id: str
    document_id: str
    chunk_index: int
    kind: ChunkKind
    text: str
    token_count: int | None
    sources: tuple[ChunkSource, ...]
    parent_unit_id: str | None
    fragment_index: int
    fragment_count: int
    regions: tuple[ChunkRegion, ...] = ()    # filled after chunking, see rag/ingest/regions.py

    def __post_init__(self) -> None:
        if not self.text.strip() or type(self.chunk_index) is not int or self.chunk_index < 0:
            raise ValueError("chunk text or index invalid")
        if self.token_count is not None and (type(self.token_count) is not int or self.token_count <= 0):
            raise ValueError("token count invalid")
        if self.fragment_count < 1 or not 0 <= self.fragment_index < self.fragment_count:
            raise ValueError("chunk fragment interval invalid")
        if (self.parent_unit_id is None) != (self.fragment_count == 1):
            raise ValueError("split chunk parent mismatch")


@dataclass(frozen=True)
class ChunkBatch:
    document_id: str
    file_hash: str
    chunks: tuple[DocumentChunk, ...]
    character_count: int
    warnings: tuple[ProcessingWarning, ...]

    def __post_init__(self) -> None:
        if not self.chunks or self.character_count < 0:
            raise ValueError("empty or invalid chunk batch")
        if any(item.document_id != self.document_id or item.chunk_index != index
               for index, item in enumerate(self.chunks)):
            raise ValueError("chunk batch identity or order mismatch")
        if len({item.chunk_id for item in self.chunks}) != len(self.chunks):
            raise ValueError("duplicate chunk ID")


# --- page geometry helpers -------------------------------------------------

COORDINATE_EPSILON_PT = 0.000001


def validate_bbox(box: BoundingBox, page_width: float, page_height: float) -> BoundingBox | None:
    if (not all(math.isfinite(value) and value > 0 for value in (page_width, page_height))
            or (box.x1 - box.x0) * (box.y1 - box.y0) <= 0):
        return None
    if (box.x0 < -COORDINATE_EPSILON_PT or box.y0 < -COORDINATE_EPSILON_PT
            or box.x1 > page_width + COORDINATE_EPSILON_PT
            or box.y1 > page_height + COORDINATE_EPSILON_PT):
        return None
    return BoundingBox(max(0.0, box.x0), max(0.0, box.y0),
                       min(page_width, box.x1), min(page_height, box.y1))


def enclosing_bbox(boxes: tuple[BoundingBox, ...]) -> BoundingBox | None:
    if not boxes:
        return None
    return BoundingBox(min(box.x0 for box in boxes), min(box.y0 for box in boxes),
                       max(box.x1 for box in boxes), max(box.y1 for box in boxes))
