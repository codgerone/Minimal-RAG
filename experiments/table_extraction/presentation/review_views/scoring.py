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

from experiments.table_extraction.domain.scoring.config import METRIC_DIRECTIONS, METRIC_WEIGHTS, SCORE_EPSILON
from experiments.table_extraction.domain.models.scoring import CandidateScoringResult, GroupScoringResult, RelativeMetricScore, ScoringReport, TokenCount
from experiments.table_extraction.application.models import ScoringManifest
from experiments.table_extraction.domain.models.tables import BoundingBox, TableCandidate

def _format_float(value: float | None) -> str:
    """把审核视图中的浮点数统一显示为两位小数。"""
    return "N/A" if value is None else f"{value:.2f}"


def _format_value(value: object) -> str:
    """把普通审计值转换为明确的显示文本。"""
    return "N/A" if value is None else str(value)


def _format_bbox(bbox: BoundingBox) -> str:
    """把 Slot bbox 格式化为两位小数坐标。"""
    return f"({_format_float(bbox.x0)}, {_format_float(bbox.y0)}, {_format_float(bbox.x1)}, {_format_float(bbox.y1)})"


def _token_count_text(values: list[TokenCount]) -> str:
    """把未匹配 token 频次压缩为可读文本。"""
    return "无" if not values else ", ".join(f"{item.token} × {item.count}" for item in values)


def _reason_text(reason_codes: list[str]) -> str:
    """把原因码列表转换为审核视图文本。"""
    return ", ".join(reason_codes) if reason_codes else "无"


def _relative_by_name(candidate: CandidateScoringResult) -> dict[str, RelativeMetricScore]:
    """按指标名索引候选的四项相对分。"""
    return {item.metric_name: item for item in candidate.relative_scores}


def _competition_ranks(values: dict[str, float]) -> dict[str, int]:
    """按统一容差生成同分共享名次的竞赛排名。"""
    return {
        identifier: 1 + sum(other > value + SCORE_EPSILON for other in values.values())
        for identifier, value in values.items()
    }


def _candidate_ranks(group: GroupScoringResult) -> tuple[dict[str, dict[str, int]], dict[str, int]]:
    """计算四项相对分排名和总分排名。"""
    relative = {candidate.candidate_id: _relative_by_name(candidate) for candidate in group.candidates}
    metric_ranks = {
        metric_name: _competition_ranks({
            candidate.candidate_id: relative[candidate.candidate_id][metric_name].score
            for candidate in group.candidates
        })
        for metric_name in METRIC_WEIGHTS
    }
    total_ranks = _competition_ranks({
        candidate.candidate_id: candidate.total_score for candidate in group.candidates
    })
    return metric_ranks, total_ranks


def _source_html(
    candidate: TableCandidate,
    source_pdf: Path,
    tools_output_root: Path,
) -> Path | None:
    """定位公共候选对应的规范化表格 HTML。"""
    if not candidate.strategy or not candidate.artifacts.html_file:
        return None
    return (
        tools_output_root / candidate.tool / source_pdf.stem / "normalized"
        / candidate.strategy / candidate.artifacts.html_file
    )


def _table_iframe(source: Path | None, final_review_dir: Path, title: str) -> str:
    """生成表格 iframe；源文件缺失时保留明确提示。"""
    if source is None or not source.is_file():
        return '<p class="missing">未找到该 candidate 的表格 HTML 文件。</p>'
    relative = Path(os.path.relpath(source, final_review_dir)).as_posix()
    return f'<iframe src="{html.escape(relative, quote=True)}" title="{html.escape(title, quote=True)}"></iframe>'


def _audit_rows(rows: list[tuple[str, object]], float_fields: set[str] | None = None) -> str:
    """生成一个指标卡片中的键值审计行。"""
    formatted: list[str] = []
    float_fields = float_fields or set()
    for label, value in rows:
        rendered = _format_float(value) if label in float_fields else _format_value(value)
        formatted.append(
            f'<div class="fact"><dt>{html.escape(label)}</dt><dd>{html.escape(rendered)}</dd></div>'
        )
    return "".join(formatted)


def _relative_rows(relative: RelativeMetricScore, rank: int) -> list[tuple[str, object]]:
    """返回每个指标共用的战绩与排名字段。"""
    return [
        ("metric_rank", rank),
        ("wins / ties / losses", f"{relative.wins} / {relative.ties} / {relative.losses}"),
        ("opponent_count", relative.opponent_count),
    ]


def _metric_cards(
    candidate: CandidateScoringResult,
    metric_ranks: dict[str, dict[str, int]],
) -> str:
    """生成 candidate 的四项原始指标、相对分和排名卡片。"""
    raw = candidate.raw_metrics
    relative = _relative_by_name(candidate)
    identifier = candidate.candidate_id
    text_metric = raw.text_coverage
    critical = raw.critical_tokens
    shape = raw.grid_shape
    blank = raw.blank_grid

    text_rows = [
        ("status", text_metric.status),
        ("reason_codes", _reason_text(text_metric.reason_codes)),
        ("reference_token_count", text_metric.reference_token_count),
        ("candidate_token_count", text_metric.candidate_token_count),
        ("matched_token_count", text_metric.matched_token_count),
        ("precision", text_metric.precision),
        ("recall", text_metric.recall),
        ("f1", text_metric.f1),
        ("unmatched_reference_tokens", _token_count_text(text_metric.unmatched_reference_tokens)),
        ("unmatched_candidate_tokens", _token_count_text(text_metric.unmatched_candidate_tokens)),
    ] + _relative_rows(relative["text_f1"], metric_ranks["text_f1"][identifier])
    critical_rows = [
        ("status", critical.status),
        ("reason_codes", _reason_text(critical.reason_codes)),
        ("reference_token_count", critical.reference_token_count),
        ("matched_token_count", critical.matched_token_count),
        ("integrity", critical.integrity),
        ("unmatched_reference_tokens", _token_count_text(critical.unmatched_reference_tokens)),
    ] + _relative_rows(
        relative["critical_token_integrity"], metric_ranks["critical_token_integrity"][identifier],
    )
    shape_rows = [
        ("status", shape.status),
        ("reason_codes", _reason_text(shape.reason_codes)),
        ("row_count", shape.row_count),
        ("column_count", shape.column_count),
        ("same_shape_candidate_count", shape.same_shape_candidate_count),
        ("group_candidate_count", shape.group_candidate_count),
        ("support", shape.support),
    ] + _relative_rows(relative["shape_support"], metric_ranks["shape_support"][identifier])
    blank_rows = [
        ("status", blank.status),
        ("reason_codes", _reason_text(blank.reason_codes)),
        ("logical_position_count", blank.logical_position_count),
        ("nonempty_covered_position_count", blank.nonempty_covered_position_count),
        ("blank_position_count", blank.blank_position_count),
        ("blank_ratio", blank.blank_ratio),
        ("reference_source", blank.reference_source),
        ("reference_blank_ratio", blank.reference_blank_ratio),
        ("blank_anomaly", blank.blank_anomaly),
        ("invalid_nonempty_cell_ids", ", ".join(blank.invalid_nonempty_cell_ids) or "无"),
    ] + _relative_rows(relative["blank_anomaly"], metric_ranks["blank_anomaly"][identifier])

    float_fields = {
        "precision", "recall", "f1", "integrity", "support", "blank_ratio",
        "reference_blank_ratio", "blank_anomaly",
    }
    cards = [
        ("文本双向覆盖率", relative["text_f1"].score, text_rows),
        ("关键值词完整度", relative["critical_token_integrity"].score, critical_rows),
        ("网格形状共识度", relative["shape_support"].score, shape_rows),
        ("空白网格异常度", relative["blank_anomaly"].score, blank_rows),
    ]
    return "".join(
        f'<article class="metric"><h3><span>{title}</span>'
        f'<span class="metric-score">relative_score：{_format_float(score)}</span></h3>'
        f'<dl>{_audit_rows(rows, float_fields)}</dl></article>'
        for title, score, rows in cards
    )


def _candidate_section(
    candidate: CandidateScoringResult,
    full_candidate: TableCandidate,
    metric_ranks: dict[str, dict[str, int]],
    total_rank: int,
    source_pdf: Path,
    tools_output_root: Path,
    final_review_dir: Path,
    selected_candidate_id: str,
) -> str:
    """生成一个 candidate 的表格、指标明细、排名和 winner 标记。"""
    is_winner = candidate.candidate_id == selected_candidate_id
    badge = '<span class="winner-badge">WINNER</span>' if is_winner else ""
    section_class = "candidate winner" if is_winner else "candidate"
    warnings = ", ".join(candidate.raw_metrics.warnings) or "无"
    table = _table_iframe(
        _source_html(full_candidate, source_pdf, tools_output_root),
        final_review_dir,
        candidate.candidate_id,
    )
    return f"""
<section class="{section_class}">
  <h2>{html.escape(candidate.candidate_id)} {badge}</h2>
  <p class="candidate-meta">tool={html.escape(candidate.tool)} | strategy={html.escape(candidate.strategy or 'N/A')}</p>
  <div class="total-score"><strong>总分 {_format_float(candidate.total_score)}</strong><span>总分排名 #{total_rank}</span></div>
  <p class="warnings"><strong>warnings：</strong>{html.escape(warnings)}</p>
  <div class="metrics">{_metric_cards(candidate, metric_ranks)}</div>
  <div class="table-view"><h3>候选表格</h3>{table}</div>
</section>"""


def _common_facts(group: GroupScoringResult, source_pdf: Path) -> str:
    """生成页面顶部的 Slot 参照与最终选择共同事实。"""
    reference = group.text_reference
    blank = next(
        (
            candidate.raw_metrics.blank_grid
            for candidate in group.candidates
            if candidate.raw_metrics.blank_grid.reference_blank_ratio is not None
        ),
        None,
    )
    token_count = sum(item.count for item in reference.token_counts)
    critical_count = sum(item.count for item in reference.critical_token_counts)
    rows = [
        ("PDF", source_pdf.name),
        ("group_id", group.group_id),
        ("slot_id", group.slot_id),
        ("page_number", reference.page_number),
        ("slot_bbox", _format_bbox(reference.slot_bbox)),
        ("reference_source", reference.source),
        ("reference_word_count", len(reference.words)),
        ("reference_token_count", token_count),
        ("critical_reference_token_count", critical_count),
        ("candidate_count", len(group.candidates)),
        ("blank_reference_source", blank.reference_source if blank else None),
        ("reference_blank_ratio", _format_float(blank.reference_blank_ratio) if blank else "N/A"),
        ("metric_weights", ", ".join(f"{name}={weight:.2f}" for name, weight in METRIC_WEIGHTS.items())),
        ("metric_directions", ", ".join(f"{name}={value}" for name, value in METRIC_DIRECTIONS.items())),
        ("score_epsilon", str(SCORE_EPSILON)),
        ("highest_total_score", _format_float(group.highest_total_score)),
        ("tied_top_candidate_ids", ", ".join(group.tied_top_candidate_ids)),
        ("selected_candidate_id", group.selected_candidate_id),
        ("selection_reason", group.selection_reason),
    ]
    return _audit_rows(rows)


def render_scoring_views(
    final_review_dir: Path,
    source_pdf: Path,
    tools_output_root: Path,
    candidates_by_id: dict[str, TableCandidate],
    report: ScoringReport,
) -> dict[str, str]:
    """为每个评分 group 写入包含全部候选、指标和排名的 HTML。"""
    rendered_pages: dict[str, str] = {}
    for group in report.groups:
        metric_ranks, total_ranks = _candidate_ranks(group)
        original_indexes = {
            candidate.candidate_id: index for index, candidate in enumerate(group.candidates)
        }
        ordered_candidates = sorted(
            group.candidates,
            key=lambda candidate: (
                total_ranks[candidate.candidate_id],
                candidate.candidate_id != group.selected_candidate_id,
                original_indexes[candidate.candidate_id],
            ),
        )
        sections = [
            _candidate_section(
                candidate,
                candidates_by_id[candidate.candidate_id],
                metric_ranks,
                total_ranks[candidate.candidate_id],
                source_pdf,
                tools_output_root,
                final_review_dir,
                group.selected_candidate_id,
            )
            for candidate in ordered_candidates
        ]
        document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>{html.escape(source_pdf.name)} / {group.group_id} / scoring</title>
<style>
:root {{ color-scheme:light; --green:#137333; --border:#d9dde3; --muted:#5f6368; }}
body {{ font-family:Arial,"Microsoft YaHei",sans-serif; margin:24px; color:#202124; background:#f7f9fc; }}
h1 {{ margin:0 0 16px; }} h2 {{ font-family:Consolas,monospace; font-size:18px; margin-top:0; }}
.common,.candidate {{ background:#fff; border:1px solid var(--border); border-radius:10px; padding:20px; margin-bottom:24px; }}
.common dl {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:8px 16px; margin:0; }}
.metric dl {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px 16px; margin:0; }}
.fact {{ min-width:0; border-bottom:1px solid #eef0f2; padding:6px 0; }}
dt {{ color:var(--muted); font-size:12px; }} dd {{ margin:3px 0 0; overflow-wrap:anywhere; font-family:Consolas,monospace; }}
.candidate.winner {{ border:3px solid var(--green); box-shadow:0 0 0 4px #e6f4ea; }}
.winner-badge {{ background:var(--green); color:#fff; border-radius:999px; padding:4px 10px; font:700 12px Arial,sans-serif; }}
.candidate-meta,.warnings {{ color:var(--muted); }}
.total-score {{ display:flex; gap:24px; align-items:center; background:#e8f0fe; color:#174ea6; border-radius:8px; padding:12px 16px; margin:12px 0; }}
.metrics {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(340px,1fr)); gap:14px; }}
.metric {{ border:1px solid var(--border); border-radius:8px; padding:14px; }}
.metric h3 {{ display:flex; justify-content:space-between; gap:12px; margin:0 0 12px; font-size:16px; }}
.metric-score {{ color:#174ea6; white-space:nowrap; font-family:Consolas,monospace; }}
.table-view {{ margin-top:18px; }} iframe {{ width:100%; min-height:420px; border:1px solid #9aa0a6; background:#fff; }}
.missing {{ color:#b3261e; }}
@media (max-width:760px) {{ .metric dl {{ grid-template-columns:1fr; }} .metric h3 {{ display:block; }} .metric-score {{ display:block; margin-top:4px; }} }}
</style></head><body>
<h1>{html.escape(source_pdf.name)} / {group.group_id} / Scoring</h1>
<section class="common"><h2>Group 共同事实</h2><dl>{_common_facts(group, source_pdf)}</dl></section>
{''.join(sections)}
</body></html>"""
        rendered_pages[f"{group.group_id}.html"] = document
    return rendered_pages
