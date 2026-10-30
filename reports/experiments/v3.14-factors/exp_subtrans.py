"""Experiment: split the effect of translating each sub-question into the two sides of the hybrid
retriever (as exp_channels.py does for the original question).

The single-factor pair of pairs.py "P3.2.2 · 子问题（不合并） · 两侧去编码": with decomposition and no
merge, each information item is ranked in the sub-questions annotated for it; left the Chinese
sub-question, right its translation, codes stripped on both sides. For every question, generation 0,
each sub-question's Chinese text and translation on the semantic, BM25 and hybrid retrievers: every
information item's completion rank, its evidence units' ranks and the top units.

Writes exp_subtrans.json next to this script.
Run: python reports/experiments/v3.14-factors/exp_subtrans.py
"""

from __future__ import annotations

import json
from pathlib import Path

from probe import Probe
from rag.query.scope import strip_codes

HERE = Path(__file__).resolve().parent
PLANS = json.loads((HERE.parent / "v3.14-phrasings-v8/plans.json").read_text(encoding="utf-8"))["generations"]["0"]
MAPPING = json.loads((HERE.parent / "v3.14-decomposition/mapping.json").read_text(encoding="utf-8"))["cases"]
COMBOS = ["RM-T", "LM+-T"]
KINDS = ("question", "translation")


def main() -> None:
    out: dict = {}
    for retriever in ("semantic", "bm25", "hybrid"):
        probe = Probe(COMBOS, retriever)
        for case_id, plans in PLANS.items():
            codes = plans["scope"]["codes"]
            case = out.setdefault(case_id, {"mapping": MAPPING[case_id]["mapping"], "subquestions": {}})
            for q in plans["decomposed"]["questions"]:
                sub = case["subquestions"].setdefault(q["id"], {"texts": {}, "ranks": {}})
                for x in q["phrasings"]:
                    if x["kind"] not in KINDS:
                        continue
                    text = strip_codes(x["text"], codes)[0]
                    sub["texts"][x["kind"]] = text
                    for combo in COMBOS:
                        sub["ranks"].setdefault(x["kind"], {}).setdefault(retriever, {})[combo] = probe.ranks(combo, case_id, text)
            print(retriever, case_id, flush=True)
        del probe
    (HERE / "exp_subtrans.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
