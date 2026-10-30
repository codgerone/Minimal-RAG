"""Experiment: test the causes found with exp_subtrans.py and bm25_explain.py for the sub-questions whose
translation ranks the evidence lower than the Chinese sub-question. Each variant changes one thing in the
translation of generation 0 (codes stripped); the information item's completion rank and its evidence
units' ranks on the semantic, BM25 and hybrid retrievers.

Variants:
- 去掉误匹配的通用词: drop the one generic word that is the translation's only BM25 match in the
  scoped document (the BM25 list then holds no chunk or only real matches).
- 换成文档用词: the document's own word for the thing asked (its table header, clause title, tax name),
  or its singular form where the translation's plural is not stemmed to it.
- 补回所指: the referent the decomposition dropped (P3.1 analysis, category C).
- 去掉所有格: "ELPU's" → "ELPU" (the tokenizer splits off an "s" that matches 13 of 49 chunks).

Writes exp_subtrans2.json next to this script. Run: python reports/experiments/v3.14-factors/exp_subtrans2.py
"""

from __future__ import annotations

import json
from pathlib import Path

from probe import Probe

HERE = Path(__file__).resolve().parent
SUB = json.loads((HERE / "exp_subtrans.json").read_text(encoding="utf-8"))
# (case, sub-question, item, variant name, old phrase, new phrase) applied to the stripped translation
VARIANTS = [
    ("CONTRACT-105-Q002", "Q1", "e1", "换成文档用词（单数）", "ítems", "ítem"),
    ("CONTRACT-105-Q001", "Q1", "e1", "换成文档用词", "partes firmantes", "otorgantes"),
    ("PO-24-12-Q001", "Q1", "e1", "去掉误匹配的通用词", " in order ?", "?"),
    ("PO-24-12-Q001", "Q1", "e1", "换成文档用词", "products", "goods"),
    ("PO-24-12-Q001", "Q3", "e3", "去掉误匹配的通用词", " in order ?", "?"),
    ("PO-24-12-Q001", "Q3", "e3", "换成文档用词", "quantity", "Qty"),
    ("PO-24-12-Q004", "Q3", "e3", "补回所指", "payment deadline for order", "payment deadline for the MDC ORCA fee in order"),
    ("E001-602-Q001", "Q3", "e3", "换成文档用词", "impuestos", "IGV"),
    ("PO-25-19-Q006", "Q2", "e2", "去掉所有格", "ELPU's", "ELPU"),
]
COMBOS = ["RM-T", "LM+-T"]


def main() -> None:
    out: list[dict] = []
    for case, sq, item, name, old, new in VARIANTS:
        base = SUB[case]["subquestions"][sq]["texts"]["translation"]
        assert base.count(old) == 1, (case, old)
        out.append({"case": case, "sub": sq, "item": item, "name": name, "old": old, "new": new,
                    "texts": {"中文子问题": SUB[case]["subquestions"][sq]["texts"]["question"], "译文": base,
                              name: base.replace(old, new)}, "n": {}})
    for retriever in ("semantic", "bm25", "hybrid"):
        probe = Probe(COMBOS, retriever)
        for v in out:
            for label, text in v["texts"].items():
                for combo in COMBOS:
                    r = probe.ranks(combo, v["case"], text)
                    v["n"].setdefault(retriever, {}).setdefault(label, {})[combo] = {
                        "n": r[v["item"]]["n"], "units": r[v["item"]]["units"], "top": r["_top"][:4]}
        del probe
    (HERE / "exp_subtrans2.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for v in out:
        print(v["case"], v["sub"], v["item"], v["name"])
        for label in v["texts"]:
            print("   ", f"{label:12}", " ".join(f'{c} ' + "/".join(str(v["n"][r][label][c]["n"]) for r in ("semantic", "bm25", "hybrid"))
                                            for c in COMBOS))


if __name__ == "__main__":
    main()
