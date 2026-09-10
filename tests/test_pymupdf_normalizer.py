"""验证 PyMuPDF 单元格跨度恢复。"""

from experiments.table_extraction.infrastructure.extractors.pymupdf.normalizer import normalize_strategy


def test_normalizer_restores_row_span_from_cell_bbox() -> None:
    """跨两行的 MODEL 单元格应覆盖两个逻辑行。"""
    raw_strategy = {
        "strategy": "lines_strict",
        "pages": [{
            "page_number": 1,
            "bbox": [0, 0, 300, 200],
            "words": [],
            "tables": [{
                "bbox": [0, 0, 300, 100],
                "row_count": 2,
                "column_count": 2,
                "rows": [
                    {"bbox": [0, 0, 300, 50], "cells": [[0, 0, 100, 100], [100, 0, 300, 50]]},
                    {"bbox": [0, 50, 300, 100], "cells": [None, [100, 50, 300, 100]]},
                ],
                "extract": [["MODEL", "Delivery"], [None, "2025-09-25"]],
                "header": {"bbox": [0, 0, 300, 100], "cells": [], "names": [], "external": False},
            }],
        }],
    }

    result = normalize_strategy(raw_strategy)
    model_cell = next(cell for cell in result.candidates[0].cells if cell.text == "MODEL")

    assert (model_cell.start_row_offset_idx, model_cell.end_row_offset_idx) == (0, 2)
    assert (model_cell.start_col_offset_idx, model_cell.end_col_offset_idx) == (0, 1)
    assert (model_cell.row_span, model_cell.col_span) == (2, 1)
    assert model_cell.span_source == "geometry_inferred"
    assert result.candidates[0].uncovered_grid_positions == []
