"""写出 Unstructured 原始 Element 与规范化表格。"""

from __future__ import annotations

import json
from pathlib import Path
from ..normalization import normalize_page_groups, apply_page_failures
from typing import Any

from experiments.table_extraction.infrastructure.artifacts.writers import _write_csv, _write_json
from experiments.table_extraction.infrastructure.extractors.unstructured.models import UnstructuredRunResult
from experiments.table_extraction.infrastructure.extractors.unstructured.normalizer import normalize_elements


def serialize_elements(elements: list[Any]) -> list[dict[str, Any]]:
    """使用官方序列化方法保留完整 Element。"""
    from unstructured.staging.base import convert_to_dict
    return convert_to_dict(elements)


def export_results(result: UnstructuredRunResult, output_dir: Path, pdf_path: Path, *, render_table) -> Path:
    """按统一 raw/normalized 结构导出一次解析结果。"""
    normalized_count = warning_count = 0
    if result.status == "success":
        elements = serialize_elements(result.elements)
        _write_json(output_dir / "raw" / "elements.json", elements)
        groups = {}
        table_numbers = {}
        for index, element in enumerate(elements):
            if element.get('type') == 'Table':
                table_numbers[index] = len(table_numbers) + 1
                page = (element.get('metadata') or {}).get('page_number')
                groups.setdefault((page,) if isinstance(page, int) else (), []).append(index)
        normalized = normalize_page_groups(groups, lambda indices: normalize_elements(
            [elements[i] for i in indices], str(pdf_path), element_indices=indices,
            table_numbers=table_numbers))
        apply_page_failures(result.run_metadata, normalized.failed_pages)
        tables_dir = output_dir / "normalized" / "hi_res" / "tables"
        tables_dir.mkdir(parents=True, exist_ok=True)
        for candidate in normalized.candidates:
            _write_csv(tables_dir / Path(candidate.artifacts.csv_file or "table.csv").name, normalized.rows_by_candidate[candidate.candidate_id])
            (tables_dir / Path(candidate.artifacts.html_file or "table.html").name).write_text(render_table(candidate), encoding="utf-8")
        _write_json(output_dir / "normalized" / "hi_res" / "tables.json", {"tool": "unstructured", "strategy": "hi_res", "table_count": len(normalized.candidates), "tables": [item.to_dict() for item in normalized.candidates]})
        normalized_count = len(normalized.candidates)
        warning_count = sum(len(item.warnings) for item in normalized.candidates)
    manifest = {"tool": "unstructured", "source_pdf": str(pdf_path), "status": result.status, "error": result.error, "run_metadata": result.run_metadata, "normalized_table_count": normalized_count, "warning_count": warning_count}
    path = output_dir / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
