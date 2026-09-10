"""跨工具复用的表格候选数据模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal


CellRole = Literal["column_header", "row_header", "row_section", "body", "unknown"]
SpanSource = Literal["native", "geometry_inferred", "edge_inferred", "unavailable"]


@dataclass(frozen=True)
class BoundingBox:
    """表示 PDF 坐标系中的矩形边界。"""

    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class TableRegion:
    """记录候选表格在一个页面上的来源区域。"""

    page_number: int
    page_width: float | None
    page_height: float | None
    bbox: BoundingBox | None
    source_ref: str
    coordinate_transform: "CoordinateTransform | None" = None


@dataclass(frozen=True)
class CoordinateTransform:
    """记录来源坐标到公共页面坐标的转换证据。"""

    source_system: str
    target_system: str = "pymupdf_page_top_left_pt_v1"
    source_page_width: float | None = None
    source_page_height: float | None = None
    scale_x: float | None = None
    scale_y: float | None = None
    y_axis_flipped: bool = False
    page_rotation: int | None = None


@dataclass(frozen=True)
class GridPosition:
    """记录未被物理单元格覆盖的逻辑网格位置。"""

    row_index: int
    column_index: int
    reason: str


@dataclass
class TableCell:
    """记录一个物理单元格及其在逻辑网格中的覆盖范围。"""

    cell_id: str
    bbox: BoundingBox | None
    row_span: int | None
    col_span: int | None
    start_row_offset_idx: int | None
    end_row_offset_idx: int | None
    start_col_offset_idx: int | None
    end_col_offset_idx: int | None
    text: str | None
    roles: list[CellRole]
    span_source: SpanSource
    source_ref: str
    source_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TableArtifacts:
    """记录便于人工查看的派生文件路径。"""

    csv_file: str | None = None
    html_file: str | None = None


@dataclass
class TableCandidate:
    """统一描述某工具、某策略识别出的一张表格候选。"""

    candidate_id: str
    tool: str
    strategy: str | None
    source_ref: str
    regions: list[TableRegion]
    row_count: int | None
    column_count: int | None
    x_boundaries: list[float] | None
    y_boundaries: list[float] | None
    cells: list[TableCell]
    uncovered_grid_positions: list[GridPosition]
    artifacts: TableArtifacts
    warnings: list[str] = field(default_factory=list)
    unplaced_text: str | None = None

    def to_dict(self) -> dict:
        """转换为可直接写入 JSON 的字典。"""
        return asdict(self)
