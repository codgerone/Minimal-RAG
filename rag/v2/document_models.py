"""Owned V2 layout and parsed-document contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias

from rag.build_config import TableStrategy, ToolName
from rag.models import SourceDocument
from rag.v2.common import PageSpan, ProcessingWarning
from rag.v2.table_header import HeaderDecision
from rag.v2.table_models import GridPosition, TableCell
from rag.v2.table_models import PageExecution, TableCandidate
from rag.v2.table_selection import TableSlot
from rag.v2.table_serialization import SerializedTable


TextKind: TypeAlias = Literal[
    "title", "paragraph", "header", "footer", "footnote", "caption", "table_note", "other_text",
]


@dataclass(frozen=True)
class DoclingRawArtifact:
    relative_path: str
    schema_name: str
    schema_version: str
    page_count: int
    text_count: int
    table_count: int
    picture_count: int

    def __post_init__(self) -> None:
        path = Path(self.relative_path)
        counts = (self.page_count, self.text_count, self.table_count, self.picture_count)
        if not self.relative_path or path.is_absolute() or ".." in path.parts:
            raise ValueError("Docling raw 路径必须是 build 根目录内的相对路径。")
        if self.page_count <= 0 or any(value < 0 for value in counts[1:]):
            raise ValueError("Docling raw 文档统计无效。")


@dataclass(frozen=True)
class LayoutParseResult:
    raw_document: DoclingRawArtifact
    layout_document: "LayoutDocument"
    docling_table_candidates: tuple[TableCandidate, ...]
    page_executions: tuple[PageExecution, ...]


@dataclass(frozen=True)
class LayoutText:
    element_id: str
    docling_ref: str
    kind: TextKind
    text: str
    sources: tuple[PageSpan, ...]


@dataclass(frozen=True)
class LayoutListItem:
    item_id: str
    docling_ref: str
    text: str
    level: int
    ordinal: str | None
    parent_item_id: str | None
    sources: tuple[PageSpan, ...]


@dataclass(frozen=True)
class LayoutList:
    element_id: str
    docling_refs: tuple[str, ...]
    items: tuple[LayoutListItem, ...]
    sources: tuple[PageSpan, ...]

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for item in self.items:
            if item.item_id in seen or item.level < 0:
                raise ValueError("LayoutList item 身份必须唯一且 level 非负。")
            if item.level == 0 and item.parent_item_id is not None:
                raise ValueError("顶层 List item 不得包含父项。")
            if item.level > 0 and item.parent_item_id not in seen:
                raise ValueError("嵌套 List item 必须引用同 List 中更早的父项。")
            seen.add(item.item_id)


@dataclass(frozen=True)
class LayoutTablePlaceholder:
    element_id: str
    docling_ref: str
    slot_id: str
    sources: tuple[PageSpan, ...]


LayoutElement: TypeAlias = LayoutText | LayoutList | LayoutTablePlaceholder


@dataclass(frozen=True)
class LayoutDocument:
    document_id: str
    page_count: int
    elements: tuple[LayoutElement, ...]
    table_slots: tuple[TableSlot, ...]
    warnings: tuple[ProcessingWarning, ...]
    schema_version: Literal["layout_document_v1"] = "layout_document_v1"

    def __post_init__(self) -> None:
        if self.page_count <= 0:
            raise ValueError("LayoutDocument.page_count 必须为正数。")
        element_ids = tuple(item.element_id for item in self.elements)
        slot_ids = tuple(item.slot_id for item in self.table_slots)
        placeholders = tuple(item for item in self.elements if isinstance(item, LayoutTablePlaceholder))
        if len(element_ids) != len(set(element_ids)) or len(slot_ids) != len(set(slot_ids)):
            raise ValueError("LayoutDocument element_id/slot_id 必须唯一。")
        if tuple(item.slot_id for item in placeholders) != slot_ids:
            raise ValueError("每个 table slot 必须按阅读顺序恰有一个 placeholder。")


@dataclass(frozen=True)
class TextNode:
    node_id: str
    kind: TextKind
    text: str
    docling_ref: str
    sources: tuple[PageSpan, ...]


@dataclass(frozen=True)
class ListNode:
    node_id: str
    items: tuple[LayoutListItem, ...]
    docling_refs: tuple[str, ...]
    sources: tuple[PageSpan, ...]


@dataclass(frozen=True)
class StructuredTable:
    table_id: str
    tool: ToolName
    strategy: TableStrategy
    row_count: int | None
    column_count: int | None
    cells: tuple[TableCell, ...]
    uncovered_grid_positions: tuple[GridPosition, ...]
    unplaced_text: str | None
    sources: tuple[PageSpan, ...]
    warnings: tuple[ProcessingWarning, ...]


@dataclass(frozen=True)
class TableNode:
    node_id: str
    slot_id: str
    docling_ref: str
    origin: Literal["selected_winner", "docling_native_fallback"]
    table: StructuredTable
    selection_ref: str | None
    header: HeaderDecision
    serialized: SerializedTable
    sources: tuple[PageSpan, ...]

    def __post_init__(self) -> None:
        if (self.origin == "selected_winner") != (self.selection_ref is not None):
            raise ValueError("仅 winner TableNode 可以包含 selection_ref。")


ParsedNode: TypeAlias = TextNode | ListNode | TableNode


@dataclass(frozen=True)
class ParsedDocument:
    document_id: str
    document_name: str
    relative_path: str
    file_hash: str
    nodes: tuple[ParsedNode, ...]
    warnings: tuple[ProcessingWarning, ...]
    schema_version: Literal["parsed_document_v1"] = "parsed_document_v1"

    def __post_init__(self) -> None:
        ids = tuple(item.node_id for item in self.nodes)
        if len(ids) != len(set(ids)):
            raise ValueError("ParsedDocument.node_id 必须唯一。")
