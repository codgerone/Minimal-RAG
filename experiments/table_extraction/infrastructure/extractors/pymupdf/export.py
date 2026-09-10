"""写出 PyMuPDF 的原始快照、规范化表格和可视化文件。"""

from __future__ import annotations

import csv
import html
import json
from pathlib import Path
from typing import Any

from experiments.table_extraction.infrastructure.extractors.pymupdf.normalizer import NormalizedStrategyResult
from experiments.table_extraction.infrastructure.artifacts.writers import _write_csv, _write_json
from experiments.table_extraction.domain.models.tables import TableCandidate, TableCell

def export_strategy(raw_strategy: dict[str, Any], normalized: NormalizedStrategyResult, output_dir: Path, *, render_table) -> None:
    """写出一个策略的原始结果、统一模型、CSV 与 HTML。"""
    strategy = raw_strategy["strategy"]
    _write_json(output_dir / "raw" / f"{strategy}.json", raw_strategy)
    normalized_dir = output_dir / "normalized" / strategy
    tables_dir = normalized_dir / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)
    for candidate in normalized.candidates:
        rows = normalized.rows_by_candidate[candidate.candidate_id]
        csv_path = tables_dir / Path(candidate.artifacts.csv_file or "table.csv").name
        html_path = tables_dir / Path(candidate.artifacts.html_file or "table.html").name
        _write_csv(csv_path, rows)
        html_path.write_text(render_table(candidate), encoding="utf-8")
    _write_json(normalized_dir / "tables.json", {
        "tool": "pymupdf", "strategy": strategy,
        "table_count": len(normalized.candidates),
        "tables": [candidate.to_dict() for candidate in normalized.candidates],
    })


def export_manifest(metadata: dict[str, Any], output_dir: Path) -> Path:
    """写出一次 PyMuPDF 实验的来源和策略统计信息。"""
    path = output_dir / "manifest.json"
    _write_json(path, metadata)
    return path


