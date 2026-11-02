"""Experiment: why does removing the identifier code help items whose evidence is running text and
hurt some whose evidence is a table? (P3.2.0 on the original question.)

The code is a user-assigned string that never occurs in the documents (documents/documents.toml), so
BM25 cannot match it; any effect goes through the dense vector. Variants of the original question,
each changing only the code: as is; removed; its digits replaced by other digits (no year); its year
"2025" kept and the rest replaced; letters only; a word-like placeholder. If every variant with digits
behaves like the original and the letter-only one like the removal, the digits pull the vector; if
all variants with any code behave alike, it is the presence of an identifier.

Writes exp_codes.json next to this script. Run: python reports/experiments/v3.14-factors/exp_codes.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from probe import Probe

HERE = Path(__file__).resolve().parent
CASES = ["CONTRACT-105-Q010", "PO-25-19-Q005", "PO-25-19-Q003", "PO-25-19-Q001",       # worse when removed
         "CONTRACT-105-Q001", "CONTRACT-105-Q003", "CONTRACT-105-Q004", "CONTRACT-105-Q007", "E001-602-Q001"]  # better
COMBOS = ["RM-T", "RM-C", "LM-C"]


def variants(code: str) -> dict[str, str | None]:
    letters = re.sub(r"[^A-Za-z]", "", code)
    digits = re.sub(r"[^0-9]", "", code)
    other = "".join("8461937520"[int(d)] for d in digits)          # a fixed digit permutation
    keep_year = digits.replace("2025", "2025", 1)[:4] + other[4:] if "2025" in digits else other
    return {"原样": code, "删去": None,
            "换数字": code.replace(digits, other) if digits in code else re.sub(r"\d", lambda m: "8461937520"[int(m.group())], code),
            "保留年份": code.replace(digits, keep_year) if digits in code else code,
            "只有字母": letters + "QZXWVK",
            "无意义词": "Mulberry"}


def main() -> None:
    probe = Probe(COMBOS)
    out = {}
    for case_id in CASES:
        case = probe.cases[case_id]
        code = next(c for c in ("CONT2025001", "ORDER2025001", "ORDER2025002", "ORDER2025003", "INVOICE-E001-602")
                    if c in case.question)
        out[case_id] = {}
        for name, repl in variants(code).items():
            text = case.question.replace(code, repl) if repl is not None else re.sub(r"\s{2,}", " ", case.question.replace(code, ""))
            out[case_id][name] = {"text": text, "ranks": {}}
            for combo in COMBOS:
                r = probe.ranks(combo, case_id, text)
                out[case_id][name]["ranks"][combo] = {g: v["n"] for g, v in r.items() if g != "_top"}
            print(case_id, name, text[:40], {c: list(v.values()) for c, v in out[case_id][name]["ranks"].items()}, flush=True)
    (HERE / "exp_codes.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
