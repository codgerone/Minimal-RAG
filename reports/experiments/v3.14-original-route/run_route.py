"""Original-question route: P3.1 alone (each sub-question's own text only), without vs with the
original question as the last route, on the 13 combinations. Better / worse per question by the
proposal's question 3 (rag.eval.query_compare.evidence_change, threshold ln 1.5); class per
combination by question 3, overall by question 5. Writes summary.json and tables.md.
Run from the repository root."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import plan_inputs, planner  # noqa: E402
from rag.cli import _assembly  # noqa: E402
from rag.eval.judge import judge_case  # noqa: E402
from rag.eval.metrics import completion_ranks  # noqa: E402
from rag.eval.query_compare import COMBOS, direction, evidence_change, evidence_positions  # noqa: E402
from rag.eval.runner import prepare  # noqa: E402
from rag.paths import Workspace  # noqa: E402
from rag.query import execution  # noqa: E402

KS = (3, 6, 9, 12)
SIDES = ("base", "without", "with")   # V3.13; P3.1 without / with the original route
ws = Workspace(Path.cwd())
p = planner(ws)


def classify(better: int, worse: int) -> str:
    if worse <= 1 and better > worse:
        return "固定启用"
    if better >= 1 and worse >= 2:
        return "V3.15 补救候选"
    return "不采用"


def complete(needed: dict, k: int) -> bool:
    return all(x is not None and x <= k for x in needed.values())


result = {}
for combo in COMBOS:
    ctx = prepare(ws, _assembly(ws, combo.config, "hybrid", combo.table_return_unit), cache_retrievals=True)
    ctx.warm_up()
    rows = {}
    for case in ctx.dataset.cases:
        if not case.answerable:
            continue
        scope = ctx.scope(case.question)
        languages, codes = plan_inputs(ws, scope)
        plan = p.plan(case.question, languages, True, codes)
        evidence = judge_case(case, ctx.chunks)
        runs = {"base": execution.plain(ctx.searcher, case.question, scope.document_ids)}
        for name, route in (("without", False), ("with", True)):
            runs[name] = execution.planned(ctx.searcher, plan, execution.QuerySettings(original_route=route),
                                           scope.document_ids)
        positions = {n: evidence_positions(evidence, r) for n, r in runs.items()}
        change = evidence_change(positions["without"], positions["with"])
        rows[case.case_id] = {
            "subquestions": len(plan.questions),
            "positions": positions,
            "change": round(change, 4), "direction": direction(change),
            "needed": {n: {g.group_id: completion_ranks([u.chunk_ids for u in r.units], evidence.groups).get(g.group_id)
                           for g in evidence.groups} for n, r in runs.items()},
            "retrievals": {n: r.cost.retrievals for n, r in runs.items()},
            "retrieval_seconds": {n: round(r.cost.retrieval_seconds, 4) for n, r in runs.items()}}
    better = [c for c, r in rows.items() if r["direction"] == 1]
    worse = [c for c, r in rows.items() if r["direction"] == -1]
    n = len(rows)
    result[combo.label] = {
        "better": better, "worse": worse, "class": classify(len(better), len(worse)),
        "complete": {s: {k: sum(complete(r["needed"][s], k) for r in rows.values()) for k in KS} for s in SIDES},
        "retrievals": {s: sum(r["retrievals"][s] for r in rows.values()) / n for s in SIDES},
        "retrieval_seconds": {s: sum(r["retrieval_seconds"][s] for r in rows.values()) / n for s in SIDES},
        "rows": rows}
    print(combo.label, result[combo.label]["class"], len(better), len(worse), flush=True)

(HERE / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

lines = ["| 组合 | 变好 | 变差 | 归类 | " + " | ".join(f"K={k} 完整覆盖（V3.13 / 无 / 有）" for k in KS) + " |",
         "|---|---|---|---|" + "---|" * len(KS)]
for label, r in result.items():
    lines.append(f"| {label} | {len(r['better'])} | {len(r['worse'])} | {r['class']} | "
                 + " | ".join(" / ".join(str(r["complete"][s][k]) for s in SIDES) for k in KS) + " |")
votes = Counter(r["class"] for r in result.values())
lines += ["", "归类票数：" + "，".join(f"{c} {v}" for c, v in votes.most_common()), ""]
qs = sorted({c for r in result.values() for c in r["better"] + r["worse"]})
lines += ["| 题 | " + " | ".join(result) + " |", "|---|" + "---|" * len(result)]
for q in qs:
    cells = []
    for r in result.values():
        x = r["rows"][q]
        mark = {1: "好", -1: "差", 0: ""}[x["direction"]]
        cells.append(f"{mark} {x['change']:+.2f}".strip())
    lines.append(f"| {q} | " + " | ".join(cells) + " |")
lines += ["", "| 组合 | 每题检索次数（V3.13 / 无 / 有） | 每题检索耗时 ms（V3.13 / 无 / 有） |", "|---|---|---|"]
for label, r in result.items():
    lines.append(f"| {label} | " + " / ".join(f"{r['retrievals'][s]:.2f}" for s in SIDES) + " | "
                 + " / ".join(f"{r['retrieval_seconds'][s] * 1000:.0f}" for s in SIDES) + " |")
(HERE / "tables.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(lines))
