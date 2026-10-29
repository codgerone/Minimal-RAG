"""Experiment A, second part: the questions that removing the code makes worse (on the original question,
in all four generations) but that the first part did not cover or covered only on combinations where
they do not change. The same variants as exp_codes.py, on the combinations where these questions get
worse (with the lead sentence or summary in the table text, and LL-C).

Writes exp_codes2.json next to this script. Run: python reports/experiments/v3.14-factors/exp_codes2.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from exp_codes import variants
from probe import Probe

HERE = Path(__file__).resolve().parent
CASES = ["CONTRACT-105-Q014", "PO-24-12-Q004", "PO-25-19-Q001", "PO-25-19-Q002", "PO-25-19-Q003", "PO-25-19-Q007",
         "PO-25-19-Q009"]
COMBOS = ["LM+-C", "LL+-C", "LL-C", "S-T"]


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
                out[case_id][name]["ranks"][combo] = [v["n"] for g, v in r.items() if g != "_top"]
            print(case_id, name, {c: v for c, v in out[case_id][name]["ranks"].items()}, flush=True)
    (HERE / "exp_codes2.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
