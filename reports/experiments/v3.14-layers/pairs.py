"""Single-factor comparisons on the 120 configurations (proposal v3.14, "分层评估"; step 8 of the
handover): original route off / on, round robin vs global RRF, unmerged vs merged, each phrasing
fused by RRF vs unfused vs off. Every pair differs in one factor; each is also checked across the
other factor's states (the "context" of the pair), so a factor's effect is not read off one
configuration only.

Reads reports/query/v3.14/data.json (no retrieval, no model calls); writes pairs.json next to this
script. The facts listed per pair follow the proposal's question 5.
Run: python reports/experiments/v3.14-layers/pairs.py
"""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

from rag.eval.query_compare import PHRASING_OF, QueryConfig, completion_vector, evaluated_configs, verdict

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
ACCEPTANCE = (3, 6, 9, 12)
DATA = json.loads((ROOT / "reports/query/v3.14/data.json").read_text(encoding="utf-8"))
COMBOS = [c["label"] for c in DATA["combos"]]
TABLE = {c["label"]: c["table_return_unit"] == "table" for c in DATA["combos"]}
CASES = {c["case_id"]: c for c in DATA["cases"]}
# question types of the new questions (proposal, "新题与题型"); the others are the 24 earlier questions
TYPES = {"PO-25-19-Q005": "分解题", "CONTRACT-105-Q011": "分解题", "PO-25-19-Q006": "回答上的多跳",
         "CONTRACT-105-Q013": "回答上的多跳", "CONTRACT-105-Q014": "回答上的多跳", "PO-25-19-Q009": "检索多跳",
         "PO-25-19-Q007": "隐含前提", "CONTRACT-105-Q012": "隐含前提"}


def complete(n, k):
    return all(x is not None and x <= k for x in n)


def consistency(per_case: dict[str, Counter]) -> dict[str, list[str]]:
    """Questions by how their verdicts agree across the combinations (not a vote): changed the same
    way in every combination where they changed, or in different directions."""
    out = {"better": [], "worse": [], "mixed": [], "split": []}
    for case, counter in sorted(per_case.items()):
        changed = [v for v in ("better", "worse", "mixed") if counter[v]]
        if len(changed) == 1:
            out[changed[0]].append(case)
        elif changed:
            out["split"].append(case)
    return out


def compare(left: str, right: str) -> dict:
    """The facts of question 5 for `left vs right` (configuration keys)."""
    L, R = DATA["configs"][left], DATA["configs"][right]
    merged = QueryConfig.parse(left).merged and QueryConfig.parse(right).merged
    per_combo, per_case = {}, {}
    for combo in COMBOS:
        counts = Counter()
        for case, rf in R[combo].items():
            lf = L[combo][case]
            v = verdict(completion_vector(lf["n"]), completion_vector(rf["n"]))
            counts[v] += 1
            per_case.setdefault(case, Counter())[v] += 1
        row = {"verdicts": dict(counts)}
        if merged:
            row["regress"] = sorted(c for c in R[combo] if any(complete(L[combo][c]["n"], k) and not complete(R[combo][c]["n"], k)
                                                              for k in ACCEPTANCE))
            row["complete"] = {k: [sum(complete(f["n"], k) for f in L[combo].values()),
                                   sum(complete(f["n"], k) for f in R[combo].values())] for k in ACCEPTANCE}
        per_combo[combo] = row
    cost = {side: [sum(f["c"][i] for c in COMBOS for f in X[c].values()) / sum(len(X[c]) for c in COMBOS)
                   for i in (0, 1, 3, 4)] for side, X in (("left", L), ("right", R))}
    total = lambda group: dict(sum((Counter(per_combo[c]["verdicts"]) for c in group), Counter()))
    by_items, by_type = {}, {}
    for case, counter in per_case.items():
        items = len(CASES[case]["groups"])
        key = "1 项" if items == 1 else "2 项" if items == 2 else "3 项及以上"
        by_items.setdefault(key, Counter()).update(counter)
        by_type.setdefault(TYPES.get(case, "原 24 题"), Counter()).update(counter)
    return {"left": left, "right": right, "merged": merged, "combos": per_combo,
            "total": total(COMBOS), "table": total([c for c in COMBOS if TABLE[c]]),
            "chunk": total([c for c in COMBOS if not TABLE[c]]),
            "by_items": {k: dict(v) for k, v in sorted(by_items.items())},
            "by_type": {k: dict(v) for k, v in by_type.items()},
            "cases": {c: dict(v) for c, v in sorted(per_case.items()) if v["same"] != len(COMBOS)},
            "consistency": consistency(per_case),
            # retrievals, retrieval seconds, LLM seconds, LLM $ per question over the combinations
            "cost": cost}


def key(**kw) -> str:
    return QueryConfig(**kw).key


PHRASING_STATES = sorted({(c.phrasings, c.fuse) for c in evaluated_configs()}, key=lambda x: (len(x[0]), x))
MERGE_NAMES = {"rr": "轮流取", "rrf": "全局 RRF"}
NAMES = {"P3.2.1": "改写", "P3.2.2": "翻译", "P3.2.3": "HyDE", "P3.2.4": "step-back"}
DECOMPOSITIONS = {"不分解": {}, "分解不合并": dict(decompose=True),
                  **{f"分解{MERGE_NAMES[m]}{' + 原问题一路' if o else ''}": dict(decompose=True, merge=m, original_route=o)
                     for m in ("rr", "rrf") for o in (False, True)}}


def group(name: str, pairs: list[tuple[str, str, str]]) -> dict:
    """pairs: (context, left key, right key)."""
    return {"name": name, "pairs": [{"context": ctx, **compare(l, r)} for ctx, l, r in pairs]}


def ph_name(phrasings, fuse) -> str:
    if not phrasings:
        return "无写法"
    return " + ".join(NAMES[m] for m in phrasings) + ("（RRF）" if fuse else "（不融合）")


def line(t):
    return f"+{t.get('better', 0)} −{t.get('worse', 0)} ◆{t.get('mixed', 0)} ={t.get('same', 0)}"


def main() -> None:
    groups = []
    # 1. the original route: off → on, per merge, across phrasing states
    for merge in ("rr", "rrf"):
        groups.append(group(f"原问题一路 关 → 开（{MERGE_NAMES[merge]}）", [
            (ph_name(p, f), key(decompose=True, merge=merge, phrasings=p, fuse=f),
             key(decompose=True, merge=merge, original_route=True, phrasings=p, fuse=f)) for p, f in PHRASING_STATES]))
    # 2. merge: round robin → global RRF, without and with the original route
    for o in (False, True):
        groups.append(group(f"轮流取 → 全局 RRF（原问题一路{'开' if o else '关'}）", [
            (ph_name(p, f), key(decompose=True, merge="rr", original_route=o, phrasings=p, fuse=f),
             key(decompose=True, merge="rrf", original_route=o, phrasings=p, fuse=f)) for p, f in PHRASING_STATES]))
    # 3. unmerged → merged
    for name, d in DECOMPOSITIONS.items():
        if d.get("merge"):
            groups.append(group(f"{name.replace('分解', '分解不合并 → ', 1)}", [
                (ph_name(p, f), key(decompose=True, phrasings=p, fuse=f), key(**d, phrasings=p, fuse=f))
                for p, f in PHRASING_STATES]))
    # 4. decomposition off → on (each decomposition state), across phrasing states
    for name, d in DECOMPOSITIONS.items():
        if d:
            groups.append(group(f"分解 不开 → {name.removeprefix('分解')}", [
                (ph_name(p, f), key(phrasings=p, fuse=f), key(**d, phrasings=p, fuse=f)) for p, f in PHRASING_STATES]))
    # 5. each phrasing: off → RRF, off → unfused, unfused → RRF; per decomposition state
    for m in PHRASING_OF:
        for (pa, fa), (pb, fb), title in ((((), True), ((m,), True), "不开 → RRF"),
                                          (((), True), ((m,), False), "不开 → 不融合"),
                                          (((m,), False), ((m,), True), "不融合 → RRF")):
            groups.append(group(f"{m} {NAMES[m]} {title}", [
                (name, key(**d, phrasings=pa, fuse=fa), key(**d, phrasings=pb, fuse=fb)) for name, d in DECOMPOSITIONS.items()]))
        # marginal: added to the other phrasings under RRF
        others = [p for p, f in PHRASING_STATES if f and m not in p]
        groups.append(group(f"{m} {NAMES[m]} 加入其他写法（RRF，不分解）", [
            (ph_name(p, True), key(phrasings=p, fuse=True) if p else "",
             key(phrasings=tuple(x for x in PHRASING_OF if x in p or x == m), fuse=True)) for p in others]))
    (HERE / "pairs.json").write_text(json.dumps({"groups": groups}, ensure_ascii=False, indent=1), encoding="utf-8")
    for g in groups:
        print("##", g["name"])
        for p in g["pairs"]:
            reg = sum(len(r.get("regress", [])) for r in p["combos"].values()) if p["merged"] else "—"
            c = p["cost"]
            q = {k: len(v) for k, v in p["consistency"].items()}
            print(f"  {p['context']:<34} 合计 {line(p['total']):<22} 整表 {line(p['table']):<20} chunk {line(p['chunk']):<20}"
                  f" 题：一致变好 {q['better']} 一致变差 {q['worse']} 一致各有优势 {q['mixed']} 方向不一 {q['split']}"
                  f" 退化题次 {reg}  检索 {c['left'][0]:.1f}→{c['right'][0]:.1f}")


if __name__ == "__main__":
    main()
