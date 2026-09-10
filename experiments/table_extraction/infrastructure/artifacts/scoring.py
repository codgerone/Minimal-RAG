"""导出评分 JSON、运行摘要和逐组人工审核视图。"""

from __future__ import annotations

from dataclasses import asdict
import html
import json
import os
from pathlib import Path
import shutil
from typing import Any
from uuid import uuid4
from .publication import publish_directories

from experiments.table_extraction.domain.scoring.config import METRIC_DIRECTIONS, METRIC_WEIGHTS, SCORE_EPSILON
from experiments.table_extraction.domain.models.scoring import CandidateScoringResult, GroupScoringResult, RelativeMetricScore, ScoringReport, TokenCount
from experiments.table_extraction.application.models import ScoringManifest
from experiments.table_extraction.domain.models.tables import BoundingBox, TableCandidate


def _write_json(path: Path, value: Any) -> None:
    """写入便于人工审阅的 UTF-8 JSON。"""
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def report_dict(report: ScoringReport) -> dict[str, Any]:
    """按架构规定的字段顺序序列化 ScoringReport。"""
    return {
        "format_version": report.format_version,
        "groups": [asdict(group) for group in report.groups],
        "source_pdf": report.source_pdf,
        "source_grouping_report": report.source_grouping_report,
        "warnings": report.warnings,
    }


def export_scoring(
    output_dir: Path,
    final_review_dir: Path,
    source_pdf: Path,
    tools_output_root: Path,
    candidates_by_id: dict[str, TableCandidate],
    report: ScoringReport,
    manifest: ScoringManifest,
    *, render_views,
) -> Path:
    """完整写好评分 JSON 和审核视图后替换同 PDF 的上一版产物。"""
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    final_review_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_root = output_dir.parent / f".scoring-{uuid4().hex}"
    staging_output = staging_root / "report"
    staging_review = staging_root / "review"
    staging_root.mkdir()
    try:
        staging_output.mkdir()
        _write_json(staging_output / "scoring.json", report_dict(report))
        _write_json(staging_output / "manifest.json", asdict(manifest))
        rendered_pages = render_views(
            final_review_dir, source_pdf, tools_output_root,
            candidates_by_id, report,
        )
        staging_review.mkdir()
        for filename, document in rendered_pages.items():
            (staging_review / filename).write_text(document, encoding="utf-8")
        publish_directories([(staging_output, output_dir), (staging_review, final_review_dir)])
    finally:
        if staging_root.exists():
            shutil.rmtree(staging_root)
    return output_dir / "scoring.json"
