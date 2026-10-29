"""Question features the effects of the measures are set against (proposal v3.14, 2026-10-29).

Per answerable question: the document and its language, the number of information items, the question
type (proposal "新题与题型"; the old 24 questions are "原题"), whether the question already quotes terms in
the document's language, and per information item whether its evidence is in a table (any evidence unit
of the RM-T ranking is a whole table). Writes features.json next to this script.
Run: python reports/experiments/v3.14-factors/features.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent

TYPES = {"PO-25-19-Q005": "分解题", "CONTRACT-105-Q011": "分解题",
         "PO-25-19-Q006": "回答上的多跳", "CONTRACT-105-Q013": "回答上的多跳", "CONTRACT-105-Q014": "回答上的多跳",
         "PO-25-19-Q009": "检索多跳", "PO-25-19-Q007": "隐含前提", "CONTRACT-105-Q012": "隐含前提"}
LATIN_WORD = re.compile(r"[A-Za-zÁÉÍÓÚÑáéíóúñü][A-Za-zÁÉÍÓÚÑáéíóúñü'-]{2,}")


def main() -> None:
    data = json.loads((ROOT / "reports/query/v3.14/data.json").read_text(encoding="utf-8"))
    units = data["units"]["RM-T"]
    out = {}
    for case in data["cases"]:
        cid = case["case_id"]
        scope = data["plans"][cid]["scope"]
        text = case["question"]
        for code in scope["codes"]:
            text = text.replace(code, " ")
        # words in Latin script other than the codes: names of companies and products, or quoted terms
        latin = LATIN_WORD.findall(text)
        held = data["holds"]["RM-T"][cid]
        table_items = []
        for g in case["groups"]:
            excerpts = {x["excerpt_id"] for x in case["excerpts"] if g["group_id"] in x["groups"]}
            in_units = [int(i) for i, xs in held.items() if excerpts & set(xs)]
            table_items.append(any(units[i]["table"] for i in in_units))
        out[cid] = {"document": case["document_id"], "language": scope["languages"][0], "codes": scope["codes"],
                    "items": len(case["groups"]), "type": TYPES.get(cid, "原题"), "latin_words": latin,
                    "table_items": table_items}
    (HERE / "features.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for cid, f in out.items():
        print(cid, f["language"], f["items"], f["type"], sum(f["table_items"]), f["latin_words"])


if __name__ == "__main__":
    main()
