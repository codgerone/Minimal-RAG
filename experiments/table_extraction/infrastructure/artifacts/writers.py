from __future__ import annotations
import csv
import json
from pathlib import Path
from typing import Any

def _write_json(path: Path, value: Any) -> None:
    """以 UTF-8 和缩进格式写入 JSON 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

def _write_csv(path: Path, rows: list[list[str | None]]) -> None:
    """将原始二维提取文本写为方便检查的 CSV。"""
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerows(rows)

