"""Phrasing prompt v8 (proposal v3.14, 2026-10-29 revision; decision 016): the plans of generation 0 and
the independent generations 1–3, without and with decomposition, for every answerable question. The
decompositions are the cached v9 ones; only the phrasing calls are new. Questions run on a few threads:
each (generation, question, decomposed) is a different call, so no two threads make the same call.

Writes plans.json next to this script: per generation and question both plans, and per retrieval text
how many identifier codes P3.2.0 removes from it (rag/query/scope.py strip_codes).
Run: python reports/experiments/v3.14-phrasings-v8/run_plans.py
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rag.eval.dataset import load_dataset
from rag.eval.query_compare import make_planner, plan_json
from rag.paths import Workspace
from rag.query.planning import PROMPT_VERSIONS
from rag.query.scope import strip_codes

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
GENERATIONS = (0, 1, 2, 3)
WORKERS = 6


def main() -> None:
    scopes = {c: p["scope"] for c, p in json.loads(
        (ROOT / "reports/query/v3.14/data.json").read_text(encoding="utf-8"))["plans"].items()}
    planner = make_planner(Workspace(ROOT))
    questions = {c.case_id: c.question for c in load_dataset(ROOT / "eval/ground-truth").cases}

    def one(task):
        generation, case_id, decomposed = task
        plan = planner.plan(questions[case_id], scopes[case_id]["languages"], decomposed, generation=generation)
        print(generation, case_id, "decomposed" if decomposed else "plain", flush=True)
        return task, plan

    tasks = [(g, c, d) for g in GENERATIONS for c in scopes for d in (False, True)]
    with ThreadPoolExecutor(WORKERS) as pool:
        done = dict(pool.map(one, tasks))
    out: dict[str, dict] = {}
    cost = 0.0
    for (generation, case_id, decomposed), plan in done.items():
        cost += plan.phrasing.cost_usd + (plan.decomposition.cost_usd if decomposed else 0)
        data = plan_json(plan)
        for q in data["questions"]:
            q["stripped"] = strip_codes(q["text"], scopes[case_id]["codes"])[1]
            for x in q["phrasings"]:
                x["stripped"] = strip_codes(x["text"], scopes[case_id]["codes"])[1]
        out.setdefault(str(generation), {}).setdefault(case_id, {"scope": scopes[case_id]})[
            "decomposed" if decomposed else "plain"] = data
    (HERE / "plans.json").write_text(json.dumps(
        {"prompt": PROMPT_VERSIONS, "model": planner.llm.model, "usd": round(cost, 4), "generations": out},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print("usd", round(cost, 4))


if __name__ == "__main__":
    main()
