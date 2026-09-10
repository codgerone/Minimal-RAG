from dataclasses import dataclass

@dataclass(frozen=True)
class PageGeometry:
    """描述公共坐标系中的有效页面几何。"""

    page_number: int
    width: float
    height: float
    rotation: int
    has_crop_offset: bool = False


