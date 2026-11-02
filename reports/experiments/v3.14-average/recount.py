"""Re-counting of the decision 015 comparisons (and the V3.14.1 language comparisons) with the 13
combinations averaged (2026-11-02): per generation, the number of questions better / worse / mixed in
each combination, averaged over the 13 combinations; per question, its direction in a generation is the
sign of (combinations better − combinations worse), stable when the same in all four generations, a flip
when positive in one and negative in another. The retired rule (one direction shared by every changed
combination, otherwise mixed) is computed alongside, to check each row against decision 015.

Reads reports/query/v3.14/{facts,perturb}; writes recount.json next to this script.
Run: python reports/experiments/v3.14-average/recount.py
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PAGE = ROOT / "reports/query/v3.14"
GENS = ("0", "1", "2", "3")
FINAL = "P3.2.0_P3.2.2@rrf"
ROWS = [   # (row of the decision 015 table, left, right)
    ("P3.2.0 去编码：原句 → 去编码原句", "", "P3.2.0"),
    ("翻译单独：去编码原句 → 译文", "P3.2.0", "P3.2.0_P3.2.2"),
    ("翻译融合：去编码原句 → 原句 + 译文", "P3.2.0", "P3.2.0_P3.2.2@rrf"),
    ("HyDE 单独", "P3.2.0", "P3.2.0_P3.2.3"),
    ("HyDE 融合", "P3.2.0", "P3.2.0_P3.2.3@rrf"),
    ("HyDE 叠加在最终配置上", FINAL, "P3.2.0_P3.2.2+P3.2.3@rrf"),
    ("改写单独", "P3.2.0", "P3.2.0_P3.2.1"),
    ("改写融合", "P3.2.0", "P3.2.0_P3.2.1@rrf"),
    ("改写叠加在最终配置上", FINAL, "P3.2.0_P3.2.1+P3.2.2@rrf"),
    ("step-back 单独", "P3.2.0", "P3.2.0_P3.2.4"),
    ("step-back 融合", "P3.2.0", "P3.2.0_P3.2.4@rrf"),
    ("step-back 叠加在最终配置上", FINAL, "P3.2.0_P3.2.2+P3.2.4@rrf"),
    ("分解，不合并（检索层）", FINAL, "P3.1_P3.2.0_P3.2.2@rrf"),
    ("分解，轮流取", FINAL, "P3.1@rr_P3.2.0_P3.2.2@rrf"),
    ("分解，全局 RRF", FINAL, "P3.1@rrf_P3.2.0_P3.2.2@rrf"),
    ("最终配置：V3.13 → 去编码 + 原句 + 译文", "", FINAL),
]


def load(kind: str, k: str) -> dict:
    text = (PAGE / kind / f"{k or 'none'}.js").read_text(encoding="utf-8")
    return json.loads(re.search(r"\]=(.*);\s*$", text, re.S).group(1))


def ranks(k: str) -> dict:
    out = {"0": {c: {q: f["n"] for q, f in cases.items()} for c, cases in load("facts", k).items()}}
    out.update(load("perturb", k))
    return out


def verdict(left, right) -> str:
    a = sorted(math.inf if x is None else x for x in left)
    b = sorted(math.inf if x is None else x for x in right)
    e, t = any(r < l for l, r in zip(a, b)), any(r > l for l, r in zip(a, b))
    return "mixed" if e and t else "better" if e else "worse" if t else "same"


def count(left: str, right: str) -> dict:
    L, R = ranks(left), ranks(right)
    combos = list(L["0"])
    cases = list(L["0"][combos[0]])
    v = {g: {q: [verdict(L[g][c][q], R[g][c][q]) for c in combos] for q in cases} for g in GENS}
    n = len(combos)
    avg = [{x: sum(vs.count(x) for vs in v[g].values()) / n for x in ("better", "worse", "mixed")} for g in GENS]
    sign = {q: [(vs.count("better") > vs.count("worse")) - (vs.count("better") < vs.count("worse"))
                for vs in (v[g][q] for g in GENS)] for q in cases}
    old = []
    for g in GENS:
        per = {}
        for q, vs in v[g].items():
            s = set(vs) - {"same"}
            per[q] = "same" if not s else s.pop() if len(s) == 1 else "mixed"
        old.append(per)
    old_q = {q: [old[i][q] for i in range(4)] for q in cases}
    return {
        "avg": avg,
        "net": [a["better"] - a["worse"] for a in avg],
        "per_question": {q: [[vs.count(x) for x in ("better", "worse", "mixed")] for vs in (v[g][q] for g in GENS)] for q in cases},
        "stable": {"better": [q for q, s in sign.items() if all(x > 0 for x in s)],
                   "worse": [q for q, s in sign.items() if all(x < 0 for x in s)]},
        "flip": [q for q, s in sign.items() if 1 in s and -1 in s],
        "old": {"gens": [[sum(1 for x in old[i].values() if x == y) for y in ("better", "worse", "mixed")] for i in range(4)],
                "combo": [[sum(vs.count(y) for vs in v[g].values()) for y in ("better", "worse")] for g in GENS],
                "stable": [sum(1 for x in old_q.values() if len(set(x)) == 1 and x[0] == y) for y in ("better", "worse")]},
    }


def main() -> None:
    out = {row: {"left": l, "right": r, **count(l, r)} for row, l, r in ROWS}
    (Path(__file__).resolve().parent / "recount.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    for row, x in out.items():
        o = x["old"]
        print(f"{row}\n  旧：逐组合 {' '.join(f'{a}/{b}' for a, b in o['combo'])}  逐题 {' '.join('/'.join(map(str, g)) for g in o['gens'])}  稳定 {o['stable'][0]}/{o['stable'][1]}")
        mean = {k: sum(a[k] for a in x['avg']) / 4 for k in ('better', 'worse', 'mixed')}
        print(f"  新：平均 {mean['better']:.1f}/{mean['worse']:.1f}/{mean['mixed']:.1f}  净值 {sum(x['net'])/4:+.1f}（{min(x['net']):+.1f}…{max(x['net']):+.1f}）"
              f"  稳定 ▲{len(x['stable']['better'])} ▼{len(x['stable']['worse'])}  翻转 {len(x['flip'])}")


if __name__ == "__main__":
    main()
