"""Experiment D, second part: step-back questions without Latin-script words. Step-back also drops
Chinese qualifiers (which batch, which company, which guarantee). For the questions where the dropped
qualifier can be named, the step-back text with that qualifier appended in brackets (nothing else
changed), codes stripped, on the hybrid retriever; as exp_stepback.py. The qualifiers are read off the
question by hand (they are this experiment's inputs, not a rule).

Writes exp_stepback2.json next to this script. Run: python reports/experiments/v3.14-factors/exp_stepback2.py
"""

from __future__ import annotations

import json
from pathlib import Path

from probe import Probe
from rag.query.scope import strip_codes

HERE = Path(__file__).resolve().parent
PLANS = json.loads((HERE.parent / "v3.14-phrasings-v8/plans.json").read_text(encoding="utf-8"))["generations"]["0"]
COMBOS = ["RM-T", "RM-C", "LM-C", "LL-T"]
DROPPED = {"PO-25-15-Q001": ["第四批次"], "PO-25-15-Q002": ["第六批次"],
           "PO-25-19-Q009": ["唯一对序列号有额外要求的电力公司"], "CONTRACT-105-Q014": ["技术商务担保"],
           "CONTRACT-105-Q010": ["是否分批交货", "每个批次的具体交货日期"], "PO-25-19-Q005": ["订购数量最多的一款产品"]}


def main() -> None:
    probe = Probe(COMBOS)
    out = {}
    for case_id, dropped in DROPPED.items():
        plans = PLANS[case_id]
        codes = plans["scope"]["codes"]
        q = plans["plain"]["questions"][0]
        question = strip_codes(q["text"], codes)[0]
        step = strip_codes(next(x["text"] for x in q["phrasings"] if x["kind"] == "step_back"), codes)[0]
        texts = {"原句": question, "step-back": step, "补回": f"{step}（{'，'.join(dropped)}）"}
        out[case_id] = {"texts": {**texts, "dropped": dropped}, "n": {}}
        for name, text in texts.items():
            out[case_id]["n"][name] = {c: [v["n"] for g, v in probe.ranks(c, case_id, text).items() if g != "_top"] for c in COMBOS}
        print(case_id, {k: v for k, v in out[case_id]["n"].items()}, flush=True)
    (HERE / "exp_stepback2.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
