"""Per-document audit pages: 1-解析.html, 2-表格.html (if any tables), 3-分块.html."""

from __future__ import annotations

import shutil
from collections import Counter
from statistics import median

from rag.index.manifest import DocumentEntry
from rag.ingest.pipeline import ProcessedDocument
from rag.ingest.tables.models import GroupScoringResult, StructuredTable, TableCandidate
from rag.jsonio import write_atomic
from rag.models import ChunkBatch, ListNode, TableNode, TextNode
from rag.paths import Workspace
from rag.reports.html import badge, esc, filters, page, text_block

TEXT_KINDS = {"title": "标题", "paragraph": "段落", "header": "页眉", "footer": "页脚",
              "footnote": "脚注", "caption": "图表标题", "table_note": "表注",
              "other_text": "其他文本"}
CHUNK_KINDS = {"text": "正文", "list": "列表", "table": "表格"}
ADMISSION = {
    "matched_single_table_slot": "进入候选",
    "no_eligible_table_slot_on_page": "该页没有可对应的表格",
    "no_table_slot_passed_bidirectional_coverage": "与表格位置重叠不足",
    "multiple_table_slots_passed_bidirectional_coverage": "同时对应多个表格，无法归属",
}
SELECTION = {"highest_total_score": "总分最高", "tool_priority_tiebreak": "总分并列，按工具优先级",
             "candidate_id_tiebreak": "总分并列，按候选编号"}
METRIC_LABELS = (("text_f1", "文本覆盖"), ("critical_token_integrity", "关键数字完整"),
                 ("shape_support", "行列形状共识"), ("blank_anomaly", "空白异常"))
PAGE_NAMES = ("1-解析.html", "2-表格.html", "3-分块.html")


def render_grid(table: StructuredTable | TableCandidate, max_rows: int | None = None) -> str:
    """Draw a table from its cell grid, honouring merged cells."""
    rows = table.row_count or 0
    cols = table.column_count or 0
    placed = [c for c in table.cells if c.start_row_offset_idx is not None
              and c.start_col_offset_idx is not None]
    if not rows or not cols or not placed:
        loose = " | ".join(c.text or "" for c in table.cells)
        return f'<div class="meta">（无法排成网格）</div>{text_block(loose or table.unplaced_text or "")}'
    starts = {(c.start_row_offset_idx, c.start_col_offset_idx): c for c in placed}
    covered: set[tuple[int, int]] = set()
    html_rows = []
    shown = rows if max_rows is None else min(rows, max_rows)
    for r in range(shown):
        cells = []
        for col in range(cols):
            if (r, col) in covered:
                continue
            cell = starts.get((r, col))
            if cell is None:
                cells.append("<td></td>")
                continue
            rs = max(1, (cell.end_row_offset_idx or r + 1) - r)
            cs = max(1, (cell.end_col_offset_idx or col + 1) - col)
            for dr in range(rs):
                for dc in range(cs):
                    covered.add((r + dr, col + dc))
            span = (f' rowspan="{rs}"' if rs > 1 else "") + (f' colspan="{cs}"' if cs > 1 else "")
            cells.append(f"<td{span}>{esc(cell.text or '')}</td>")
        html_rows.append("<tr>" + "".join(cells) + "</tr>")
    more = (f'<div class="meta">…另有 {rows - shown} 行</div>' if shown < rows else "")
    return f'<div class="scroll"><table>{"".join(html_rows)}</table></div>{more}'


def _header(processed: ProcessedDocument, entry: DocumentEntry, config_name: str,
            current: int) -> str:
    links = []
    for index, name in enumerate(PAGE_NAMES):
        if index == 1 and not processed.parse.primary.table_slots:
            continue
        label = name.removesuffix(".html")
        links.append(f"<b>{esc(label)}</b>" if index == current else f'<a href="{esc(name)}">{esc(label)}</a>')
    return (f"<h1>{esc(entry.document_name)}</h1>"
            f'<div class="sub">配置 {esc(config_name)} · {entry.page_count} 页 · '
            f'{entry.chunk_count} 个 chunk · {entry.table_count} 张表 · 入库于 {esc(entry.built_at)}'
            f'　|　{"　".join(links)}</div>')


def _crumbs(config_name: str) -> list[tuple[str, str]]:
    return [("报告首页", "../../../index.html"), (f"配置 {config_name}", f"../../../index.html#{config_name}")]


def _node_page(node) -> int:
    return node.sources[0].page_number if node.sources else 0


def parse_page(processed: ProcessedDocument, entry: DocumentEntry, config_name: str) -> str:
    document = processed.document
    kinds = Counter(TEXT_KINDS.get(n.kind, n.kind) if isinstance(n, TextNode)
                    else "列表" if isinstance(n, ListNode) else "表格" for n in document.nodes)
    summary = "　".join(f"{k} {v}" for k, v in kinds.most_common())
    warnings = Counter(w.code for w in document.warnings)
    warn_html = ("" if not warnings else "<div class='meta'>处理提示：" +
                 "；".join(f"{esc(code)} ×{n}" for code, n in warnings.items()) + "</div>")
    by_page: dict[int, list[str]] = {}
    for node in document.nodes:
        if isinstance(node, TextNode):
            html = f'{badge(TEXT_KINDS.get(node.kind, node.kind))}{text_block(node.text)}'
        elif isinstance(node, ListNode):
            items = "\n".join(("  " * item.level) + (item.ordinal or "•") + " " + item.text
                              for item in node.items)
            html = f'{badge("列表")}{text_block(items)}'
        else:
            assert isinstance(node, TableNode)
            origin = ("候选胜出：" + node.table.tool + "/" + node.table.strategy
                      if node.origin == "selected_winner" else "采用解析器原生表格")
            html = (f'{badge("表格", "warn")} <span class="meta">{esc(origin)}，'
                    f'详见 <a href="2-表格.html">表格页</a></span>{render_grid(node.table, 12)}')
        by_page.setdefault(_node_page(node), []).append(f'<div class="item">{html}</div>')
    sections = "".join(f'<div class="card"><h3>第 {p} 页</h3>{"".join(items)}</div>'
                       for p, items in sorted(by_page.items()))
    body = (_header(processed, entry, config_name, 0) +
            f'<div class="card"><b>解析结果</b>：{esc(summary)}{warn_html}'
            f'<div class="meta">按阅读顺序列出解析器识别出的元素；原始解析输出保存在 '
            f'.rag/indexes/{esc(config_name)}/documents/{esc(entry.folder)}/parse-native.json</div></div>'
            + sections)
    return page(f"解析 · {entry.document_name}", body, crumbs=_crumbs(config_name))


def _score_table(scored: GroupScoringResult) -> str:
    rows = []
    for candidate in sorted(scored.candidates, key=lambda c: -c.total_score):
        raw = candidate.raw_metrics
        values = {"text_f1": raw.text_coverage.f1, "critical_token_integrity": raw.critical_tokens.integrity,
                  "shape_support": raw.grid_shape.support, "blank_anomaly": raw.blank_grid.blank_anomaly}
        cells = "".join(f'<td class="num">{"—" if values[key] is None else f"{values[key]:.2f}"}</td>'
                        for key, _ in METRIC_LABELS)
        mark = " ★" if candidate.candidate_id == scored.selected_candidate_id else ""
        rows.append(f"<tr><td>{esc(candidate.tool)}/{esc(candidate.strategy)}{mark}</td>{cells}"
                    f'<td class="num"><b>{candidate.total_score:.2f}</b></td></tr>')
    head = "".join(f'<th class="num">{esc(label)}</th>' for _, label in METRIC_LABELS)
    return (f'<div class="scroll"><table><tr><th>候选</th>{head}<th class="num">总分</th></tr>'
            f'{"".join(rows)}</table></div><div class="meta">四项指标两两比较后折算为 0–1 的相对分，'
            f'总分为四项相对分的平均；空白异常越低越好。规则见 docs/rules/table-selection.md</div>')


def tables_page(processed: ProcessedDocument, entry: DocumentEntry, config_name: str) -> str:
    candidates = {c.candidate_id: c for r in processed.extraction_reports for c in r.candidates}
    admissions = ({a.candidate_id: a for a in processed.grouping.candidate_admission_results}
                  if processed.grouping else {})
    scores = {g.slot_id: g for g in processed.scoring.groups} if processed.scoring else {}
    resolutions = {r.slot_id: r for r in processed.resolutions}
    prepared = {p.slot_id: p for p in processed.prepared_tables}
    slots = processed.parse.primary.table_slots
    winners = sum(r.origin == "selected_winner" for r in processed.resolutions)
    executions = [e for r in processed.extraction_reports for e in r.executions]
    failed = [e for e in executions if e.status in {"failed", "not_started", "completed_with_page_failures"}]
    tool_line = "　".join(f"{e.tool}/{e.strategy} {sum(len(p.candidate_ids) for p in e.pages)} 张"
                          + (" ⚠" if e in failed else "") for e in executions)
    summary = (f'<div class="card"><b>{len(slots)} 张表</b>：{winners} 张采用候选胜出者，'
               f'{len(slots) - winners} 张采用解析器原生表格。'
               f'<div class="meta">各工具找到的候选：{esc(tool_line) or "（未配置表格提取器）"}</div>'
               + ("".join(f'<div class="meta">⚠ {esc(e.tool)}/{esc(e.strategy)}：{esc(e.status)} '
                          f'{esc(e.error_message or "")}</div>' for e in failed)) + "</div>")
    sections = []
    for number, slot in enumerate(slots, start=1):
        resolution = resolutions[slot.slot_id]
        scored = scores.get(slot.slot_id)
        prep = prepared[slot.slot_id]
        members = [cid for cid, a in admissions.items() if a.matched_slot_id == slot.slot_id]
        if resolution.origin == "selected_winner" and scored:
            decision = (f'{badge("采用候选", "ok")} {esc(prep.adopted_table.tool)}/'
                        f'{esc(prep.adopted_table.strategy)} · {SELECTION.get(scored.selection_reason, "")}'
                        f'（总分 {scored.highest_total_score:.2f}，{len(scored.candidates)} 个候选参与评分）')
        else:
            decision = f'{badge("原生表格", "warn")} 没有可评分的候选，保留解析器识别的表格结构'
        cards = []
        totals = {c.candidate_id: c.total_score for c in scored.candidates} if scored else {}
        ordered = sorted(members, key=lambda cid: (cid != resolution.selected_candidate_id,
                                                   -totals.get(cid, 0.0), cid))
        for cid in ordered:
            candidate = candidates[cid]
            chosen = cid == resolution.selected_candidate_id
            cards.append(f'<div class="card{" winner" if chosen else ""}"><h3>{esc(candidate.tool)}/'
                         f'{esc(candidate.strategy)}{" ★ 胜出" if chosen else ""}</h3>'
                         f'<div class="meta">{candidate.row_count or "?"} 行 × {candidate.column_count or "?"} 列</div>'
                         f'{render_grid(candidate, 15)}</div>')
        page_candidates = [c for c in candidates.values()
                           if c.regions and c.regions[0].page_number == slot.page_number
                           and c.candidate_id not in members]
        others = "".join(
            f"<tr><td>{esc(c.tool)}/{esc(c.strategy)}</td><td>"
            f"{esc(ADMISSION.get(admissions[c.candidate_id].reason_codes[0], admissions[c.candidate_id].reason_codes[0]) if admissions.get(c.candidate_id) and admissions[c.candidate_id].reason_codes else (admissions[c.candidate_id].deferred_reason if admissions.get(c.candidate_id) else ''))}"
            f"</td></tr>" for c in page_candidates)
        header = prep.header_decision
        header_line = ("表头已识别：第 " + str((header.header_start_row or 0) + 1) + "–" + str(header.header_end_row) + " 行"
                       if header.outcome == "identified" else "表头未确定（按行列位置描述单元格）")
        sections.append(
            f'<h2 id="t{number}">表 {number} · 第 {slot.page_number or "?"} 页</h2>'
            f'<div class="card">{decision}<div class="meta">{esc(header_line)}</div>'
            + (_score_table(scored) if scored else "") + "</div>"
            + (f'<div class="grid">{"".join(cards[:3])}</div>' if cards else "")
            + (f'<details><summary>其余 {len(cards) - 3} 个候选表</summary>'
               f'<div class="grid">{"".join(cards[3:])}</div></details>' if len(cards) > 3 else "")
            + (f'<details><summary>本页未进入该表评分的其他候选（{len(page_candidates)}）</summary>'
               f'<table><tr><th>候选</th><th>原因</th></tr>{others}</table></details>' if page_candidates else "")
            + f'<details><summary>入库文本（{len(prep.serialized_table.text)} 字符）</summary>'
              f'{text_block(prep.serialized_table.text)}</details>')
    body = _header(processed, entry, config_name, 1) + summary + "".join(sections)
    return page(f"表格 · {entry.document_name}", body, crumbs=_crumbs(config_name))


def chunks_page(processed: ProcessedDocument, batch: ChunkBatch, entry: DocumentEntry,
                config_name: str) -> str:
    chunks = batch.chunks
    lengths = [len(c.text) for c in chunks]
    tokens = [c.token_count for c in chunks if c.token_count is not None]
    size = (f"token 数 最小 {min(tokens)} / 中位 {int(median(tokens))} / 最大 {max(tokens)}"
            if tokens else f"字符数 最小 {min(lengths)} / 中位 {int(median(lengths))} / 最大 {max(lengths)}")
    near_limit = [c for c in chunks if c.token_count and c.token_count > 480]
    tiny = [c for c in chunks if len(c.text) < 30]
    kinds = Counter(CHUNK_KINDS.get(c.kind, c.kind) for c in chunks)
    cards = []
    for chunk in chunks:
        pages = sorted({s.page_number for src in chunk.sources for s in src.page_spans})
        tags = ["all", chunk.kind] + (["tiny"] if chunk in tiny else []) + (["split"] if chunk.fragment_count > 1 else [])
        size_text = f"{chunk.token_count} tokens" if chunk.token_count else f"{len(chunk.text)} 字符"
        split = f" · 拆分 {chunk.fragment_index + 1}/{chunk.fragment_count}" if chunk.fragment_count > 1 else ""
        repeated = [s for s in chunk.sources if s.repeated_context]
        sources = "".join(f"<li>{esc(s.node_id)} [{s.source_text_start}–{s.source_text_end}]"
                          f"{' 重复上下文：' + esc(s.context_kind) if s.repeated_context else ''}</li>"
                          for s in chunk.sources)
        cards.append(
            f'<div class="card" data-tags="{" ".join(tags)}"><h3>#{chunk.chunk_index} · '
            f'{esc(CHUNK_KINDS.get(chunk.kind, chunk.kind))} · 第 {",".join(map(str, pages))} 页 · '
            f'{size_text}{split}</h3>'
            + (f'<div class="meta">含 {len(repeated)} 段为保持可读而重复的上下文（表头/上级列表项/重叠）</div>' if repeated else "")
            + f'{text_block(chunk.text)}<details><summary>{esc(chunk.chunk_id)} · 来源</summary>'
              f'<ul class="meta">{sources}</ul></details></div>')
    notes = []
    if near_limit:
        notes.append(f"{len(near_limit)} 个接近 512 token 上限")
    if tiny:
        notes.append(f"{len(tiny)} 个少于 30 字符")
    summary = (f'<div class="card"><b>{len(chunks)} 个 chunk</b>：'
               + "　".join(f"{k} {v}" for k, v in kinds.items()) + f'<div class="meta">{size}'
               + (f"　·　注意：{'；'.join(notes)}" if notes else "") + "</div></div>")
    filter_bar = filters([("all", "全部", len(chunks))] +
                         [(kind, label, sum(c.kind == kind for c in chunks))
                          for kind, label in CHUNK_KINDS.items() if any(c.kind == kind for c in chunks)] +
                         ([("split", "被拆分的", sum(c.fragment_count > 1 for c in chunks))]
                          if any(c.fragment_count > 1 for c in chunks) else []) +
                         ([("tiny", "过短的", len(tiny))] if tiny else []))
    body = _header(processed, entry, config_name, 2) + summary + filter_bar + "".join(cards)
    return page(f"分块 · {entry.document_name}", body, crumbs=_crumbs(config_name), scripts=True)


def write_document_reports(workspace: Workspace, config_name: str, processed: ProcessedDocument,
                           batch: ChunkBatch, entry: DocumentEntry) -> None:
    folder = workspace.ingest_reports(config_name) / entry.folder
    if folder.exists():
        shutil.rmtree(folder)
    write_atomic(folder / PAGE_NAMES[0], parse_page(processed, entry, config_name))
    if processed.parse.primary.table_slots:
        write_atomic(folder / PAGE_NAMES[1], tables_page(processed, entry, config_name))
    write_atomic(folder / PAGE_NAMES[2], chunks_page(processed, batch, entry, config_name))
