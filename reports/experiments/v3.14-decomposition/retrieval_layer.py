"""P3.1 retrieval layer (proposal v3.14, "分层评估"): each information item's completion rank in the
rankings of its own sub-questions (question text only, no phrasings, no merging), against the
original question's ranking (V3.13); and the ideal bound over all sub-questions. Also, for the
items, the rank after merging (only P3.1, round robin with the original-question route last), to
see whether the retrieval-layer gains survive.

Frozen v9 plans (cached), cached retrievals: no model calls. Reads mapping.json; writes
retrieval_layer.json and retrieval_layer.md next to this script.
Run: python reports/experiments/v3.14-decomposition/retrieval_layer.py
"""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

from rag.eval.judge import judge_case
from rag.eval.metrics import routed_completion_rank
from rag.eval.query_compare import (COMBOS, completion_vector, leads, make_planner, settings_for,
                                    verdict)
from rag.eval.runner import prepare
from rag.paths import Workspace
from rag.query import execution
from rag.query.planning import document_languages

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
NAMES = {"better": "变好", "worse": "变差", "mixed": "各有优势", "same": "不变"}


def fmt(v: list[float]) -> str:
    return "·".join("∞" if x == math.inf else str(int(x)) for x in v)


def fmt_leads(l: dict) -> str:
    r = lambda rs: "、".join(f"K={a}" if a == b else f"K={a}–{b}" for a, b in rs)
    return "；".join(([f"左领先 {r(l['left'])}"] if l["left"] else []) + ([f"右领先 {r(l['right'])}"] if l["right"] else []))


def main() -> None:
    from rag.cli import _assembly
    workspace = Workspace(ROOT)
    mapping = json.loads((HERE / "mapping.json").read_text(encoding="utf-8"))["cases"]
    planner = make_planner(workspace)
    rows: dict[str, dict] = {}
    for combo in COMBOS:
        ctx = prepare(workspace, _assembly(workspace, combo.config, "hybrid", combo.table_return_unit),
                      cache_retrievals=True)
        languages_of = document_languages(workspace.index_dir(combo.config))
        rows[combo.label] = {}
        for case in ctx.dataset.cases:
            if not case.answerable:
                continue
            scope = ctx.scope(case.question)
            documents = scope.document_ids
            languages = sorted({languages_of[d] for d in (documents or languages_of)})
            plan = planner.plan(case.question, languages, True, list(scope.codes))
            evidence = judge_case(case, ctx.chunks)
            ids = lambda units: [u.chunk_ids for u in units]
            original = ids(ctx.searcher.rank(case.question, documents).units)
            routes = {q.id: ids(ctx.searcher.rank(q.text, documents).units) for q in plan.questions}
            merged = ids(execution.planned(ctx.searcher, plan, settings_for(("P3.1",))[1], documents).units)
            m = mapping[case.case_id]["mapping"]
            items = {}
            for group in case.groups:
                schemes = [[evidence.excerpts[x].acceptable_sets for x in s.excerpt_ids] for s in group.schemes]
                mine = m[group.group_id] or list(routes)          # omitted: all sub-questions
                items[group.group_id] = {
                    "original": routed_completion_rank([original], schemes),
                    "mapped": routed_completion_rank([routes[q] for q in mine], schemes),
                    "ideal": routed_completion_rank(list(routes.values()), schemes),
                    "merged": routed_completion_rank([merged], schemes)}
            rows[combo.label][case.case_id] = items
        print(combo.label, flush=True)

    out = {"plan": "query_plan_v9 / v7, generation 0", "rows": rows}
    (HERE / "retrieval_layer.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    vec = lambda items, side: completion_vector(v[side] for v in items.values())
    lines = ["# P3.1 检索层（2026-10-28）", "",
             "冻结的 v9 规划，数据集 3.4.1。“原问题”= 原问题检索一次（V3.13）；“检索层”= 各信息项在对应子问题一路（只用原句）中的凑齐名次；"
             "“理想上限”= 各信息项在全部子问题各路中的凑齐名次；“合并后”= 只开 P3.1（轮流取，原问题一路排每圈最后）的返回顺序。"
             "判定按提案拍板问题 3（凑齐向量的支配关系）。", "",
             "## 1. 各组合的判定题数（变好 / 变差 / 各有优势 / 不变）", "",
             "| 组合 | 检索层 vs 原问题 | 理想上限 vs 原问题 | 合并后 vs 原问题 |", "|---|---|---|---|"]
    detail = []
    totals = {k: Counter() for k in ("mapped", "ideal", "merged")}
    for combo, cases in rows.items():
        cells = []
        for side in ("mapped", "ideal", "merged"):
            c = Counter(verdict(vec(items, "original"), vec(items, side)) for items in cases.values())
            totals[side].update(c)
            cells.append(f"{c['better']} / {c['worse']} / {c['mixed']} / {c['same']}")
        lines.append(f"| {combo} | " + " | ".join(cells) + " |")
        for cid, items in cases.items():
            l, r, g = vec(items, "original"), vec(items, "mapped"), vec(items, "merged")
            v = verdict(l, r)
            if v != "same" or verdict(l, g) != "same":
                detail.append((cid, combo, v, l, r, vec(items, "ideal"), g, verdict(l, g)))
    lines.append("| 合计 | " + " | ".join(f"{t['better']} / {t['worse']} / {t['mixed']} / {t['same']}"
                                         for t in totals.values()) + " |")
    lines += ["", "## 2. 有变化的题（凑齐向量：原问题 → 检索层；理想上限；合并后）", "",
              "| 题 | 组合 | 检索层 | 原问题 → 检索层 | 理想上限 | 合并后（判定） |", "|---|---|---|---|---|---|"]
    for cid, combo, v, l, r, i, g, gv in sorted(detail):
        extra = f"；{fmt_leads(leads(l, r))}" if v == "mixed" else ""
        lines.append(f"| {cid} | {combo} | {NAMES[v]} | {fmt(l)} → {fmt(r)}{extra} | {fmt(i)} | {fmt(g)}（{NAMES[gv]}） |")
    (HERE / "retrieval_layer.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
