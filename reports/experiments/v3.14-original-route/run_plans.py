"""Plans every question (query_plan_v7, generation 0) without and with decomposition; the LLM
calls are cached in .rag/llm-cache/, so later runs reuse them. Writes plans.json.
Run from the repository root."""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import plan_inputs, planner  # noqa: E402
from rag.cli import _assembly  # noqa: E402
from rag.eval.runner import prepare  # noqa: E402
from rag.paths import Workspace  # noqa: E402
from rag.query.planning import PROMPT_VERSION  # noqa: E402

ws = Workspace(Path.cwd())
ctx = prepare(ws, _assembly(ws, "structured", "hybrid", "table"))
p = planner(ws)


def one(case):
    scope = ctx.scope(case.question)
    languages, codes = plan_inputs(ws, scope)
    out = {}
    for decompose in (False, True):
        plan = p.plan(case.question, languages, decompose, codes)
        out["decomposed" if decompose else "plain"] = {
            "questions": [{"id": q.id, "text": q.text,
                           "phrasings": [{"kind": x.kind, "language": x.language, "text": x.text} for x in q.phrasings]}
                          for q in plan.questions],
            "degraded": list(plan.degraded),
            "cost": {"calls": plan.cost.calls, "seconds": plan.cost.seconds, "cost_usd": plan.cost.cost_usd}}
    return case.case_id, {"question": case.question, "languages": languages, "codes": codes, **out}


with ThreadPoolExecutor(4) as pool:
    plans = dict(pool.map(one, ctx.dataset.cases))
(HERE / "plans.json").write_text(json.dumps({"prompt_version": PROMPT_VERSION, "plans": plans},
                                            ensure_ascii=False, indent=2), encoding="utf-8")
for cid, x in plans.items():
    d = x["decomposed"]
    print(cid, "子问题", len(d["questions"]), "降级" if d["degraded"] or x["plain"]["degraded"] else "",
          f"${x['plain']['cost']['cost_usd'] + d['cost']['cost_usd']:.4f}")
print("合计 $%.4f" % sum(x["plain"]["cost"]["cost_usd"] + x["decomposed"]["cost"]["cost_usd"] for x in plans.values()))
