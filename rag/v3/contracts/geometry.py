"""Page coordinate validation shared by PDF fact adapters."""

from __future__ import annotations

import math

from rag.v3.contracts.documents import BoundingBox

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
