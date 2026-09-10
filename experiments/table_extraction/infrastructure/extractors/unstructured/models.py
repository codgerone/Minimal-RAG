"""Unstructured 版面分析实验的数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class UnstructuredRunResult:
    """记录一次 Unstructured 版面分析的运行状态。"""

    status: str
    elements: list[Any] = field(default_factory=list)
    error: str | None = None
    run_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class UnstructuredTableIndexRecord:
    """记录 Table 在全量 Element 输出中的位置及其便捷查看文件。"""

    table_id: str
    element_index: int
    html_file: str | None
    text_file: str | None
    raw_reference: str
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        """转换为可写入 JSON 的字典。"""
        return {
            "table_id": self.table_id,
            "element_index": self.element_index,
            "html_file": self.html_file,
            "text_file": self.text_file,
            "raw_reference": self.raw_reference,
            "warnings": self.warnings,
        }
