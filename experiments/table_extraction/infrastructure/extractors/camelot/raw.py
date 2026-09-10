"""将 Camelot TableList 的公开字段保存为可追溯 JSON 快照。"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any


def _json_value(value: Any) -> Any:
    """递归转换 Camelot 公开值，无法忠实 JSON 化时保留字符串表示。"""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if is_dataclass(value):
        return _json_value(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if hasattr(value, "to_dict"):
        return _json_value(value.to_dict())
    return repr(value)


def _cell_snapshot(cell: Any) -> dict[str, Any]:
    """提取一个 Camelot Cell 的文本、几何和边界状态。"""
    return {
        "x1": getattr(cell, "x1", None), "y1": getattr(cell, "y1", None),
        "x2": getattr(cell, "x2", None), "y2": getattr(cell, "y2", None),
        "text": getattr(cell, "text", None),
        "left": getattr(cell, "left", None), "right": getattr(cell, "right", None),
        "top": getattr(cell, "top", None), "bottom": getattr(cell, "bottom", None),
        "hspan": getattr(cell, "hspan", None), "vspan": getattr(cell, "vspan", None),
    }


def table_snapshot(table: Any, index: int) -> dict[str, Any]:
    """提取一张 Camelot Table 的公开解析事实和完整 cell 网格。"""
    return {
        "index": index,
        "flavor": getattr(table, "flavor", None), "page": getattr(table, "page", None),
        "order": getattr(table, "order", None), "shape": _json_value(getattr(table, "shape", None)),
        "accuracy": getattr(table, "accuracy", None), "whitespace": getattr(table, "whitespace", None),
        "filename": getattr(table, "filename", None), "rotation": getattr(table, "rotation", None),
        "parse": _json_value(getattr(table, "parse", None)),
        "parse_details": _json_value(getattr(table, "parse_details", None)),
        "textlines": _json_value(getattr(table, "textlines", None)),
        "rows": _json_value(getattr(table, "rows", None)), "cols": _json_value(getattr(table, "cols", None)),
        "dataframe": _json_value(getattr(table, "df", None)),
        "cells": [[_cell_snapshot(cell) for cell in row] for row in getattr(table, "cells", [])],
        "parsing_report": _json_value(getattr(table, "parsing_report", None)),
    }


def flavor_snapshot(flavor: str, status: str, error: str | None, metadata: dict[str, Any], tables: list[Any]) -> dict[str, Any]:
    """组织一个 flavor 的原始 TableList 快照。"""
    return {
        "tool": "camelot", "flavor": flavor, "status": status, "error": error,
        "run_metadata": _json_value(metadata),
        "tables": [table_snapshot(table, index) for index, table in enumerate(tables)],
    }
