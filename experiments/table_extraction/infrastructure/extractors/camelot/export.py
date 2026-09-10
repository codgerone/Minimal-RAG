"""按统一目录导出 Camelot 原始快照与规范化表格。"""

from __future__ import annotations

import json
from pathlib import Path
from ..normalization import normalize_page_groups, apply_page_failures

from experiments.table_extraction.infrastructure.extractors.camelot.models import CamelotRunResult
from experiments.table_extraction.infrastructure.extractors.camelot.normalizer import normalize_flavor
from experiments.table_extraction.infrastructure.extractors.camelot.raw import flavor_snapshot
from experiments.table_extraction.infrastructure.artifacts.writers import _write_csv, _write_json


def export_results(results: list[CamelotRunResult], output_dir: Path, pdf_path: Path, *, render_table) -> Path:
    """导出每个 flavor 的 raw、normalized 结果和唯一 manifest。"""
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    manifest_runs: list[dict[str, object]] = []
    for result in results:
        _write_json(raw_dir / f"{result.flavor}.json", flavor_snapshot(
            result.flavor, result.status, result.error, result.run_metadata, result.tables,
        ))
        normalized_count = warning_count = 0
        if result.status == "success":
            groups = {}
            for index, table in enumerate(result.tables):
                groups.setdefault((int(table.page),), []).append(index)
            normalized = normalize_page_groups(groups, lambda indices: normalize_flavor(
                [result.tables[i] for i in indices], result.flavor, str(pdf_path), table_indices=indices))
            apply_page_failures(result.run_metadata, normalized.failed_pages)
            strategy_dir = output_dir / "normalized" / result.flavor
            tables_dir = strategy_dir / "tables"
            tables_dir.mkdir(parents=True, exist_ok=True)
            for candidate in normalized.candidates:
                _write_csv(tables_dir / Path(candidate.artifacts.csv_file or "table.csv").name, normalized.rows_by_candidate[candidate.candidate_id])
                (tables_dir / Path(candidate.artifacts.html_file or "table.html").name).write_text(render_table(candidate), encoding="utf-8")
            _write_json(strategy_dir / "tables.json", {
                "tool": "camelot", "strategy": result.flavor,
                "table_count": len(normalized.candidates),
                "tables": [candidate.to_dict() for candidate in normalized.candidates],
            })
            normalized_count = len(normalized.candidates)
            warning_count = sum(len(candidate.warnings) for candidate in normalized.candidates)
        _write_json(raw_dir / f"{result.flavor}.json", flavor_snapshot(
            result.flavor, result.status, result.error, result.run_metadata, result.tables))
        manifest_runs.append({
            "flavor": result.flavor, "status": result.status, "error": result.error,
            "run_metadata": result.run_metadata, "raw_path": f"raw/{result.flavor}.json",
            "normalized_path": f"normalized/{result.flavor}/tables.json" if result.status == "success" else None,
            "native_table_count": len(result.tables), "normalized_table_count": normalized_count,
            "warning_count": warning_count,
        })
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps({"tool": "camelot", "source_pdf": str(pdf_path), "runs": manifest_runs}, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path
