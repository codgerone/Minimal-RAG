"""Perturbation test (proposal v3.14, "冻结与复现"): three independent plan generations (1–3), cached
apart from the frozen generation 0, for every answerable question, without and with decomposition,
with the same prompts, model and scope as generation 0 (reports/query/v3.14/data.json).

Writes plans.json next to this script: per generation and question, both plans and whether the
sub-questions are the same as generation 0's.
Run: python reports/experiments/v3.14-perturbation/run_plans.py [generation …]
(with generations given, only those are planned, e.g. to fill the cache in parallel, and nothing is written)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from rag.eval.dataset import load_dataset
from rag.eval.query_compare import make_planner, plan_json
from rag.paths import Workspace
from rag.query.planning import PROMPT_VERSIONS

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
GENERATIONS = (1, 2, 3)


def main(generations: tuple[int, ...]) -> None:
    data = json.loads((ROOT / "reports/query/v3.14/data.json").read_text(encoding="utf-8"))
    planner = make_planner(Workspace(ROOT))
    questions = {c.case_id: c.question for c in load_dataset(ROOT / "eval/ground-truth").cases}
    out: dict[str, dict] = {}
    cost = 0.0
    for generation in generations:
        out[str(generation)] = {}
        for case_id, stored in data["plans"].items():
            scope = stored["scope"]
            pair = {d: planner.plan(questions[case_id], scope["languages"], d, scope["codes"], generation)
                    for d in (False, True)}
            cost += sum(p.cost.cost_usd for p in pair.values())
            texts = [q.text for q in pair[True].questions]
            same = texts == [q["text"] for q in stored["decomposed"]["questions"]]
            out[str(generation)][case_id] = {"plain": plan_json(pair[False]), "decomposed": plan_json(pair[True]),
                                             "same_subquestions": same}
            print(generation, case_id, len(texts), "same" if same else "changed", flush=True)
    if generations != GENERATIONS:
        return
    (HERE / "plans.json").write_text(json.dumps(
        {"prompt": PROMPT_VERSIONS, "model": planner.llm.model, "usd": round(cost, 4), "generations": out},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print("usd", round(cost, 4))


if __name__ == "__main__":
    main(tuple(int(x) for x in sys.argv[1:]) or GENERATIONS)
