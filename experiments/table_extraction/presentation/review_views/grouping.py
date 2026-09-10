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

from experiments.table_extraction.domain.models.selection import CandidateAdmissionResult, CandidateView, GroupingReport, SlotMatchEvaluation, TableGroup, TableSlot
from experiments.table_extraction.application.models import GroupingManifest

def _source_html(view: CandidateView, source_pdf: Path, tools_output_root: Path) -> Path | None:
    """定位一个规范化候选已经生成的 HTML 文件。"""
    if not view.html_file or not view.strategy:
        return None
    return tools_output_root / view.tool / source_pdf.stem / "normalized" / view.strategy / view.html_file


def _iframe(source: Path | None, final_review_dir: Path, title: str) -> str:
    """生成候选 HTML iframe；文件缺失时返回明确提示。"""
    if source is None or not source.is_file():
        return '<p class="missing">未找到该候选的表格 HTML 文件。</p>'
    relative = Path(os.path.relpath(source, final_review_dir)).as_posix()
    return f'<iframe src="{html.escape(relative, quote=True)}" title="{html.escape(title, quote=True)}"></iframe>'


def _metrics_line(evaluation: SlotMatchEvaluation | None) -> str:
    """把成员候选的准入覆盖率转换为简洁审核文本。"""
    if evaluation is None:
        return "未找到该成员的 accepted slot 评价。"
    metrics = evaluation.metrics
    return (
        f"candidate_coverage={metrics.candidate_coverage:.6f} | "
        f"slot_coverage={metrics.slot_coverage:.6f} | IoU={metrics.iou:.6f}"
    )


def _candidate_section(
    view: CandidateView,
    source_pdf: Path,
    tools_output_root: Path,
    final_review_dir: Path,
    evaluation: SlotMatchEvaluation | None,
    is_slot_baseline: bool,
    is_member: bool,
) -> str:
    """生成一个 slot 基准或 admitted 成员的审核区块。"""
    labels = []
    if is_slot_baseline:
        labels.append("Docling slot 基准")
    if is_member:
        labels.append("admitted member")
    title = html.escape(view.candidate_id)
    meta = f"tool={html.escape(view.tool)} | strategy={html.escape(view.strategy or 'none')} | {' + '.join(labels)}"
    metrics = _metrics_line(evaluation) if is_member else "该视图仅作为 Docling Table Slot 的可读基准。"
    return (
        f"<section><h2>{title}</h2><p class=\"meta\">{meta}</p>"
        f"<p class=\"metrics\">{html.escape(metrics)}</p>"
        f"{_iframe(_source_html(view, source_pdf, tools_output_root), final_review_dir, view.candidate_id)}</section>"
    )


def render_grouping_views(
    final_review_dir: Path,
    source_pdf: Path,
    tools_output_root: Path,
    groups: list[TableGroup],
    slots: list[TableSlot],
    views: list[CandidateView],
    evaluations: list[SlotMatchEvaluation],
) -> dict[str, str]:
    """为每个 Table Slot 写入包含基准和 admitted members 的 HTML。"""
    rendered_pages: dict[str, str] = {}
    slot_by_id = {slot.slot_id: slot for slot in slots}
    view_by_id = {view.candidate_id: view for view in views}
    evaluation_by_pair = {
        (item.candidate_id, item.slot_id): item
        for item in evaluations
        if item.decision == "accepted"
    }
    baseline_by_ref = {
        view.source_ref: view
        for view in views
        if view.tool == "docling"
    }

    for group in groups:
        slot = slot_by_id[group.slot_id]
        baseline = baseline_by_ref.get(f"raw/document.json{slot.docling_table_ref}")
        sections: list[str] = []
        rendered: set[str] = set()
        if baseline is not None:
            is_member = baseline.candidate_id in group.member_candidate_ids
            sections.append(_candidate_section(
                baseline, source_pdf, tools_output_root, final_review_dir,
                evaluation_by_pair.get((baseline.candidate_id, slot.slot_id)), True, is_member,
            ))
            rendered.add(baseline.candidate_id)
        else:
            sections.append('<section><h2>Docling slot 基准</h2><p class="missing">无法映射到 Docling normalized candidate。</p></section>')

        for identifier in group.member_candidate_ids:
            if identifier in rendered:
                continue
            view = view_by_id[identifier]
            sections.append(_candidate_section(
                view, source_pdf, tools_output_root, final_review_dir,
                evaluation_by_pair.get((identifier, slot.slot_id)), False, True,
            ))

        bbox = asdict(group.slot_bbox) if group.slot_bbox else None
        document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>{html.escape(source_pdf.name)} / {group.group_id}</title>
<style>
body {{ font-family: Arial,"Microsoft YaHei",sans-serif; margin:24px; color:#202124; }}
h1 {{ margin-bottom:4px; }} .meta {{ color:#5f6368; }} .metrics {{ font-family:Consolas,monospace; }}
section {{ border-top:1px solid #dadce0; margin-top:24px; padding-top:16px; }}
h2 {{ font-family:Consolas,monospace; font-size:16px; }}
iframe {{ width:100%; min-height:420px; border:1px solid #9aa0a6; }} .missing {{ color:#b3261e; }}
</style></head><body>
<h1>{html.escape(source_pdf.name)} / {group.group_id}</h1>
<p class="meta">slot_id={group.slot_id} | page={group.page_number} | status={group.status} | unresolved_reason={group.unresolved_reason}</p>
<p class="meta">slot_bbox={html.escape(json.dumps(bbox, ensure_ascii=False))} | docling_table_ref={html.escape(group.docling_table_ref)}</p>
{''.join(sections)}
</body></html>"""
        rendered_pages[f"{group.group_id}.html"] = document
    return rendered_pages
