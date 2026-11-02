"""Experiment: does the translation lose because it names things differently from the document?

The order tables label their batches "1st Delivery … 6th Delivery"; the translations say "fourth
delivery batch", "sixth batch", "Batch 1". The same translation with only that phrase replaced by the
document's label, codes stripped, on the semantic, BM25 and hybrid retrievers.

Writes exp_terms.json next to this script. Run: python reports/experiments/v3.14-factors/exp_terms.py
"""

from __future__ import annotations

import json
from pathlib import Path

from probe import Probe

HERE = Path(__file__).resolve().parent
TEXTS = {
    "PO-25-15-Q001": ("What is the specific delivery date and total product amount for the fourth delivery batch in order ?",
                      "fourth delivery batch", "4th Delivery"),
    "PO-25-15-Q002": ("What is the specific delivery date for the sixth batch of order , and what is the total amount for the delivered products?",
                      "sixth batch", "6th Delivery"),
    "PO-25-19-Q007": ("What is the latest arrival deadline in Peru for Batch 1 of order ?", "Batch 1", "the 1st Delivery"),
}
COMBOS = ["RM-T", "RM-C"]


def main() -> None:
    out = {}
    for retriever in ("semantic", "bm25", "hybrid"):
        probe = Probe(COMBOS, retriever)
        for case_id, (text, phrase, label) in TEXTS.items():
            for name, t in (("译文", text), ("换成文档用词", text.replace(phrase, label))):
                out.setdefault(case_id, {"phrase": phrase, "label": label, "n": {}})["n"].setdefault(retriever, {})[name] = {
                    c: [v["n"] for g, v in probe.ranks(c, case_id, t).items() if g != "_top"] for c in COMBOS}
        del probe
    (HERE / "exp_terms.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
