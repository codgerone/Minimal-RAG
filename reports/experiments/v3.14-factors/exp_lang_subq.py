"""Sub-question level comparison of the rewrite's language (V3.14.1), each sub-question an independent question.

For every generation (0-3), every answerable question's decomposed plan, every sub-question with at least
one information item annotated to it (the decomposition mapping of that generation): the completion
ranks of its items in its own ranking only, with the rewrite in the question language, the document
languages and both (codes stripped, the rewrite in place of the sub-question), on the 13 combinations.
Verdicts by dominance per combination; a sub-question's verdict in a generation is the one direction its
changed combinations share, or mixed (the rule of the question level).

Writes exp_lang_subq.json next to this script.
Run: python reports/experiments/v3.14-factors/exp_lang_subq.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from rag.cli import _assembly
from rag.eval.judge import judge_case
from rag.eval.metrics import routed_completion_rank
from rag.eval.query_compare import (COMBOS, DECOMPOSITION_MAPPING, PERTURBATION_MAPPING, MemoSearch, QueryConfig,
                                    make_planner)
from rag.eval.runner import prepare
from rag.jsonio import read_json
from rag.paths import Workspace
from rag.query import execution
from rag.query.planning import document_languages

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
VARIANTS = {"q": "P3.1_P3.2.0_P3.2.1", "d": "P3.1_P3.2.0_P3.2.1~d", "qd": "P3.1_P3.2.0_P3.2.1~qd"}
PAIRS = (("q", "d"), ("q", "qd"), ("d", "qd"))


def verdict(left, right) -> str:
    a = sorted(math.inf if x is None else x for x in left)
    b = sorted(math.inf if x is None else x for x in right)
    e, t = any(r < l for l, r in zip(a, b)), any(r > l for l, r in zip(a, b))
    return "mixed" if e and t else "better" if e else "worse" if t else "same"


def main() -> None:
    ws = Workspace(ROOT)
    planner = make_planner(ws)
    planner.llm.complete_structured = None   # every plan must come from the cache
    out: dict = {}   # generation -> "case/qid" -> {text, groups, ranks: variant -> combo -> [ranks]}
    for combo in COMBOS:
        ctx = prepare(ws, _assembly(ws, combo.config, "hybrid", combo.table_return_unit, 1.0), cache_retrievals=True)
        ctx.warm_up()
        languages_of = document_languages(ws.index_dir(combo.config))
        search = MemoSearch(ctx.searcher)
        for g in (0, 1, 2, 3):
            mapping = read_json(ROOT / (PERTURBATION_MAPPING.format(generation=g) if g else DECOMPOSITION_MAPPING))["cases"]
            for case in ctx.dataset.cases:
                if not case.answerable:
                    continue
                scope = ctx.scope(case.question)
                languages = sorted({languages_of[d] for d in (scope.document_ids or languages_of)})
                plan = planner.add_languages(planner.plan(case.question, languages, True, generation=g), languages, generation=g)
                evidence = judge_case(case, ctx.chunks)
                annotated = mapping[case.case_id]["mapping"]
                for v, key in VARIANTS.items():
                    run = execution.planned(search, plan, QueryConfig.parse(key).settings(), scope.document_ids, scope.codes)
                    for route in run.routes:
                        groups = [x for x in case.groups if route.question_id in annotated[x.group_id]]
                        if not groups:
                            continue
                        units = [u.chunk_ids for u in route.units]
                        n = [routed_completion_rank([units], [[evidence.excerpts[x].acceptable_sets for x in s.excerpt_ids]
                                                              for s in grp.schemes]) for grp in groups]
                        rec = out.setdefault(str(g), {}).setdefault(f"{case.case_id}/{route.question_id}", {
                            "text": route.text, "groups": [x.group_id for x in groups], "ranks": {}})
                        rec["ranks"].setdefault(v, {})[combo.label] = n
        print(combo.label, flush=True)
    summary = {}
    for g, subs in out.items():
        for sq, rec in subs.items():
            rec["verdict"] = {}
            for a, b in PAIRS:
                vs = {verdict(rec["ranks"][a][c], rec["ranks"][b][c]) for c in rec["ranks"][a]} - {"same"}
                rec["verdict"][f"{a}>{b}"] = "same" if not vs else vs.pop() if len(vs) == 1 else "mixed"
    (HERE / "exp_lang_subq.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
