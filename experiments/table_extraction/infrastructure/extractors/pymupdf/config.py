"""PyMuPDF 表格提取策略与跨度容差。"""

STRATEGIES = {
    "lines": {"vertical_strategy": "lines", "horizontal_strategy": "lines"},
    "lines_strict": {"vertical_strategy": "lines_strict", "horizontal_strategy": "lines_strict"},
    "text": {"vertical_strategy": "text", "horizontal_strategy": "text"},
}

# 用于从单元格边界恢复隐含行、列跨度的坐标容差（单位：pt）。
SPAN_BOUNDARY_TOLERANCE = 2.0

