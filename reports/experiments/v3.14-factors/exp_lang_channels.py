"""Experiment G (V3.14.1): split the effect of writing the rewrite and the step-back in the documents'
language into the two sides of the hybrid retriever, as experiment B did for the translation.

For every question, without decomposition, codes stripped, generation 0: the information items'
completion ranks of the question-language and the document-language rewrite and step-back on the
semantic, BM25 and hybrid retrievers (RM-T, RM-C), with each text.

Writes exp_lang_channels.json next to this script.
Run: python reports/experiments/v3.14-factors/exp_lang_channels.py
"""

from __future__ import annotations

import json
from pathlib import Path

from probe import Probe
from rag.query.scope import strip_codes

HERE = Path(__file__).resolve().parent
DATA = json.loads((HERE.parents[1] / "query/v3.14/data.json").read_text(encoding="utf-8"))
COMBOS = ["RM-T", "RM-C"]


def main() -> None:
    out: dict = {}
    for retriever in ("semantic", "bm25", "hybrid"):
        probe = Probe(COMBOS, retriever)
        for case_id, plans in DATA["plans"].items():
            codes = plans["scope"]["codes"]
            q = plans["plain"]["questions"][0]
            for x in q["phrasings"]:
                if x["kind"] not in ("rewrite", "step_back"):
                    continue
                name = f'{x["kind"]}:{"document" if x["language"] else "question"}'
                text = strip_codes(x["text"], codes)[0]
                for combo in COMBOS:
                    r = probe.ranks(combo, case_id, text)
                    out.setdefault(case_id, {}).setdefault(name, {"text": text}).setdefault(retriever, {})[combo] = {
                        "n": [v["n"] for g, v in r.items() if g != "_top"], "top": r["_top"][:4]}
            print(retriever, case_id, flush=True)
        del probe
    (HERE / "exp_lang_channels.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
