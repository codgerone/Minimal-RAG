"""Frozen extraction fact models and invariants for V2 table candidates."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, TypeAlias

from rag.build_config import TableStrategy, ToolName
from rag.v2.common import BoundingBox, CoordinateTransform, ProcessingWarning


StrategyStatus: TypeAlias = Literal[
    "not_started", "completed_no_tables", "completed_with_tables",
    "completed_with_page_failures", "failed",
]
PageExecutionStatus: TypeAlias = Literal["completed", "failed"]
SpanSource: TypeAlias = Literal["native", "geometry_inferred", "edge_inferred", "unavailable"]
CellRole: TypeAlias = Literal["column_header", "row_header", "row_section", "body", "unknown"]

LEGAL_STRATEGIES: dict[ToolName, tuple[TableStrategy, ...]] = {
    "pymupdf": ("lines", "lines_strict", "text"),
    "camelot": ("lattice", "stream", "network", "hybrid"),
    "docling": ("accurate",),
    "unstructured": ("hi_res",),
}


@dataclass(frozen=True)
class PageExecution:
    page_number: int
    status: PageExecutionStatus
    candidate_ids: tuple[str, ...]
    error_type: str | None
    error_message: str | None

    def __post_init__(self) -> None:
        if self.page_number <= 0:
            raise ValueError("PageExecution page_number 必须为正数。")
        if self.status == "completed":
            if self.error_type is not None or self.error_message is not None:
                raise ValueError("completed 页面不得包含错误。")
        elif self.candidate_ids or not self.error_type or not self.error_message:
            raise ValueError("failed 页面必须无候选且包含错误。")


@dataclass(frozen=True)
class StrategyExecution:
    tool: ToolName
    strategy: TableStrategy
    status: StrategyStatus
    pages: tuple[PageExecution, ...]
    candidate_ids: tuple[str, ...]
    error_type: str | None
    error_message: str | None

    def __post_init__(self) -> None:
        if self.strategy not in LEGAL_STRATEGIES[self.tool]:
            raise ValueError("非法工具—策略组合。")
        page_numbers = tuple(page.page_number for page in self.pages)
        if page_numbers != tuple(sorted(set(page_numbers))):
            raise ValueError("StrategyExecution pages 必须按页码严格递增。")
        successful_ids = tuple(
            candidate_id for page in self.pages if page.status == "completed"
            for candidate_id in page.candidate_ids
        )
        completed = tuple(page for page in self.pages if page.status == "completed")
        failed = tuple(page for page in self.pages if page.status == "failed")
        has_error = bool(self.error_type and self.error_message)
        if self.status == "not_started":
            valid = not self.pages and not self.candidate_ids and has_error
        elif self.status == "failed":
            valid = (not completed and not self.candidate_ids and has_error)
        elif self.status == "completed_no_tables":
            valid = bool(completed) and not failed and not successful_ids and not has_error
        elif self.status == "completed_with_tables":
            valid = bool(completed and successful_ids) and not failed and not has_error
        else:
            valid = bool(completed and failed) and not has_error
        if not valid or self.candidate_ids != successful_ids:
            raise ValueError("StrategyExecution 状态组合不合法。")


@dataclass(frozen=True)
class TableRegion:
    page_number: int | None
    page_width: float | None
    page_height: float | None
    bbox: BoundingBox | None
    source_ref: str
    coordinate_transform: CoordinateTransform | None
    unavailable_reason: Literal[
        "missing_bbox", "coordinate_conversion_failed", "invalid_bbox", "invalid_page_geometry",
    ] | None = None

    def __post_init__(self) -> None:
        if not self.source_ref:
            raise ValueError("TableRegion source_ref 不能为空。")
        located = (self.page_number, self.page_width, self.page_height)
        if any(value is None for value in located):
            if any(value is not None for value in located) or self.bbox is not None:
                raise ValueError("TableRegion 不得保存部分页面定位。")
        elif self.page_number <= 0 or self.page_width <= 0 or self.page_height <= 0:
            raise ValueError("TableRegion 页面几何无效。")
        if self.bbox is not None and self.unavailable_reason is not None:
            raise ValueError("已定位 TableRegion 不得包含 unavailable_reason。")
        if self.bbox is None and self.unavailable_reason is None:
            raise ValueError("无 bbox TableRegion 必须包含 unavailable_reason。")


@dataclass(frozen=True)
class TableCell:
    cell_id: str
    text: str | None
    start_row_offset_idx: int | None
    end_row_offset_idx: int | None
    start_col_offset_idx: int | None
    end_col_offset_idx: int | None
    row_span: int | None
    col_span: int | None
    bbox: BoundingBox | None
    roles: tuple[CellRole, ...]
    span_source: SpanSource
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.cell_id:
            raise ValueError("TableCell cell_id 不能为空。")
        values = (
            self.start_row_offset_idx, self.end_row_offset_idx,
            self.start_col_offset_idx, self.end_col_offset_idx,
            self.row_span, self.col_span,
        )
        if all(value is None for value in values):
            if self.span_source != "unavailable":
                raise ValueError("无法定位的 TableCell 必须标为 unavailable。")
            return
        if any(value is None for value in values):
            raise ValueError("TableCell 网格字段必须同时存在或同时为空。")
        row0, row1, col0, col1, row_span, col_span = values
        assert None not in values
        if row0 < 0 or col0 < 0 or row1 <= row0 or col1 <= col0:
            raise ValueError("TableCell 半开区间无效。")
        if row_span != row1 - row0 or col_span != col1 - col0:
            raise ValueError("TableCell span 与区间不一致。")
        if self.span_source == "unavailable":
            raise ValueError("已定位 TableCell 不得标为 unavailable。")


@dataclass(frozen=True)
class GridPosition:
    row_index: int
    column_index: int
    reason: Literal["missing_physical_cell"] = "missing_physical_cell"


@dataclass(frozen=True)
class TableCandidate:
    candidate_id: str
    tool: ToolName
    strategy: TableStrategy
    source_ref: str
    regions: tuple[TableRegion, ...]
    row_count: int | None
    column_count: int | None
    x_boundaries: tuple[float, ...] | None
    y_boundaries: tuple[float, ...] | None
    cells: tuple[TableCell, ...]
    uncovered_grid_positions: tuple[GridPosition, ...]
    unplaced_text: str | None
    warnings: tuple[ProcessingWarning, ...]

    def __post_init__(self) -> None:
        if self.strategy not in LEGAL_STRATEGIES[self.tool]:
            raise ValueError("非法工具—策略组合。")
        dimensions = (self.row_count, self.column_count)
        boundaries = (self.x_boundaries, self.y_boundaries)
        if all(value is None for value in dimensions):
            if any(value is not None for value in boundaries):
                raise ValueError("无逻辑网格候选不得包含几何 boundaries。")
            if any(cell.start_row_offset_idx is not None for cell in self.cells):
                raise ValueError("无网格候选不得包含已定位 cell。")
            return
        if any(value is None for value in dimensions):
            raise ValueError("候选逻辑网格维度必须同时存在或同时为空。")
        assert self.row_count is not None and self.column_count is not None
        if self.row_count <= 0 or self.column_count <= 0:
            raise ValueError("候选网格维度必须为正。")
        if any(value is None for value in boundaries) and not all(value is None for value in boundaries):
            raise ValueError("候选 x/y boundaries 必须同时存在或同时为空。")
        if self.x_boundaries is not None and self.y_boundaries is not None:
            if len(self.x_boundaries) != self.column_count + 1 or len(self.y_boundaries) != self.row_count + 1:
                raise ValueError("候选 boundaries 数量与网格不一致。")
            for axis_boundaries in (self.x_boundaries, self.y_boundaries):
                if not all(math.isfinite(value) for value in axis_boundaries) or any(
                    right <= left for left, right in zip(axis_boundaries, axis_boundaries[1:])
                ):
                    raise ValueError("候选 boundaries 必须有限且严格递增。")
        covered: set[tuple[int, int]] = set()
        for cell in self.cells:
            if cell.start_row_offset_idx is None:
                continue
            assert cell.end_row_offset_idx is not None
            assert cell.start_col_offset_idx is not None and cell.end_col_offset_idx is not None
            if cell.end_row_offset_idx > self.row_count or cell.end_col_offset_idx > self.column_count:
                raise ValueError("TableCell 区间超出候选网格。")
            positions = {
                (row, col)
                for row in range(cell.start_row_offset_idx, cell.end_row_offset_idx)
                for col in range(cell.start_col_offset_idx, cell.end_col_offset_idx)
            }
            if covered & positions:
                raise ValueError("TableCandidate cells 存在重叠覆盖。")
            covered.update(positions)
        uncovered = {(item.row_index, item.column_index) for item in self.uncovered_grid_positions}
        if any(row < 0 or col < 0 or row >= self.row_count or col >= self.column_count for row, col in uncovered):
            raise ValueError("未覆盖位置超出候选网格。")
        if covered & uncovered or covered | uncovered != {
            (row, col) for row in range(self.row_count) for col in range(self.column_count)
        }:
            raise ValueError("候选 cell 与未覆盖位置未形成完整且互斥的网格事实。")


@dataclass(frozen=True)
class ExtractionReport:
    source_pdf: str
    file_hash: str
    executions: tuple[StrategyExecution, ...]
    candidates: tuple[TableCandidate, ...]
    warnings: tuple[ProcessingWarning, ...]
    schema_version: Literal["table_extraction_v1"] = "table_extraction_v1"

    def __post_init__(self) -> None:
        ids = tuple(candidate.candidate_id for candidate in self.candidates)
        execution_ids = tuple(
            candidate_id for execution in self.executions
            for candidate_id in execution.candidate_ids
        )
        if len(ids) != len(set(ids)) or set(ids) != set(execution_ids):
            raise ValueError("ExtractionReport candidate 集合与 executions 不一致。")
