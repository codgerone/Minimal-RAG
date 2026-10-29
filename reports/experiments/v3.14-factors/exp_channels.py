"""Experiment: split the effect of a phrasing into the two sides of the hybrid retriever.

A question written in Chinese finds no BM25 match in the English and Spanish documents (apart from
Latin-script words it quotes), so for it the hybrid ranking is the dense ranking. A phrasing in the
documents' language (translation, HyDE) changes the dense vector and also brings in BM25. For every
question, without decomposition and with codes stripped on both sides (the pure single-factor pair of
pairs.py), the information items' completion ranks of the original text and of each phrasing, on the
semantic, BM25 and hybrid retrievers.

Writes exp_channels.json next to this script.
Run: python reports/experiments/v3.14-factors/exp_channels.py
"""

from __future__ import annotations

import json
from pathlib import Path

from probe import Probe
from rag.query.scope import strip_codes

HERE = Path(__file__).resolve().parent
PLANS = json.loads((HERE.parent / "v3.14-phrasings-v8/plans.json").read_text(encoding="utf-8"))["generations"]["0"]
COMBOS = ["RM-T", "RM-C"]
KINDS = ("question", "rewrite", "translation", "hyde", "step_back")


def main() -> None:
    out: dict = {}
    for retriever in ("semantic", "bm25", "hybrid"):
        probe = Probe(COMBOS, retriever)
        for case_id, plans in PLANS.items():
            codes = plans["scope"]["codes"]
            q = plans["plain"]["questions"][0]
            for x in q["phrasings"]:
                if x["kind"] not in KINDS:
                    continue
                text = strip_codes(x["text"], codes)[0]
                for combo in COMBOS:
                    r = probe.ranks(combo, case_id, text)
                    out.setdefault(case_id, {}).setdefault(x["kind"], {}).setdefault(retriever, {})[combo] = {
                        "n": [v["n"] for g, v in r.items() if g != "_top"], "top": r["_top"][:4]}
            print(retriever, case_id, flush=True)
        del probe
    (HERE / "exp_channels.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
