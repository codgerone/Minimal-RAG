"""Camelot 实验结果的数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CamelotRunResult:
    """记录一种 Camelot parser 的完整运行状态。"""

    flavor: str
    status: str
    tables: list[Any] = field(default_factory=list)
    error: str | None = None
    run_metadata: dict[str, Any] = field(default_factory=dict)
