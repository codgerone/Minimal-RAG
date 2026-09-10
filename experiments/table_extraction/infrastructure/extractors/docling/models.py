"""Docling 版面分析实验的数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DoclingRunResult:
    """记录一次 Docling PDF 转换的运行状态。"""

    status: str
    document: Any | None = None
    error: str | None = None
    run_metadata: dict[str, Any] = field(default_factory=dict)
    attempt_documents: list[dict[str, Any]] = field(default_factory=list)
