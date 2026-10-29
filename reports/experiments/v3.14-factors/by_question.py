"""Per question, for one comparison of pairs.json: how many of the 13 combinations are better / worse /
mixed, and per information item the completion ranks left → right in each combination.

Run: python reports/experiments/v3.14-factors/by_question.py "<factor>" "<context>" [case_id …]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAIRS = json.loads((HERE / "pairs.json").read_text(encoding="utf-8"))
SIGN = {"better": "▲", "worse": "▼", "mixed": "◆", "same": "·"}


def rk(x):
    return "∞" if x is None else str(x)


def main(factor: str, context: str, only: list[str]) -> None:
    comp = next(c for c in PAIRS if c["factor"] == factor and c["context"] == context)
    print(comp["left"], "→", comp["right"])
    for case, combos in comp["cells"].items():
        vs = [cell["v"][0] for cell in combos.values()]
        if only and case not in only:
            continue
        if not only and not ({"worse", "mixed"} & set(vs)):
            continue
        tally = " ".join(f"{SIGN[v]}{vs.count(v)}" for v in ("better", "worse", "mixed") if vs.count(v))
        print(f"\n{case}  {tally}")
        for combo, cell in combos.items():
            l, r = cell["l"][0], cell["r"][0]
            items = "  ".join(f"e{i + 1}:{rk(a)}→{rk(b)}" for i, (a, b) in enumerate(zip(l, r)) if a != b)
            print(f"   {combo:6} {SIGN[cell['v'][0]]} {items}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3:])
