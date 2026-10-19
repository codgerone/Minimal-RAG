"""Review page for the correct table structure answers: each indexed table drawn with its answer marked."""

from __future__ import annotations

import json
from pathlib import Path

from rag.eval.table_structure import find_answer, load_answers
from rag.ingest.tables.models import StructuredTable, TableCell
from rag.models import BoundingBox, PageSpan
from rag.paths import Workspace
from rag.reports.html import badge, esc, page, write_assets
from rag.reports.ingest import GRID_LEGEND, render_grid


def _table(data: dict) -> StructuredTable:
    cells = tuple(TableCell(c["cell_id"], c["text"], c["start_row_offset_idx"], c["end_row_offset_idx"],
                            c["start_col_offset_idx"], c["end_col_offset_idx"], c["row_span"], c["col_span"],
                            BoundingBox(**c["bbox"]) if c.get("bbox") else None, tuple(c["roles"]),
                            c["span_source"], tuple(c["source_refs"])) for c in data["cells"])
    return StructuredTable(data["table_id"], data["tool"], data["strategy"], data["row_count"],
                           data["column_count"], cells, (), data.get("unplaced_text"), (), ())


def _spans(node: dict) -> tuple[PageSpan, ...]:
    return tuple(PageSpan(s["page_number"], BoundingBox(**s["bbox"]) if s.get("bbox") else None, s["source_ref"])
                 for s in node["sources"])


def write_answers_page(workspace: Workspace, config_name: str) -> Path:
    """reports/table-structure/index.html, from the tables in this config's index."""
    folder = workspace.root / "eval" / "table-structure"
    tabs, panels, counts = [], [], [0, 0]
    for doc in sorted((workspace.index_dir(config_name) / "documents").iterdir()):
        parsed = json.loads((doc / "parsed.json").read_text(encoding="utf-8"))
        answers = load_answers(folder, parsed["document_id"])
        cards = []
        for number, node in enumerate((n for n in parsed["nodes"] if "table" in n), start=1):
            pages = "、".join(str(s["page_number"]) for s in node["sources"])
            answer = find_answer(answers, _spans(node)) if answers else None
            counts[answer is not None] += 1
            if answer is None:
                body = badge("没有对应的答案", "bad") + render_grid(_table(node["table"]))
            else:
                note = f'<div class="meta">说明：{esc(answer.note)}</div>' if answer.note else ""
                body = note + render_grid(_table(node["table"]), annotation=answer.annotation)
            cards.append(f'<div class="card"><h3>表 {number} · 第 {pages} 页</h3>{body}</div>')
        key = f"d{len(tabs)}"
        tabs.append(f'<a href="#{key}" data-tab="{key}">{esc(parsed["document_name"])}（{len(cards)} 张）</a>')
        panels.append(f'<section data-panel="{key}"{" class=hidden" if panels else ""}>{"".join(cards)}</section>')
    body = (f'<h1>表格结构正确答案</h1><div class="sub">eval/table-structure/ 中的答案，画在配置 {esc(config_name)} '
            f'解析出的表格上；表按页面坐标对应答案，行号、列号即网格的行列号。共 {sum(counts)} 张表，'
            f'{counts[1]} 张找到答案。请逐张核对标色是否正确。</div>{GRID_LEGEND}'
            f'<div class="tabs">{"".join(tabs)}</div>{"".join(panels)}')
    out = workspace.reports / "table-structure" / "index.html"
    write_assets(workspace.reports)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page("表格结构正确答案", body, root="../", crumbs=[("报告首页", "../index.html")]),
                   encoding="utf-8")
    return out
