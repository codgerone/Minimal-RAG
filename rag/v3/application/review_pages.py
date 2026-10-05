"""Self-contained, document-level HTML views of already-computed table evidence."""

from __future__ import annotations

import html
from dataclasses import fields

from rag.v3.contracts.processing import ProcessingResult
from rag.v3.contracts.tables import (
    CandidateScoringResult, GroupScoringResult, PreparedTableContent,
    StructuredTable, TableCandidate,
)


def _e(value: object) -> str:
    return html.escape("—" if value is None else str(value), quote=True)


def _n(value: float | None) -> str:
    return "—" if value is None else f"{value:.6g}"


def _list(values) -> str:
    return ", ".join(str(value) for value in values) or "—"


def _facts(items: tuple[tuple[str, object], ...]) -> str:
    return '<dl class="facts">' + "".join(
        f'<div><dt>{_e(label)}</dt><dd>{_e(value)}</dd></div>' for label, value in items
    ) + '</dl>'


def _bbox(box) -> str:
    return "—" if box is None else ", ".join(_n(value) for value in
                                           (box.x0, box.y0, box.x1, box.y1))


def _table_grid(table: TableCandidate | StructuredTable,
                *, header_end_row: int | None = None) -> str:
    rows, cols = table.row_count, table.column_count
    if rows is None or cols is None or rows <= 0 or cols <= 0:
        grid = '<p class="empty">此候选没有可定位的行列网格。</p>'
    else:
        starts = {(cell.start_row_offset_idx, cell.start_col_offset_idx): cell
                  for cell in table.cells
                  if cell.span_source != "unavailable"
                  and None not in (cell.start_row_offset_idx, cell.end_row_offset_idx,
                                   cell.start_col_offset_idx, cell.end_col_offset_idx)}
        covered: set[tuple[int, int]] = set()
        markup: list[str] = []
        for row in range(rows):
            parts = ["<tr>"]
            for col in range(cols):
                if (row, col) in covered:
                    continue
                cell = starts.get((row, col))
                if cell is None:
                    parts.append('<td class="uncovered" title="没有可定位的物理单元格"></td>')
                    continue
                row_end = min(rows, cell.end_row_offset_idx)
                col_end = min(cols, cell.end_col_offset_idx)
                for covered_row in range(row, row_end):
                    for covered_col in range(col, col_end):
                        covered.add((covered_row, covered_col))
                tag = "th" if header_end_row is not None and row < header_end_row else "td"
                body = _e(cell.text or "").replace("\n", "<br>")
                parts.append(
                    f'<{tag} data-cell-id="{_e(cell.cell_id)}" '
                    f'rowspan="{row_end - row}" colspan="{col_end - col}">{body}</{tag}>'
                )
            parts.append("</tr>")
            markup.append("".join(parts))
        grid = f'<div class="table-scroll"><table class="physical">{"".join(markup)}</table></div>'
    unplaced = [cell.text for cell in table.cells
                if cell.span_source == "unavailable" and cell.text]
    if table.unplaced_text:
        unplaced.append(table.unplaced_text)
    if unplaced:
        grid += ('<details class="unplaced"><summary>无法定位到网格的原始文本</summary><pre>'
                 + _e("\n".join(unplaced)) + '</pre></details>')
    return grid


def _page(title: str, processing: ProcessingResult, parser_id: str,
          body: str, count: int) -> str:
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title)} · {_e(processing.source.document_name)}</title>
<style>
:root {{ color-scheme:light; --border:#d9dde3; --muted:#586372; --blue:#174ea6; --green:#137333; }}
* {{ box-sizing:border-box }} body {{ font:15px/1.55 Arial,"Microsoft YaHei",sans-serif; margin:0; background:#f5f7fb; color:#202124 }}
main {{ max-width:1500px; margin:0 auto; padding:24px }} h1 {{ margin:0 0 8px }} h2,h3,h4 {{ margin:.2em 0 .6em }}
.lead {{ color:var(--muted); overflow-wrap:anywhere }} .slot {{ background:#fff; border:2px solid #a9b9ce; border-radius:12px; margin:28px 0; padding:22px }}
.slot-head {{ border-bottom:2px solid #dce5f1; margin-bottom:18px; padding-bottom:14px }} .slot-head h2 {{ color:#17365d }}
.candidate {{ border:1px solid var(--border); border-radius:10px; margin:18px 0; padding:18px; background:#fff }}
.candidate.winner {{ border:3px solid var(--green); box-shadow:0 0 0 3px #e6f4ea }}
.badge {{ display:inline-block; border-radius:999px; background:#e8f0fe; color:var(--blue); padding:2px 9px; margin-left:8px; font-size:12px; font-weight:bold }}
.winner .badge {{ background:var(--green); color:#fff }} .muted {{ color:var(--muted) }}
.facts {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:8px 16px; margin:10px 0 18px }}
.facts div {{ border-bottom:1px solid #edf0f4; min-width:0; padding:5px 0 }} dt {{ color:var(--muted); font-size:12px }}
dd {{ margin:2px 0 0; overflow-wrap:anywhere; font-family:Consolas,monospace }}
.metrics {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(340px,1fr)); gap:12px }}
.metric {{ border:1px solid var(--border); border-radius:8px; padding:12px }} .metric h4 {{ color:var(--blue) }}
.metric .facts {{ grid-template-columns:repeat(2,minmax(0,1fr)) }}
.score {{ background:#e8f0fe; color:var(--blue); border-radius:8px; padding:9px 12px; font-weight:bold }}
.table-scroll {{ width:100%; overflow:auto; margin:12px 0 4px; border:1px solid #9aa0a6; background:#fff }}
table.physical {{ border-collapse:collapse; width:max-content; min-width:100% }}
.physical td,.physical th {{ border:1px solid #777; padding:6px 9px; vertical-align:top; min-width:28px; white-space:pre-wrap }}
.physical th {{ background:#dcebf9 }} .physical .uncovered {{ background:#fff3cd; min-width:22px }}
pre {{ white-space:pre-wrap; overflow-wrap:anywhere; background:#f7f8fa; padding:12px }} .empty {{ color:var(--muted) }}
.unplaced {{ margin-top:12px }} .unassigned {{ border-style:dashed }}
@media(max-width:700px) {{ main {{ padding:12px }} .slot {{ padding:12px }} .metrics,.metric .facts {{ grid-template-columns:1fr }} }}
</style></head><body><main>
<h1>{_e(title)}</h1>
<p class="lead" data-document-id="{_e(processing.source.document_id)}" data-build-id="{_e(processing.build_id)}">
{_e(processing.source.document_name)} · document_id={_e(processing.source.document_id)} · build_id={_e(processing.build_id)} · parser={_e(parser_id)} · count={count}</p>
{body}
</main></body></html>'''


def _slot_head(slot, group=None, resolution=None) -> str:
    facts = [
        ("slot_id", slot.slot_id), ("page", slot.page_number),
        ("slot_bbox (x0,y0,x1,y1)", _bbox(slot.bbox)),
        ("docling_table_ref", slot.docling_table_ref),
        ("slot_status", slot.status), ("deferred_reason", slot.deferred_reason),
    ]
    if group is not None:
        facts += [("group_id", group.group_id), ("group_status", group.status),
                  ("unresolved_reason", group.unresolved_reason),
                  ("member_candidate_ids", _list(group.member_candidate_ids))]
    if resolution is not None:
        facts += [("content_origin", resolution.origin),
                  ("selected_candidate_id", resolution.selected_candidate_id)]
    return f'<header class="slot-head"><h2>{_e(slot.slot_id)} · 第 {_e(slot.page_number)} 页</h2>{_facts(tuple(facts))}</header>'


def _candidate_base(candidate: TableCandidate) -> tuple[tuple[str, object], ...]:
    return (
        ("candidate_id", candidate.candidate_id), ("tool", candidate.tool),
        ("strategy", candidate.strategy), ("source_ref", candidate.source_ref),
        ("rows × columns", f"{candidate.row_count} × {candidate.column_count}"),
        ("regions", "; ".join(f"p{region.page_number}: {_bbox(region.bbox)}"
                               for region in candidate.regions) or "—"),
        ("warnings", _list(warning.code for warning in candidate.warnings)),
    )


def render_extractor_review(processing: ProcessingResult, parser_id: str) -> str:
    grouping = processing.grouping_report
    if grouping is None:
        raise ValueError("grouping report required for extractor review")
    candidates = tuple(candidate for report in processing.extraction_reports
                       for candidate in report.candidates)
    admission = {item.candidate_id: item for item in grouping.candidate_admission_results}
    evaluations = {(item.candidate_id, item.slot_id): item
                   for item in grouping.slot_match_evaluations}
    groups = {item.slot_id: item for item in grouping.groups}
    sections: list[str] = []
    displayed: set[str] = set()
    for slot in processing.primary_document.table_slots:
        group = groups[slot.slot_id]
        relevant = [candidate for candidate in candidates
                    if (slot.slot_id in admission[candidate.candidate_id].evaluated_slot_ids
                        or admission[candidate.candidate_id].matched_slot_id == slot.slot_id
                        or (candidate.tool == "docling" and
                            candidate.source_ref.endswith(slot.docling_table_ref)))]
        relevant.sort(key=lambda candidate: (
            0 if candidate.tool == "docling" and candidate.source_ref.endswith(slot.docling_table_ref)
            else 1 if candidate.candidate_id in group.member_candidate_ids else 2,
            candidate.candidate_id,
        ))
        cards: list[str] = []
        for candidate in relevant:
            displayed.add(candidate.candidate_id)
            result = admission[candidate.candidate_id]
            evaluation = evaluations.get((candidate.candidate_id, slot.slot_id))
            baseline = candidate.tool == "docling" and candidate.source_ref.endswith(slot.docling_table_ref)
            member = candidate.candidate_id in group.member_candidate_ids
            labels = (('<span class="badge">Docling slot 基准</span>' if baseline else '')
                      + ('<span class="badge">分组成员</span>' if member else ''))
            match = () if evaluation is None else (
                ("slot_match_decision", evaluation.decision),
                ("match_reasons", _list(evaluation.reason_codes)),
                ("candidate_coverage", _n(evaluation.metrics.candidate_coverage)),
                ("slot_coverage", _n(evaluation.metrics.slot_coverage)),
                ("IoU", _n(evaluation.metrics.iou)),
            )
            facts = _candidate_base(candidate) + (
                ("admission", result.admission_decision),
                ("admission_reasons", _list(result.reason_codes)),
                ("matched_slot_id", result.matched_slot_id),
                ("evaluated_slot_ids", _list(result.evaluated_slot_ids)),
            ) + match
            cards.append(f'<article class="candidate" data-candidate-id="{_e(candidate.candidate_id)}">'
                         f'<h3>{_e(candidate.candidate_id)}{labels}</h3>{_facts(facts)}'
                         f'<h4>候选表格</h4>{_table_grid(candidate)}</article>')
        sections.append(f'<section class="slot" data-slot-id="{_e(slot.slot_id)}">'
                        + _slot_head(slot, group)
                        + ("".join(cards) or '<p class="empty">此 slot 没有相关候选。</p>')
                        + '</section>')
    unassigned = [candidate for candidate in candidates if candidate.candidate_id not in displayed]
    if unassigned:
        sections.append('<section class="slot unassigned" data-unassigned="true"><h2>未归属到任何 slot 的候选</h2>'
                        + "".join(
                            f'<article class="candidate" data-candidate-id="{_e(item.candidate_id)}">'
                            f'<h3>{_e(item.candidate_id)}</h3>{_facts(_candidate_base(item))}'
                            f'{_table_grid(item)}</article>' for item in unassigned
                        ) + '</section>')
    if not sections:
        sections.append('<p class="empty">本 PDF 没有表格 slot 或候选。</p>')
    return _page("TableExtractor 人工审核", processing, parser_id,
                 "".join(sections), len(candidates))


_METRICS = (
    ("text_f1", "文本双向覆盖率", "text_coverage"),
    ("critical_token_integrity", "关键值词完整度", "critical_tokens"),
    ("shape_support", "网格形状共识度", "grid_shape"),
    ("blank_anomaly", "空白网格异常度", "blank_grid"),
)


def _metric_cards(candidate: CandidateScoringResult) -> str:
    relative = {item.metric_name: item for item in candidate.relative_scores}
    cards: list[str] = []
    for metric_id, title, field in _METRICS:
        raw = getattr(candidate.raw_metrics, field)
        rows: list[tuple[str, object]] = []
        for data_field in fields(raw):
            value = getattr(raw, data_field.name)
            if data_field.name.startswith("unmatched_"):
                value = ", ".join(f"{item.token} × {item.count}" for item in value) or "—"
            elif isinstance(value, tuple):
                value = _list(value)
            elif isinstance(value, float):
                value = _n(value)
            rows.append((data_field.name, value))
        score = relative[metric_id]
        rows += [("wins / ties / losses", f"{score.wins} / {score.ties} / {score.losses}"),
                 ("opponent_count", score.opponent_count)]
        cards.append(f'<article class="metric" data-metric="{_e(metric_id)}">'
                     f'<h4>{_e(title)} · relative_score={_n(score.score)}</h4>'
                     f'{_facts(tuple(rows))}</article>')
    return '<div class="metrics">' + "".join(cards) + '</div>'


def _scored_candidate(candidate: CandidateScoringResult, group: GroupScoringResult,
                      original: TableCandidate | None) -> str:
    winner = candidate.candidate_id == group.selected_candidate_id
    badge = '<span class="badge">WINNER</span>' if winner else ''
    table = _table_grid(original) if original is not None else '<p class="empty">候选表格事实缺失。</p>'
    facts = _facts((("tool", candidate.tool), ("strategy", candidate.strategy),
                    ("total_score", _n(candidate.total_score)),
                    ("warnings", _list(w.code for w in candidate.raw_metrics.warnings))))
    return (f'<article class="candidate{" winner" if winner else ""}" '
            f'data-candidate-id="{_e(candidate.candidate_id)}">'
            f'<h3>{_e(candidate.candidate_id)}{badge}</h3>'
            f'{facts}'
            f'<p class="score">总分 {_n(candidate.total_score)}</p>'
            f'{_metric_cards(candidate)}<h4>候选表格</h4>{table}</article>')


def render_selector_review(processing: ProcessingResult, parser_id: str) -> str:
    grouping, scoring = processing.grouping_report, processing.scoring_report
    if grouping is None or scoring is None:
        raise ValueError("grouping and scoring reports required for selector review")
    groups = {item.slot_id: item for item in grouping.groups}
    scores = {item.slot_id: item for item in scoring.groups}
    resolutions = {item.slot_id: item for item in processing.resolutions}
    originals = {item.candidate_id: item for report in processing.extraction_reports
                 for item in report.candidates}
    native = {item.slot_id: item.table for item in processing.primary_document.native_tables}
    sections: list[str] = []
    for slot in processing.primary_document.table_slots:
        group, resolution = groups[slot.slot_id], resolutions[slot.slot_id]
        score = scores.get(slot.slot_id)
        if score is None:
            result = ('<p class="empty">此 slot 未进入评分；'
                      f'分组状态：{_e(group.status)}，原因：{_e(group.unresolved_reason)}。</p>')
            if resolution.origin == "docling_native_fallback" and slot.slot_id in native:
                result += '<h3>Docling 原生回退表格（不是 winner）</h3>' + _table_grid(native[slot.slot_id])
        else:
            result = _facts((
                ("reference_source", score.text_reference.source),
                ("reference_word_count", len(score.text_reference.words)),
                ("reference_token_count", sum(item.count for item in score.text_reference.token_counts)),
                ("critical_reference_token_count", sum(item.count for item in score.text_reference.critical_token_counts)),
                ("candidate_count", len(score.candidates)),
                ("highest_total_score", _n(score.highest_total_score)),
                ("tied_top_candidate_ids", _list(score.tied_top_candidate_ids)),
                ("winner", score.selected_candidate_id),
                ("selection_reason", score.selection_reason),
            ))
            ordered = sorted(score.candidates, key=lambda item: (
                item.candidate_id != score.selected_candidate_id,
                -item.total_score,
                item.candidate_id,
            ))
            result += "".join(_scored_candidate(item, score, originals.get(item.candidate_id))
                              for item in ordered)
        sections.append(f'<section class="slot" data-slot-id="{_e(slot.slot_id)}">'
                        + _slot_head(slot, group, resolution) + result + '</section>')
    if not sections:
        sections.append('<p class="empty">本 PDF 没有表格 slot。</p>')
    return _page("TableSelector 人工审核", processing, parser_id,
                 "".join(sections), len(grouping.groups))


def render_preparation_review(processing: ProcessingResult, parser_id: str) -> str:
    prepared_by_slot = {item.slot_id: item for item in processing.prepared_tables}
    resolutions = {item.slot_id: item for item in processing.resolutions}
    sections: list[str] = []
    for slot in processing.primary_document.table_slots:
        prepared: PreparedTableContent = prepared_by_slot[slot.slot_id]
        decision = prepared.header_decision
        facts = _facts((
            ("table_id", prepared.table_id), ("origin", resolutions[slot.slot_id].origin),
            ("header_outcome", decision.outcome), ("header_reason", decision.reason),
            ("header_start_row", decision.header_start_row),
            ("header_end_row", decision.header_end_row),
        ))
        end = decision.header_end_row if decision.outcome == "identified" else None
        sections.append(f'<section class="slot" data-slot-id="{_e(slot.slot_id)}">'
                        + _slot_head(slot, resolution=resolutions[slot.slot_id])
                        + facts + '<h3>采用的物理表格</h3>'
                        + _table_grid(prepared.adopted_table, header_end_row=end)
                        + '<h3>完整序列化文本</h3>'
                        + f'<pre data-serialized-table="{_e(prepared.table_id)}">'
                          f'{_e(prepared.serialized_table.text)}</pre></section>')
    if not sections:
        sections.append('<p class="empty">本 PDF 没有表格 slot。</p>')
    return _page("TableContentPreparation 人工审核", processing, parser_id,
                 "".join(sections), len(prepared_by_slot))
