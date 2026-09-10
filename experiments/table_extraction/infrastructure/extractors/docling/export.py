"""写出精简的 Docling 原始结果和规范化表格结果。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from ..normalization import normalize_page_groups, apply_page_failures

from experiments.table_extraction.infrastructure.extractors.docling.models import DoclingRunResult
from experiments.table_extraction.infrastructure.extractors.docling.normalizer import normalize_document
from experiments.table_extraction.infrastructure.artifacts.writers import _write_csv, _write_json


def export_results(result: DoclingRunResult, output_dir: Path, pdf_path: Path, *, render_table) -> Path:
    """按统一目录结构导出一次 Docling 运行结果。"""
    normalized_count = 0
    warning_count = 0
    for index, document in enumerate(result.attempt_documents):
        _write_json(output_dir / 'raw' / f'attempt_document_{index + 1}.json', document)
    if result.status == "success" and result.document is not None:
        raw_dir = output_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        result.document.save_as_json(raw_dir / "document.json")
        result.document.save_as_html(raw_dir / "document.html")
        groups = {}
        for index, table in enumerate(result.document.tables):
            pages = tuple(sorted({p.page_no for p in table.prov}))
            groups.setdefault(pages, []).append(index)
        normalized = normalize_page_groups(groups, lambda indices: normalize_document(
            SimpleNamespace(tables=[result.document.tables[i] for i in indices], pages=result.document.pages),
            str(pdf_path), table_indices=indices))
        apply_page_failures(result.run_metadata, normalized.failed_pages)
        strategy_dir = output_dir / "normalized" / "default"
        tables_dir = strategy_dir / "tables"
        tables_dir.mkdir(parents=True, exist_ok=True)
        for candidate in normalized.candidates:
            _write_csv(tables_dir / Path(candidate.artifacts.csv_file or "table.csv").name, normalized.rows_by_candidate[candidate.candidate_id])
            (tables_dir / Path(candidate.artifacts.html_file or "table.html").name).write_text(render_table(candidate), encoding="utf-8")
        _write_json(strategy_dir / "tables.json", {
            "tool": "docling", "strategy": "default", "table_count": len(normalized.candidates),
            "tables": [candidate.to_dict() for candidate in normalized.candidates],
        })
        normalized_count = len(normalized.candidates)
        warning_count = sum(len(candidate.warnings) for candidate in normalized.candidates)

    manifest = {
        "tool": "docling", "source_pdf": str(pdf_path), "status": result.status,
        "error": result.error, "run_metadata": result.run_metadata,
        "raw_document_path": "raw/document.json" if result.status == "success" and result.document is not None else None,
        "normalized_strategy_path": "normalized/default/tables.json" if result.status == "success" and result.document is not None else None,
        "native_table_count": result.run_metadata.get("table_count", 0),
        "normalized_table_count": normalized_count, "warning_count": warning_count,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path
