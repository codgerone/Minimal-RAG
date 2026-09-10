"""候选准入使用的 bbox 校验、归并和覆盖率计算。"""

from __future__ import annotations

import math
from typing import Any, Iterable

from experiments.table_extraction.domain.admission_config import COORD_EPSILON_PT
from experiments.table_extraction.domain.models.selection import SlotMatchMetrics
from experiments.table_extraction.domain.models.tables import BoundingBox


def bbox_from_dict(value: Any) -> BoundingBox | None:
    """把 JSON bbox 转换为矩形；缺字段或非数值时返回 None。"""
    if not isinstance(value, dict):
        return None
    try:
        return BoundingBox(*(float(value[key]) for key in ("x0", "y0", "x1", "y1")))
    except (KeyError, TypeError, ValueError):
        return None


def validate_bbox(
    box: BoundingBox,
    page_width: float,
    page_height: float,
    epsilon: float = COORD_EPSILON_PT,
) -> BoundingBox | None:
    """校验公共 bbox，并仅裁剪不超过 epsilon 的页面边界误差。"""
    values = (box.x0, box.y0, box.x1, box.y1, page_width, page_height)
    if not all(math.isfinite(value) for value in values):
        return None
    if page_width <= 0 or page_height <= 0 or box.x1 <= box.x0 or box.y1 <= box.y0:
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
    return clipped if clipped.x1 > clipped.x0 and clipped.y1 > clipped.y0 else None


def enclosing_bbox(boxes: Iterable[BoundingBox]) -> BoundingBox | None:
    """返回包含全部输入矩形的最小外接矩形。"""
    values = list(boxes)
    if not values:
        return None
    return BoundingBox(
        min(box.x0 for box in values), min(box.y0 for box in values),
        max(box.x1 for box in values), max(box.y1 for box in values),
    )


def bbox_area(box: BoundingBox) -> float:
    """返回有效矩形的面积。"""
    return max(0.0, box.x1 - box.x0) * max(0.0, box.y1 - box.y0)


def slot_match_metrics(candidate: BoundingBox, slot: BoundingBox) -> SlotMatchMetrics:
    """计算 candidate-slot 的交集、IoU 和双向覆盖率。"""
    width = max(0.0, min(candidate.x1, slot.x1) - max(candidate.x0, slot.x0))
    height = max(0.0, min(candidate.y1, slot.y1) - max(candidate.y0, slot.y0))
    intersection = width * height
    candidate_area = bbox_area(candidate)
    slot_area = bbox_area(slot)
    union = candidate_area + slot_area - intersection
    if candidate_area <= 0 or slot_area <= 0 or union <= 0:
        raise ValueError("slot_match_metrics requires positive-area boxes")
    return SlotMatchMetrics(
        intersection_area=intersection,
        union_area=union,
        iou=intersection / union,
        candidate_coverage=intersection / candidate_area,
        slot_coverage=intersection / slot_area,
    )
