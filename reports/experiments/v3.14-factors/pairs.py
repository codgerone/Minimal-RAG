"""Single-factor comparisons of the query-side measures (proposal v3.14, 2026-10-29: analyse each measure
apart, the worse and mixed questions first).

Every comparison has two configurations of the comparison page (reports/query/v3.14/) that differ in
exactly one factor, at the purest level: one retrieval text per question (no fusion of phrasings) and,
with decomposition, no merge (each information item ranked in the sub-questions annotated for it).
The fusion and merge comparisons are a separate layer (how a measure is combined, not what it does).

Per comparison: the verdicts (question 3) on 13 combinations × 32 questions in generation 0 and in the
independent generations 1–3, which are stable (the same in all four), and per question and combination
the completion ranks of each information item on both sides.

Writes pairs.json next to this script. Run: python reports/experiments/v3.14-factors/pairs.py
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
PAGE = ROOT / "reports/query/v3.14"
GENS = ("0", "1", "2", "3")
KINDS = {"P3.2.1": "改写", "P3.2.2": "翻译", "P3.2.3": "HyDE", "P3.2.4": "step-back"}


def key(decompose=False, merge=None, route=False, strip=False, kinds=(), fuse=False) -> str:
    parts = []
    if decompose:
        parts.append("P3.1" + (f"@{merge}" if merge else "") + ("-o" if route else ""))
    if strip:
        parts.append("P3.2.0")
    if kinds:
        parts.append("+".join(kinds) + ("@rrf" if fuse else ""))
    return "_".join(parts)


def comparisons() -> list[dict]:
    """(factor, context, left, right); the context names what is held fixed."""
    out = []

    def add(factor, layer, context, left, right):
        out.append({"factor": factor, "layer": layer, "context": context, "left": left, "right": right})

    for d, where in ((False, "原问题"), (True, "子问题（不合并）")):
        # P3.2.0 on the original text and on each phrasing used alone
        add("P3.2.0", "单因素", f"{where} · 原句", key(d), key(d, strip=True))
        for m, name in KINDS.items():
            add("P3.2.0", "单因素", f"{where} · {name}", key(d, kinds=(m,)), key(d, strip=True, kinds=(m,)))
        # each phrasing in place of the original text, codes stripped on both sides and kept on both sides
        for m, name in KINDS.items():
            add(m, "单因素", f"{where} · 两侧去编码", key(d, strip=True), key(d, strip=True, kinds=(m,)))
            add(m, "单因素", f"{where} · 两侧带编码", key(d), key(d, kinds=(m,)))
    # decomposition: original question → its sub-questions, the same kind of retrieval text on both sides
    for strip in (False, True):
        tag = "去编码" if strip else "带编码"
        add("P3.1", "单因素", f"原句 · {tag}", key(strip=strip), key(True, strip=strip))
        for m, name in KINDS.items():
            add("P3.1", "单因素", f"{name} · {tag}", key(strip=strip, kinds=(m,)), key(True, strip=strip, kinds=(m,)))
    # how a measure is combined: fusing a phrasing with the original text (codes kept, as the original text)
    for d, where in ((False, "原问题"), (True, "子问题（不合并）")):
        for m, name in KINDS.items():
            add("RRF 融合", "组合方式", f"{where} · 原句 → 原句 + {name}", key(d), key(d, kinds=(m,), fuse=True))
            add("RRF 融合", "组合方式", f"{where} · {name} → 原句 + {name}", key(d, kinds=(m,)), key(d, kinds=(m,), fuse=True))
    for merge in ("rr", "rrf"):
        name = {"rr": "轮流取", "rrf": "全局 RRF"}[merge]
        add("合并", "组合方式", f"不合并 → {name}", key(True), key(True, merge))
        add("合并", "组合方式", f"{name} → {name} + 原问题一路", key(True, merge), key(True, merge, True))
    return out


def load(kind: str, k: str) -> dict:
    text = (PAGE / kind / f"{k or 'none'}.js").read_text(encoding="utf-8")
    return json.loads(re.search(r"\]=(.*);\s*$", text, re.S).group(1))


def vec(n):
    return sorted(math.inf if x is None else x for x in n)


def verdict(left, right) -> str:
    a, b = vec(left), vec(right)
    earlier = any(r < l for l, r in zip(a, b))
    later = any(r > l for l, r in zip(a, b))
    return "mixed" if earlier and later else "better" if earlier else "worse" if later else "same"


def ranks_by_gen(k: str) -> dict:
    """generation → combo → case → completion ranks."""
    facts = load("facts", k)
    out = {"0": {c: {q: f["n"] for q, f in cases.items()} for c, cases in facts.items()}}
    out.update(load("perturb", k))
    return out


def main() -> None:
    cache: dict[str, dict] = {}

    def ranks(k):
        if k not in cache:
            cache[k] = ranks_by_gen(k)
        return cache[k]

    result = []
    for comp in comparisons():
        left, right = ranks(comp["left"]), ranks(comp["right"])
        cells = {}
        for combo, cases in left["0"].items():
            for case in cases:
                vs = [verdict(left[g][combo][case], right[g][combo][case]) for g in GENS]
                cells.setdefault(case, {})[combo] = {
                    "v": vs, "l": [left[g][combo][case] for g in GENS], "r": [right[g][combo][case] for g in GENS]}
        counts = {g: {v: 0 for v in ("better", "worse", "mixed", "same")} for g in GENS}
        stable = {v: 0 for v in ("better", "worse", "mixed", "same")}
        for case in cells.values():
            for cell in case.values():
                for g, v in zip(GENS, cell["v"]):
                    counts[g][v] += 1
                if len(set(cell["v"])) == 1:
                    stable[cell["v"][0]] += 1
        result.append({**comp, "counts": counts, "stable": stable, "cells": cells})
    (HERE / "pairs.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    for r in result:
        c = r["counts"]
        print(f"{r['factor']:9} {r['context']:28} " + " ".join(f"{c[g]['better']:3}/{c[g]['worse']:3}/{c[g]['mixed']:2}" for g in GENS)
              + f" | stable {r['stable']['better']}/{r['stable']['worse']}/{r['stable']['mixed']}")


if __name__ == "__main__":
    main()
