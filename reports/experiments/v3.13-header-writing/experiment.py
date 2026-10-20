"""Header writing experiment: which way of writing a multi-row header in the indexed text helps retrieval.

Two writings, everything else equal (same header rows, no sections, rule chunk packing, 512 token budget):
- `path`: the header as one line of column paths, an upper label repeated over every column it spans
  ("Price (USD) / FOB | Price (USD) / CIF");
- `rows`: the header rows written as they are, a merged label once with `←` over the columns it spans.

Every table is written both ways; for one writing, the pool is all tables' chunks in that writing plus
the prose paragraphs. Per question: the best vector rank of its target chunks (any chunk of the table,
or the chunk holding the asked row) and that chunk's similarity, with bge-m3.

Usage (repository root):
  python reports/experiments/v3.13-header-writing/experiment.py review   # tables.html for review
  python reports/experiments/v3.13-header-writing/experiment.py run      # results.json
  python reports/experiments/v3.13-header-writing/experiment.py report   # results.html
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from material import PROSE, Spec, specs  # noqa: E402

from rag.ingest.chunkers.table_plan import plan_chunks  # noqa: E402
from rag.ingest.chunkers.tokens import E5TokenCounter  # noqa: E402
from rag.ingest.tables.markdown_rows import serialize_markdown_rows  # noqa: E402
from rag.ingest.tables.models import StructuredTable, TableCell, TableStructure  # noqa: E402
from rag.models import BoundingBox  # noqa: E402

BUDGET = 512
WRITINGS = {"path": "header_rules", "rows": "llm_annotation"}   # structure source selects the writing


def build_table(spec: Spec) -> tuple[StructuredTable, int]:
    """Cells of the header (merged as laid out) and the body; returns the table and its header depth."""
    def depth(children) -> int:
        if children is None:
            return 1
        return 1 + max(depth(c[1]) if isinstance(c, tuple) else 1 for c in children)
    levels = max(depth(children) for _, children in spec.header)
    cells: list[TableCell] = []

    def add(text: str, row: int, col: int, rows: int, cols: int) -> None:
        cells.append(TableCell(f"h{row}_{col}", text, row, row + rows, col, col + cols, rows, cols,
                               BoundingBox(col * 50, row * 10, col * 50 + 40, row * 10 + 8), ("body",), "native", ()))

    def place(label: str, children, row: int, col: int) -> int:
        """Place a header node at (row, col); return the columns it spans."""
        if children is None:
            add(label, row, col, levels - row, 1)
            return 1
        span = 0
        for child in children:
            child_label, grand = child if isinstance(child, tuple) else (child, None)
            if grand is None and not isinstance(child, tuple):
                add(child_label, row + 1, col + span, levels - row - 1, 1)
                span += 1
            else:
                span += place(child_label, grand, row + 1, col + span)
        add(label, row, col, 1, span)
        return span

    col = 0
    for label, children in spec.header:
        col += place(label, children, 0, col)
    for r, values in enumerate(spec.rows, start=levels):
        for c, value in enumerate(values):
            cells.append(TableCell(f"b{r}_{c}", value, r, r + 1, c, c + 1, 1, 1,
                                   BoundingBox(c * 50, r * 10, c * 50 + 40, r * 10 + 8), ("body",), "native", ()))
    table = StructuredTable(spec.key, "pymupdf", "lines", levels + len(spec.rows), col, tuple(cells), (), None, (), ())
    return table, levels


def chunks(spec: Spec, writing: str, counter: E5TokenCounter) -> list[dict]:
    table, levels = build_table(spec)
    structure = TableStructure(WRITINGS[writing], tuple(range(levels)))   # type: ignore[arg-type]
    serialized = serialize_markdown_rows(table, structure, table_node_id=spec.key)
    rows_of = {line.line_id: line.source_rows for line in serialized.lines}
    planned = plan_chunks(serialized, structure, lambda text: counter.count_passage(text) <= BUDGET)
    out = []
    for i, p in enumerate(planned):
        labels = {spec.rows[r - levels][0] for lid in p.line_ids for r in rows_of[lid] if r >= levels}
        out.append({"id": f"{spec.key}#{i}", "table": spec.key, "text": p.text, "rows": sorted(labels),
                    "tokens": counter.count_passage(p.text)})
    return out


def targets(spec: Spec, question, pieces: list[dict]) -> set[str]:
    own = [p for p in pieces if p["table"] == spec.key]
    if question.row is None:
        return {p["id"] for p in own}
    return {p["id"] for p in own if question.row in p["rows"]}


def review() -> None:
    from rag.reports.html import esc, page, text_block, write_assets
    from rag.reports.ingest import render_grid
    counter = E5TokenCounter()
    cards = []
    for spec in specs():
        table, levels = build_table(spec)
        written = {w: chunks(spec, w, counter) for w in WRITINGS}
        qs = "".join(f"<tr><td>{esc(q.kind)}</td><td>{esc(q.text)}</td><td>{esc(q.row or '本表任一块')}</td></tr>"
                     for q in spec.questions)
        sizes = "；".join(f"{name}：{len(c)} 块，{sum(x['tokens'] for x in c):,} token" for name, c in written.items())
        cards.append(
            f'<div class="card"><h3>{esc(spec.key)} · {esc(spec.title)}（{esc(spec.language)}，{len(spec.rows)} 行）</h3>'
            f'{render_grid(table, 8 + levels, frozenset(range(levels)))}'
            f'<table><tr><th>类型</th><th>问题</th><th>目标</th></tr>{qs}</table>'
            f'<div class="meta">{esc(sizes)}</div>'
            f'<details><summary>两种写法的第一块</summary><div class="meta">path（合成列名路径）</div>'
            f'{text_block(written["path"][0]["text"], 1200)}<div class="meta">rows（原样写多行表头）</div>'
            f'{text_block(written["rows"][0]["text"], 1200)}</details></div>')
    prose = "".join(f"<li>{esc(t)}</li>" for _, t in PROSE)
    body = (f'<h1>表头写法实验：测试材料</h1><div class="sub">12 张人造表（表头层级 2 / 3 × 列数 少 / 中 / 多 × 长度 短 / 长），'
            f'每表 4 题；另有 {len(PROSE)} 段正文作为竞争对象。表格只显示前几行。</div>'
            + "".join(cards) + f'<div class="card"><h3>竞争正文（{len(PROSE)} 段）</h3><ul>{prose}</ul></div>')
    root = HERE.parents[2]
    write_assets(root / "reports")
    (HERE / "tables.html").write_text(page("表头写法实验材料", body, root="../../"), encoding="utf-8")
    print(HERE / "tables.html")


def run() -> None:
    import numpy as np
    from rag.index.embedder import bge_m3
    counter = E5TokenCounter()
    embedder = bge_m3()
    all_specs = specs()
    results = []
    for writing in WRITINGS:
        pieces = [c for spec in all_specs for c in chunks(spec, writing, counter)]
        pieces += [{"id": f"prose#{i}", "table": None, "text": t, "rows": [], "tokens": counter.count_passage(t)}
                   for i, (_, t) in enumerate(PROSE)]
        vectors = np.array(embedder.encode_passages([p["text"] for p in pieces]))
        for spec in all_specs:
            for q in spec.questions:
                sims = vectors @ np.array(embedder.encode_query(q.text))
                order = sorted(range(len(pieces)), key=lambda i: (-sims[i], pieces[i]["id"]))
                wanted = targets(spec, q, pieces)
                rank, best = next((r, i) for r, i in enumerate(order, 1) if pieces[i]["id"] in wanted)
                results.append({"writing": writing, "table": spec.key, "kind": q.kind, "question": q.text,
                                "rank": rank, "similarity": round(float(sims[best]), 4), "chunk": pieces[best]["id"],
                                "chunks": sum(p["table"] == spec.key for p in pieces),
                                "tokens": sum(p["tokens"] for p in pieces if p["table"] == spec.key)})
    (HERE / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(HERE / "results.json")


def report() -> None:
    from statistics import mean
    from rag.reports.html import badge, esc, page, write_assets
    results = json.loads((HERE / "results.json").read_text(encoding="utf-8"))
    by = {w: {(r["table"], r["kind"]): r for r in results if r["writing"] == w} for w in WRITINGS}
    keys = list(by["path"])
    kinds = {"group": "上层列组", "cell": "某行某列", "topic": "整表主题", "rows": "行实体"}

    def summary(subset: list) -> str:
        p, r = [by["path"][k] for k in subset], [by["rows"][k] for k in subset]
        path_better = sum(a["rank"] < b["rank"] for a, b in zip(p, r))
        rows_better = sum(b["rank"] < a["rank"] for a, b in zip(p, r))
        return (f"<td class=num>{path_better}</td><td class=num>{rows_better}</td><td class=num>{len(subset) - path_better - rows_better}</td>"
                f"<td class=num>{mean(x['rank'] for x in p):.2f}</td><td class=num>{mean(x['rank'] for x in r):.2f}</td>"
                f"<td class=num>{mean(b['similarity'] - a['similarity'] for a, b in zip(p, r)):+.4f}</td>")
    groups = [("全部", keys)]
    for label, index, values in (("表头层级", 0, ("2", "3")), ("列数", 1, ("few", "mid", "many")),
                                 ("长度", 2, ("short", "long"))):
        groups += [(f"{label} {v}", [k for k in keys if k[0].split("-")[index] == v]) for v in values]
    groups += [(f"题型 {kinds[kind]}", [k for k in keys if k[1] == kind]) for kind in kinds]
    rows = "".join(f"<tr><td class=nw>{esc(name)}（{len(ks)} 题）</td>{summary(ks)}</tr>" for name, ks in groups)
    detail = "".join(
        f"<tr><td class=nw>{esc(k[0])}</td><td class=nw>{kinds[k[1]]}</td><td>{esc(by['path'][k]['question'])}</td>"
        f"<td class=num>{by['path'][k]['rank']}<div class=meta>{by['path'][k]['similarity']:.4f}</div></td>"
        f"<td class=num>{by['rows'][k]['rank']}<div class=meta>{by['rows'][k]['similarity']:.4f}</div></td>"
        f"<td>{badge('path 更好', 'ok') if by['path'][k]['rank'] < by['rows'][k]['rank'] else badge('rows 更好', 'warn') if by['rows'][k]['rank'] < by['path'][k]['rank'] else ''}</td></tr>"
        for k in keys)
    tokens = {w: sum({r["table"]: r["tokens"] for r in by[w].values()}.values()) for w in WRITINGS}
    chunks_ = {w: sum({r["table"]: r["chunks"] for r in by[w].values()}.values()) for w in WRITINGS}
    body = (f'<h1>表头写法实验：结果</h1><div class="sub">path = 合成列名路径（上层标签在它跨过的每一列各写一次）；rows = 原样写多行表头'
            f'（合并格写 ←）。每种写法下池子为 12 张表的全部块加 {len(PROSE)} 段正文；名次为目标块在向量排名中的最好名次（bge-m3）。'
            f'事先定的判定：名次变好与变差之比 ≥ 2 : 1 且平均名次更好才算胜出，否则视为无差别、选 token 更少的写法。</div>'
            f'<div class="card"><h3>汇总</h3><div class="scroll"><table><tr><th>范围</th><th class=num>path 名次更好</th>'
            f'<th class=num>rows 名次更好</th><th class=num>名次相同</th><th class=num>平均名次 path</th><th class=num>平均名次 rows</th>'
            f'<th class=num>相似度 rows − path（平均）</th></tr>{rows}</table></div>'
            f'<div class="meta">12 张表入库文本合计：path {chunks_["path"]} 块、{tokens["path"]:,} token；rows {chunks_["rows"]} 块、{tokens["rows"]:,} token。</div></div>'
            f'<div class="card"><h3>逐题</h3><div class="scroll" data-grid><table><tr><th>表</th><th>题型</th><th>问题</th>'
            f'<th class=num>path 名次<br>相似度</th><th class=num>rows 名次<br>相似度</th><th></th></tr>{detail}</table></div></div>')
    write_assets(HERE.parents[2] / "reports")
    (HERE / "results.html").write_text(page("表头写法实验结果", body, root="../../"), encoding="utf-8")
    print(HERE / "results.html")


if __name__ == "__main__":
    {"review": review, "run": run, "report": report}[sys.argv[1]]()
