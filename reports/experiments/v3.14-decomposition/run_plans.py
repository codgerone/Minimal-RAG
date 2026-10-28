"""Decomposition plans with the current decomposition prompt (generation 0) for the 34 questions, next to the
frozen v7 plans in reports/query/v3.14/data.json. Plans without decomposition are planned too, to
confirm they come from the cache unchanged (the phrasing prompt is still v7).

Writes plans_<decomposition prompt version>.json next to this script.
Run: python reports/experiments/v3.14-decomposition/run_plans.py
"""

from __future__ import annotations

import json
from pathlib import Path

from rag.eval.dataset import load_dataset
from rag.eval.query_compare import make_planner, plan_json
from rag.paths import Workspace
from rag.query.planning import PROMPT_VERSIONS

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


class Counting:
    """Counts the calls that reach the model (cache misses)."""

    def __init__(self, llm):
        self.llm, self.model, self.calls = llm, llm.model, 0

    def complete_structured(self, *args, **kwargs):
        self.calls += 1
        return self.llm.complete_structured(*args, **kwargs)


def main() -> None:
    data = json.loads((ROOT / "reports/query/v3.14/data.json").read_text(encoding="utf-8"))
    planner = make_planner(Workspace(ROOT))
    planner.llm = Counting(planner.llm)
    questions = {c.case_id: c.question for c in load_dataset(ROOT / "eval/ground-truth").cases}
    out, plain_calls, cost = {}, 0, 0.0
    for case_id, stored in data["plans"].items():
        scope = stored["scope"]
        before = planner.llm.calls
        plain = planner.plan(questions[case_id], scope["languages"], False, scope["codes"])
        plain_calls += planner.llm.calls - before
        plan = planner.plan(questions[case_id], scope["languages"], True, scope["codes"])
        cost += plan.cost.cost_usd
        out[case_id] = {"v7": [q["text"] for q in stored["decomposed"]["questions"]],
                        "plan": plan_json(plan), "plain_unchanged": plan_json(plain) == stored["plain"]}
        print(case_id, len(out[case_id]["v7"]), "→", len(plan.questions), flush=True)
    (HERE / f"plans_{PROMPT_VERSIONS['decomposition'].rsplit('_', 1)[1]}.json").write_text(json.dumps(
        {"prompt": PROMPT_VERSIONS, "model": planner.llm.model,
         "usd": round(cost, 4), "plain_calls": plain_calls, "plans": out}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print("usd", round(cost, 4), "model calls for plain plans", plain_calls,
          "plain unchanged", sum(v["plain_unchanged"] for v in out.values()), "/", len(out))


if __name__ == "__main__":
    main()
