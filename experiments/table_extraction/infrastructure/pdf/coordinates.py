"""将不同工具的页面坐标转换为公共 PyMuPDF 坐标。"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from experiments.table_extraction.domain.models.tables import BoundingBox, CoordinateTransform


from experiments.table_extraction.domain.models.geometry import PageGeometry

def page_geometries(pdf_path: str) -> dict[int, PageGeometry]:
    """从原 PDF 读取 PyMuPDF 页面几何。"""
    import fitz
    with fitz.open(pdf_path) as document:
        return {index + 1: PageGeometry(index + 1, page.rect.width, page.rect.height, page.rotation, page.cropbox != page.mediabox) for index, page in enumerate(document)}


def _valid(box: BoundingBox, page: PageGeometry) -> BoundingBox | None:
    """校验并裁剪微小浮点误差后的公共 bbox。"""
    from experiments.table_extraction.domain.geometry import validate_bbox
    if page.rotation != 0 or page.has_crop_offset:
        # These source frames have not been calibrated per tool. Preserve raw facts;
        # returning None is safer than silently comparing different page spaces.
        return None
    return validate_bbox(box, page.width, page.height)


def from_top_left(box: BoundingBox, page: PageGeometry, source_system: str = "top_left_pt") -> tuple[BoundingBox | None, CoordinateTransform]:
    """校验已处于左上原点的来源 bbox。"""
    return _valid(box, page), CoordinateTransform(source_system, source_page_width=page.width, source_page_height=page.height, scale_x=1.0, scale_y=1.0, page_rotation=page.rotation)


def from_bottom_left(x0: float, y0: float, x1: float, y1: float, page: PageGeometry, source_system: str) -> tuple[BoundingBox | None, CoordinateTransform]:
    """将左下原点 bbox 翻转为公共左上原点。"""
    box = BoundingBox(x0, page.height - y1, x1, page.height - y0)
    return _valid(box, page), CoordinateTransform(source_system, source_page_width=page.width, source_page_height=page.height, scale_x=1.0, scale_y=1.0, y_axis_flipped=True, page_rotation=page.rotation)


def from_pixel_space(points: list[list[float]], width: float, height: float, page: PageGeometry) -> tuple[BoundingBox | None, CoordinateTransform]:
    """将左上原点 PixelSpace 多边形缩放为公共 bbox。"""
    evidence = CoordinateTransform("PixelSpace", source_page_width=width,
                                   source_page_height=height, page_rotation=page.rotation)
    try:
        width, height = float(width), float(height)
        if not all(isfinite(v) and v > 0 for v in (width, height)) or not points:
            return None, evidence
        xs, ys = [float(p[0]) for p in points], [float(p[1]) for p in points]
        if not all(isfinite(v) for v in xs + ys):
            return None, evidence
        scale_x, scale_y = page.width / width, page.height / height
        box = BoundingBox(min(xs)*scale_x, min(ys)*scale_y, max(xs)*scale_x, max(ys)*scale_y)
        return _valid(box, page), CoordinateTransform("PixelSpace", source_page_width=width,
            source_page_height=height, scale_x=scale_x, scale_y=scale_y, page_rotation=page.rotation)
    except (TypeError, ValueError, IndexError, KeyError, OverflowError):
        return None, evidence
