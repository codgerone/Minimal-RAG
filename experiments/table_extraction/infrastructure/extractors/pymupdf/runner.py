"""调用 PyMuPDF 的三种策略并保留原始表格快照。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from experiments.table_extraction.infrastructure.extractors.pymupdf.config import STRATEGIES


def _rect_values(rect: Any) -> list[float] | None:
    """将 PyMuPDF 矩形转换为稳定的 JSON 坐标数组。"""
    if rect is None:
        return None
    return [float(value) for value in rect]


def _snapshot_table(table: Any, table_index: int) -> dict[str, Any]:
    """保留 TableFinder 返回表格的公开字段和原始二维文本。"""
    rows = [
        {
            "bbox": _rect_values(row.bbox),
            "cells": [_rect_values(cell) for cell in row.cells],
        }
        for row in table.rows
    ]
    return {
        "table_index": table_index,
        "bbox": _rect_values(table.bbox),
        "row_count": len(rows),
        "column_count": table.col_count,
        "cells": [_rect_values(cell) for cell in table.cells],
        "rows": rows,
        "extract": table.extract(),
        "header": {
            "bbox": _rect_values(table.header.bbox),
            "cells": [_rect_values(cell) for cell in table.header.cells],
            "names": table.header.names,
            "external": table.header.external,
        },
    }


def extract_raw_strategies(pdf_path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """对 PDF 的每页分别运行三种策略，返回按策略组织的原始快照。"""
    import fitz

    raw_by_strategy: dict[str, dict[str, Any]] = {}
    with fitz.open(pdf_path) as document:
        for strategy_name, settings in STRATEGIES.items():
            pages: list[dict[str, Any]] = []
            for page_index, page in enumerate(document):
                try:
                    finder = page.find_tables(**settings)
                    tables = [_snapshot_table(table, index + 1) for index, table in enumerate(finder.tables)]
                    words = [list(word) for word in page.get_text("words")]
                    error = None
                except Exception as exc:
                    tables, words = [], []
                    error = f"{type(exc).__name__}: {exc}"
                pages.append(
                    {
                        "page_number": page_index + 1,
                        "bbox": _rect_values(page.rect),
                        "words": words,
                        "status": "failed" if error else "success",
                        "error": error,
                        "rotation": page.rotation,
                        "has_crop_offset": page.cropbox != page.mediabox,
                        "tables": tables,
                    }
                )
            raw_by_strategy[strategy_name] = {
                "tool": "pymupdf",
                "strategy": strategy_name,
                "configuration": settings,
                "pymupdf_version": fitz.VersionBind,
                "source_file": str(pdf_path),
                "page_count": len(document),
                "pages": pages,
            }

    metadata = {
        "tool": "pymupdf",
        "source_file": str(pdf_path),
        "page_count": next(iter(raw_by_strategy.values()))["page_count"],
        "strategies": {
            name: sum(len(page["tables"]) for page in result["pages"])
            for name, result in raw_by_strategy.items()
        },
    }
    return raw_by_strategy, metadata
