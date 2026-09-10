"""导出候选准入、分组报告和人工审核视图。"""

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
from .identity import digest, extraction_fingerprints

from experiments.table_extraction.domain.models.selection import CandidateAdmissionResult, CandidateView, GroupingReport, SlotMatchEvaluation, TableGroup, TableSlot
from experiments.table_extraction.application.models import GroupingManifest


def _write_json(path: Path, value: Any) -> None:
    """以稳定、可人工审阅的 UTF-8 JSON 写入文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def report_dict(report: GroupingReport) -> dict[str, Any]:
    """按规范字段顺序将 GroupingReport 转为 JSON 对象。"""
    return {
        "format_version": report.format_version,
        "groups": [asdict(item) for item in report.groups],
        "table_slots": [asdict(item) for item in report.table_slots],
        "candidate_views": [asdict(item) for item in report.candidate_views],
        "candidate_admission_results": [asdict(item) for item in report.candidate_admission_results],
        "slot_match_evaluations": [asdict(item) for item in report.slot_match_evaluations],
        "warnings": report.warnings,
    }


def _manifest(
    source_pdf: Path,
    report: GroupingReport,
    counts: dict[str, int],
    elapsed_seconds: float,
    manual_review_dir: Path,
) -> GroupingManifest:
    """根据最终报告计算 manifest 摘要。"""
    return GroupingManifest(
        source_pdf=str(source_pdf.resolve()),
        format_version=report.format_version,
        input_candidate_counts=counts,
        deferred_admission_count=sum(item.processing_status == "deferred" for item in report.candidate_admission_results),
        table_slot_count=len(report.table_slots),
        eligible_slot_count=sum(item.status == "eligible" for item in report.table_slots),
        deferred_slot_count=sum(item.status == "deferred" for item in report.table_slots),
        candidate_count=len(report.candidate_views),
        comparable_candidate_count=sum(item.processing_status == "comparable" for item in report.candidate_views),
        deferred_candidate_count=sum(item.processing_status == "deferred" for item in report.candidate_views),
        admitted_candidate_count=sum(item.admission_decision == "admitted" for item in report.candidate_admission_results),
        rejected_candidate_count=sum(item.admission_decision == "rejected" for item in report.candidate_admission_results),
        ready_group_count=sum(item.status == "ready_for_scoring" for item in report.groups),
        unresolved_group_count=sum(item.status == "unresolved" for item in report.groups),
        warnings=report.warnings,
        elapsed_seconds=round(elapsed_seconds, 3),
        groups_file="groups.json",
        manual_review_dir=str(manual_review_dir),
    )


def export_selection(
    output_dir: Path,
    source_pdf: Path,
    tools_output_root: Path,
    report: GroupingReport,
    input_counts: dict[str, int],
    elapsed_seconds: float,
    *, render_views,
) -> Path:
    """先完整生成临时产物，再替换当前 PDF 的上一版结果。"""
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    final_review_dir = output_dir.parent / "group_views_for_manual_review" / source_pdf.stem
    final_review_dir.parent.mkdir(parents=True, exist_ok=True)
    # 在输出父目录中直接创建，确保 Windows ACL 与最终结果目录一致。
    staging_root = output_dir.parent / f".selection-{uuid4().hex}"
    staging_root.mkdir()
    staging_output = staging_root / "report"
    staging_review = staging_root / "review"
    try:
        staging_output.mkdir()
        _write_json(staging_output / "groups.json", report_dict(report))
        rendered_pages = render_views(
            final_review_dir, source_pdf, tools_output_root,
            report.groups, report.table_slots, report.candidate_views, report.slot_match_evaluations,
        )
        staging_review.mkdir()
        for filename, document in rendered_pages.items():
            (staging_review / filename).write_text(document, encoding="utf-8")
        manifest = _manifest(source_pdf, report, input_counts, elapsed_seconds, final_review_dir)
        payload = asdict(manifest)
        payload['source_sha256'] = digest(source_pdf)
        payload['extraction_sha256'] = extraction_fingerprints(tools_output_root, source_pdf.stem)
        payload['report_sha256'] = digest(staging_output / 'groups.json')
        _write_json(staging_output / "manifest.json", payload)

        publish_directories([(staging_output, output_dir), (staging_review, final_review_dir)])
    finally:
        if staging_root.exists():
            shutil.rmtree(staging_root)
    return output_dir / "groups.json"
