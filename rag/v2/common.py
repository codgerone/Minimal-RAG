"""V2 common geometry, provenance, and warning models."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, TypeAlias


COORDINATE_EPSILON_PT = 0.000001
COORDINATE_SPACE = "pymupdf_page_top_left_pt_v1"

WarningCode: TypeAlias = Literal[
    "source_location_unavailable", "detached_text_recovered", "detached_list_item",
    "unknown_docling_text_label", "unsupported_nontext_item",
    "orphan_nested_list_group", "empty_text_node", "optional_tool_page_failed",
    "optional_tool_unavailable", "artifact_cleanup_failed", "legacy_config_ignored",
]
ProcessingStage: TypeAlias = Literal[
    "configuration", "docling_layout_parsing", "layout_mapping", "table_extraction",
    "table_admission", "table_grouping", "table_scoring", "parsed_document_fusion",
    "table_header_detection", "table_serialization", "document_text_serialization",
    "structured_chunking", "artifact_staging", "artifact_cleanup", "index_publication",
]


@dataclass(frozen=True)
class BoundingBox:
    x0: float
    y0: float
    x1: float
    y1: float
    coordinate_space: Literal["pymupdf_page_top_left_pt_v1"] = COORDINATE_SPACE

    def __post_init__(self) -> None:
        if not all(math.isfinite(value) for value in (self.x0, self.y0, self.x1, self.y1)):
            raise ValueError("BoundingBox 坐标必须是有限数。")
        if self.x1 < self.x0 or self.y1 < self.y0:
            raise ValueError("BoundingBox 边界顺序无效。")

    @property
    def area(self) -> float:
        return (self.x1 - self.x0) * (self.y1 - self.y0)


@dataclass(frozen=True)
class CoordinateTransform:
    source_origin: Literal["top_left", "bottom_left"]
    source_units: Literal["pt", "px"]
    scale_x: float
    scale_y: float
    page_height_before_scale: float

    def __post_init__(self) -> None:
        values = (self.scale_x, self.scale_y, self.page_height_before_scale)
        if not all(math.isfinite(value) and value > 0 for value in values):
            raise ValueError("CoordinateTransform 比例和页面高度必须为有限正数。")


@dataclass(frozen=True)
class PageSpan:
    page_number: int
    bbox: BoundingBox | None
    source_ref: str

    def __post_init__(self) -> None:
        if self.page_number <= 0 or not self.source_ref:
            raise ValueError("PageSpan 页码必须为正且 source_ref 不能为空。")


@dataclass(frozen=True)
class ProcessingWarning:
    code: WarningCode
    stage: ProcessingStage
    message: str
    source_refs: tuple[str, ...]


def validate_bbox(
    box: BoundingBox, page_width: float, page_height: float,
    epsilon: float = COORDINATE_EPSILON_PT,
) -> BoundingBox | None:
    values = (page_width, page_height)
    if not all(math.isfinite(value) and value > 0 for value in values) or box.area <= 0:
        return None
    if (
        box.x0 < -epsilon or box.y0 < -epsilon
        or box.x1 > page_width + epsilon or box.y1 > page_height + epsilon
    ):
        return None
    clipped = BoundingBox(
        max(0.0, box.x0), max(0.0, box.y0),
        min(page_width, box.x1), min(page_height, box.y1),
    )
    return clipped if clipped.area > 0 else None


def enclosing_bbox(boxes: tuple[BoundingBox, ...]) -> BoundingBox | None:
    if not boxes:
        return None
    return BoundingBox(
        min(item.x0 for item in boxes), min(item.y0 for item in boxes),
        max(item.x1 for item in boxes), max(item.y1 for item in boxes),
    )

