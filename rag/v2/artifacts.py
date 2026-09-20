"""Stage and verify immutable V2 document artifacts."""

from __future__ import annotations

import hashlib
import html
import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from rag.models import ArtifactStageResult, V2DocumentChunk
from rag.v2.document_models import ParsedDocument, TableNode
from rag.v2.table_scoring import ScoringReport
from rag.v2.table_selection import GroupingReport


@dataclass(frozen=True)
class ArtifactEnvelope:
    schema_version: str
    pipeline_id: Literal["v2"]
    document_id: str
    build_id: str
    file_hash: str
    build_config_fingerprint: str
    created_at: str
    payload: Any

    def __post_init__(self) -> None:
        if self.pipeline_id != "v2":
            raise ValueError("ArtifactEnvelope.pipeline_id 必须是 v2。")
        required = (self.schema_version, self.document_id, self.build_id, self.file_hash,
                    self.build_config_fingerprint, self.created_at)
        if any(not isinstance(item, str) or not item for item in required):
            raise ValueError("ArtifactEnvelope 必需字符串不能为空。")


@dataclass(frozen=True)
class SelectionSlotSummary:
    slot_id: str
    status: str
    deferred_reason: str | None
    docling_ref: str


@dataclass(frozen=True)
class SelectionGroupSummary:
    group_id: str
    slot_id: str
    status: str
    unresolved_reason: str | None
    member_candidate_ids: tuple[str, ...]


@dataclass(frozen=True)
class SelectionWinnerSummary:
    group_id: str
    slot_id: str
    candidate_id: str
    tool: str
    strategy: str
    selection_reason: str
    total_score: float


@dataclass(frozen=True)
class TableSelectionSummary:
    slots: tuple[SelectionSlotSummary, ...]
    groups: tuple[SelectionGroupSummary, ...]
    winners: tuple[SelectionWinnerSummary, ...]
    warnings: tuple[Any, ...]


def _json_value(value: Any) -> Any:
    if is_dataclass(value):
        return {field: _json_value(item) for field, item in asdict(value).items()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, float) and not (float("-inf") < value < float("inf")):
        raise ValueError("artifact JSON 禁止非有限浮点数。")
    return value


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(_json_value(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _write_verified(path: Path, payload: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    readback = path.read_bytes()
    if readback != payload:
        raise OSError(f"artifact 回读不一致：{path}")
    return hashlib.sha256(readback).hexdigest()


def selection_summary(grouping: GroupingReport, scoring: ScoringReport) -> TableSelectionSummary:
    scores = {item.group_id: item for item in scoring.groups}
    winners = []
    for group in grouping.groups:
        if group.status != "ready_for_scoring":
            continue
        result = scores.get(group.group_id)
        if result is None:
            raise ValueError(f"ready group {group.group_id} 缺少评分结果。")
        winner = next(item for item in result.candidates if item.candidate_id == result.selected_candidate_id)
        winners.append(SelectionWinnerSummary(
            group.group_id, group.slot_id, winner.candidate_id, winner.tool, winner.strategy,
            result.selection_reason, result.highest_total_score,
        ))
    return TableSelectionSummary(
        tuple(SelectionSlotSummary(item.slot_id, item.status, item.deferred_reason, item.docling_table_ref)
              for item in grouping.table_slots),
        tuple(SelectionGroupSummary(item.group_id, item.slot_id, item.status, item.unresolved_reason,
                                    item.member_candidate_ids) for item in grouping.groups),
        tuple(winners), grouping.warnings + scoring.warnings,
    )


def _render_table_grid(node: TableNode) -> str:
    table = node.table
    if table.row_count is None or table.column_count is None:
        return '<p class="grid-unavailable">无法根据 winner 数据恢复二维网格。</p>'
    starts = {
        (cell.start_row_offset_idx, cell.start_col_offset_idx): cell
        for cell in table.cells
        if cell.start_row_offset_idx is not None and cell.start_col_offset_idx is not None
    }
    covered = {
        (row, col)
        for cell in table.cells
        if cell.start_row_offset_idx is not None
        for row in range(cell.start_row_offset_idx, cell.end_row_offset_idx or 0)
        for col in range(cell.start_col_offset_idx or 0, cell.end_col_offset_idx or 0)
    }
    identified = node.header.outcome == "identified"
    header_start = node.header.header_start_row or 0
    header_end = node.header.header_end_row or 0
    rows = []
    for row in range(table.row_count):
        cells = []
        for col in range(table.column_count):
            cell = starts.get((row, col))
            if cell is None:
                if (row, col) in covered:
                    continue
                cells.append('<td class="empty-cell" aria-label="空单元格">&nbsp;</td>')
                continue
            is_header = identified and row < header_end and (cell.end_row_offset_idx or row) > header_start
            tag = "th" if is_header else "td"
            classes = ' class="identified-header"' if is_header else ""
            rowspan = f' rowspan="{cell.row_span}"' if (cell.row_span or 1) > 1 else ""
            colspan = f' colspan="{cell.col_span}"' if (cell.col_span or 1) > 1 else ""
            text = html.escape(cell.text or "") or "&nbsp;"
            cells.append(f"<{tag}{classes}{rowspan}{colspan}>{text}</{tag}>")
        rows.append(f"<tr>{''.join(cells)}</tr>")
    return f'<div class="table-scroll"><table class="winner-table"><tbody>{"".join(rows)}</tbody></table></div>'


def _winner_html(parsed: ParsedDocument, chunks: tuple[V2DocumentChunk, ...], summary: TableSelectionSummary) -> str:
    winner_by_slot = {item.slot_id: item for item in summary.winners}
    sections = []
    for node in parsed.nodes:
        if not isinstance(node, TableNode) or node.slot_id not in winner_by_slot:
            continue
        winner = winner_by_slot[node.slot_id]
        identified = node.header.outcome == "identified"
        status = "表头识别成功" if identified else "表头识别失败"
        status_class = "success" if identified else "failure"
        grid = _render_table_grid(node)
        relevant = tuple(
            chunk for chunk in chunks
            if any(source.node_id == node.node_id for source in chunk.sources)
        )
        embedding_text = "".join(
            f'<div class="embedding-item"><h4>Embedding 文本 {index}</h4>'
            f'<pre class="embedding-text">{html.escape(chunk.text)}</pre></div>'
            for index, chunk in enumerate(relevant, start=1)
        ) or '<p class="grid-unavailable">没有找到该表格对应的最终 Embedding 文本。</p>'
        sections.append(
            f"<section data-slot-id=\"{html.escape(node.slot_id)}\" "
            f"data-header-outcome=\"{html.escape(node.header.outcome)}\"><div class=\"section-title\">"
            f"<h2>Winner 表格 {len(sections) + 1}</h2><span class=\"status {status_class}\">{status}</span></div>"
            f"<p class=\"details\">候选：{html.escape(winner.candidate_id)}　工具：{html.escape(winner.tool)}　"
            f"策略：{html.escape(winner.strategy)}　原因：{html.escape(node.header.reason)}</p>"
            f"{grid}<h3>表格转化后的 Embedding 文本</h3>{embedding_text}</section>"
        )
    body_sections = "".join(sections) if sections else (
        '<p class="grid-unavailable">本 PDF 未选出 winner 表格。</p>'
    )
    return (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{html.escape(parsed.document_name)} · Winner 表格</title><style>"
        "body{font-family:Segoe UI,Microsoft YaHei,sans-serif;max-width:1500px;margin:0 auto;padding:24px;color:#17202a;background:#f4f6f8}"
        "h1{margin-bottom:6px}section{background:#fff;border:1px solid #d9dee5;border-radius:10px;padding:20px;margin:24px 0;box-shadow:0 2px 8px #0000000d}"
        ".section-title{display:flex;align-items:center;gap:14px;flex-wrap:wrap}.section-title h2{margin:0}.status{font-weight:700;padding:5px 10px;border-radius:999px}"
        ".status.success{color:#176b37;background:#dff4e7}.status.failure{color:#9a3412;background:#ffedd5}.details{color:#5d6875;font-size:14px}"
        ".table-scroll{overflow:auto;margin:16px 0}.winner-table{border-collapse:collapse;min-width:100%;background:white}.winner-table td,.winner-table th{border:1px solid #7d8793;padding:8px 10px;vertical-align:top;text-align:left;white-space:pre-wrap}"
        ".winner-table .identified-header{background:#fff2a8;color:#332b00;font-weight:700}.empty-cell{background:#fafafa}.embedding-text{white-space:pre-wrap;background:#f7f8fa;border:1px solid #d9dee5;border-radius:6px;padding:14px;line-height:1.55}"
        ".grid-unavailable{padding:14px;background:#fff3cd;border:1px solid #ffe69c}</style>"
        f"</head><body data-winner-count=\"{len(summary.winners)}\"><h1>{html.escape(parsed.document_name)}</h1>"
        f"<p>本 PDF 共选出 {len(summary.winners)} 张 winner 表格。黄色背景表示识别出的表头区域。</p>"
        f"{body_sections}</body></html>\n"
    )


def stage_artifacts(
    *,
    artifacts_root: Path,
    raw_document: Any,
    parsed: ParsedDocument,
    chunks: tuple[V2DocumentChunk, ...],
    grouping: GroupingReport,
    scoring: ScoringReport,
    build_id: str,
    build_config_fingerprint: str,
    diagnostics_enabled: bool,
    created_at: str | None = None,
) -> ArtifactStageResult:
    created = created_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    from rag.document_registry import make_artifact_document_name

    artifact_document = make_artifact_document_name(parsed.relative_path, parsed.document_id)
    staging = artifacts_root / "v2" / "staging" / artifact_document / build_id
    raw_path = staging / "raw-docling-document.json"
    parsed_path = staging / "parsed-document.json"
    chunks_path = staging / "chunks.json"
    summary_path = staging / "selection-summary.json"
    review_path = staging / "winner-review.html"
    diagnostics_path = staging / "diagnostics" if diagnostics_enabled else None
    summary = selection_summary(grouping, scoring)
    common = dict(pipeline_id="v2", document_id=parsed.document_id, build_id=build_id,
                  file_hash=parsed.file_hash, build_config_fingerprint=build_config_fingerprint,
                  created_at=created)
    _write_verified(raw_path, _json_bytes(raw_document.export_to_dict()))
    _write_verified(parsed_path, _json_bytes(ArtifactEnvelope("parsed_document_v1", payload=parsed, **common)))
    _write_verified(chunks_path, _json_bytes(ArtifactEnvelope(
        "document_chunks_v1", payload={"chunks": chunks}, **common)))
    _write_verified(summary_path, _json_bytes(ArtifactEnvelope(
        "table_selection_summary_v1", payload=summary, **common)))
    _write_verified(review_path, _winner_html(parsed, chunks, summary).encode("utf-8"))
    if diagnostics_path is not None:
        diagnostics_path.mkdir(parents=True, exist_ok=True)
        _write_verified(diagnostics_path / "grouping.json", _json_bytes(grouping))
        _write_verified(diagnostics_path / "scoring.json", _json_bytes(scoring))
    # Parse back JSON and verify every envelope identity before returning a publishable stage.
    json.loads(raw_path.read_text(encoding="utf-8"))
    for path, schema in (
        (parsed_path, "parsed_document_v1"),
        (chunks_path, "document_chunks_v1"),
        (summary_path, "table_selection_summary_v1"),
    ):
        loaded = json.loads(path.read_text(encoding="utf-8"))
        expected = {"schema_version": schema, **common}
        if any(loaded.get(key) != value for key, value in expected.items()):
            raise OSError(f"artifact envelope 身份回读不一致：{path}")
    return ArtifactStageResult(staging, raw_path, parsed_path, chunks_path, summary_path,
                               review_path, diagnostics_path, len(summary.winners), len(chunks))
