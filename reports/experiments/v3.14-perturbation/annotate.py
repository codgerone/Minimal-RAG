"""Sub-question ↔ information item annotation of the independent generations (proposal v3.14,
"分层评估"; criterion in reports/experiments/v3.14-decomposition/mapping.json).

A question whose sub-questions are the same as in generation 0 takes generation 0's annotation; the
others are annotated by the assistant in judged.json ({generation: {case: entry}}, entries as in
mapping.json). Writes mapping_g<n>.json for every generation, and pending.json with the questions
still to annotate (their sub-questions, information items and generation 0's annotation).
Run after run_plans.py: python reports/experiments/v3.14-perturbation/annotate.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
FIELDS = ("mapping", "omitted", "extra", "multi", "lost_qualifier", "note")


def main() -> None:
    base = json.loads((ROOT / "reports/experiments/v3.14-decomposition/mapping.json").read_text(encoding="utf-8"))
    plans = json.loads((HERE / "plans.json").read_text(encoding="utf-8"))
    judged_path = HERE / "judged.json"
    judged = json.loads(judged_path.read_text(encoding="utf-8")) if judged_path.is_file() else {}
    data = json.loads((ROOT / "reports/query/v3.14/data.json").read_text(encoding="utf-8"))
    items = {c["case_id"]: {g["group_id"]: g["information_item"] for g in c["groups"]} for c in data["cases"]}
    pending = {}
    for g, cases in plans["generations"].items():
        out = {}
        for case, plan in cases.items():
            subs = {q["id"]: q["text"] for q in plan["decomposed"]["questions"]}
            if subs == base["cases"][case]["subquestions"]:
                entry = {k: base["cases"][case][k] for k in FIELDS}
            elif case in judged.get(g, {}):
                entry = {k: judged[g][case][k] for k in FIELDS}
                assert set(entry["mapping"]) == set(items[case]), (g, case)
                assert {q for qs in entry["mapping"].values() for q in qs} | set(entry["extra"]) == set(subs), (g, case)
            else:
                pending.setdefault(g, {})[case] = {"question": next(c["question"] for c in data["cases"] if c["case_id"] == case),
                                                   "items": items[case], "subquestions": subs,
                                                   "generation_0": {"subquestions": base["cases"][case]["subquestions"],
                                                                    **{k: base["cases"][case][k] for k in FIELDS}}}
                continue
            out[case] = {**entry, "subquestions": subs}
        path = HERE / f"mapping_g{g}.json"
        path.unlink(missing_ok=True)
        if g in pending:   # query-compare runs the perturbation once every generation's file exists
            continue
        path.write_text(json.dumps(
            {"plan": f"{base['plan'].split('第 0 次')[0]}第 {g} 次独立生成", "criterion": base["criterion"],
             "cases": out}, ensure_ascii=False, indent=1), encoding="utf-8")
    (HERE / "pending.json").write_text(json.dumps(pending, ensure_ascii=False, indent=1), encoding="utf-8")
    print({g: len(v) for g, v in pending.items()} or "nothing pending")


if __name__ == "__main__":
    main()
