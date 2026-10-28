"""Re-accounting of the existing query comparison data under the 2026-10-28 rule (proposal v3.14,
question 3): completion vectors compared by dominance, against the retired Σ ln rule.

Reads reports/query/v3.14/data.json; writes summary.json and tables.md next to this script.
Run: python reports/experiments/v3.14-reaccount/reaccount.py
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OLD_THRESHOLD = math.log(1.5)
NAMES = {"better": "变好", "worse": "变差", "mixed": "各有优势", "same": "不变"}
INF = math.inf


def vector(needed: list[int | None]) -> list[float]:
    return sorted(INF if n is None else n for n in needed)


def verdict(left: list[float], right: list[float]) -> str:
    earlier = any(r < l for l, r in zip(left, right))
    later = any(r > l for l, r in zip(left, right))
    return "mixed" if earlier and later else "better" if earlier else "worse" if later else "same"


def leads(left: list[float], right: list[float]) -> dict[str, list[tuple[int, int]]]:
    """K ranges where each side has more information items complete."""
    top = int(max([x for x in left + right if x != INF], default=1))
    out: dict[str, list[tuple[int, int]]] = {"left": [], "right": []}
    for k in range(1, top + 1):
        a, b = sum(x <= k for x in left), sum(x <= k for x in right)
        side = "left" if a > b else "right" if b > a else None
        if side:
            ranges = out[side]
            if ranges and ranges[-1][1] == k - 1:
                ranges[-1] = (ranges[-1][0], k)
            else:
                ranges.append((k, k))
    return out


def old_change(left: dict[str, int], right: dict[str, int]) -> tuple[float, list[float]]:
    parts = [math.log(left[k] / right[k]) for k in left if k in right]
    return sum(parts), parts


def old_verdict(change: float) -> str:
    return "better" if change > OLD_THRESHOLD else "worse" if change < -OLD_THRESHOLD else "same"


def reason(old: str, new: str, change: float, parts: list[float], same_vector: bool,
           first_moved: bool) -> str:
    if same_vector:
        return "凑齐名次不变，只有证据单元名次变化" + ("（首块名次也变了）" if first_moved else "")
    if new == "mixed":
        return "凑齐向量交叉，新判为各有优势"
    if old == "same":
        if any(p > 0 for p in parts) and any(p < 0 for p in parts) and abs(change) <= OLD_THRESHOLD:
            return "对数抵消或被阈值抹掉（各单元有进有退）"
        return "被阈值抹掉（单方向小幅移动）"
    return "方向相反"


def fmt(v: list[float]) -> str:
    return "·".join("∞" if x == INF else str(int(x)) for x in v)


def fmt_leads(l: dict[str, list[tuple[int, int]]]) -> str:
    def r(rs):
        return "、".join(f"K={a}" if a == b else f"K={a}–{b}" for a, b in rs)
    parts = []
    if l["left"]:
        parts.append(f"左领先 {r(l['left'])}")
    if l["right"]:
        parts.append(f"右领先 {r(l['right'])}")
    return "；".join(parts)


def main() -> None:
    data = json.loads((ROOT / "reports/query/v3.14/data.json").read_text(encoding="utf-8"))
    configs = data["configs"]
    combos = [c["label"] for c in data["combos"]]
    cases = {c["case_id"]: c for c in data["cases"]}
    modules = data["evaluated"]
    full = "+".join(modules)
    pairs = [("", m) for m in modules] + [("", full)]
    pairs += [("+".join(x for x in modules if x != m), full) for m in modules]

    summary: dict[str, dict] = {}
    crosstab: Counter = Counter()
    disagreements: list[dict] = []
    for left_key, right_key in pairs:
        name = f"{left_key or 'V3.13'} → {right_key}"
        rows = {}
        for combo in combos:
            old, new = Counter(), Counter()
            for cid in cases:
                L, R = configs[left_key][combo][cid], configs[right_key][combo][cid]
                lv, rv = vector(L["n"]), vector(R["n"])
                v = verdict(lv, rv)
                change, parts = old_change(L["e"], R["e"])
                o = old_verdict(change)
                old[o] += 1
                new[v] += 1
                crosstab[(o, v)] += 1
                if o != v:
                    disagreements.append({
                        "pair": name, "combo": combo, "case": cid, "old": o, "new": v,
                        "change": round(change, 3), "left": fmt(lv), "right": fmt(rv),
                        "leads": fmt_leads(leads(lv, rv)) if v == "mixed" else "",
                        "first": [L["f"], R["f"]],
                        "reason": reason(o, v, change, parts, lv == rv, L["f"] != R["f"])})
            rows[combo] = {"old": dict(old), "new": dict(new)}
        summary[name] = rows

    (HERE / "summary.json").write_text(json.dumps(
        {"summary": summary, "crosstab": {f"{o}→{n}": c for (o, n), c in crosstab.items()},
         "disagreements": disagreements}, ensure_ascii=False, indent=1), encoding="utf-8")

    out = ["# V3.14 重新记账：凑齐向量支配关系 vs 旧变化量 Σ ln", "",
           "数据：`reports/query/v3.14/data.json`（第 0 次生成的冻结规划，返回顺序含轮流取，开 P3.1 时带原问题一路）。"
           "新判定见提案 v3.14 拍板问题 3（2026-10-28）；旧判定为 Σ ln(左/右)、阈值 ln 1.5。", "",
           "## 1. 各组对比，13 种组合合计（题次）", "",
           "| 对比 | 旧：变好 / 变差 / 不变 | 新：变好 / 变差 / 各有优势 / 不变 |", "|---|---|---|"]
    for name, rows in summary.items():
        o = Counter(); n = Counter()
        for r in rows.values():
            o.update(r["old"]); n.update(r["new"])
        out.append(f"| {name} | {o['better']} / {o['worse']} / {o['same']} | "
                   f"{n['better']} / {n['worse']} / {n['mixed']} / {n['same']} |")
    out += ["", "## 2. 旧判定 × 新判定（全部对比 × 组合 × 题）", "",
            "| 旧 \\ 新 | 变好 | 变差 | 各有优势 | 不变 |", "|---|---|---|---|---|"]
    for o in ("better", "worse", "same"):
        out.append(f"| {NAMES[o]} | " + " | ".join(str(crosstab[(o, v)]) for v in NAMES) + " |")
    out += ["", "## 3. 不一致的原因", "", "| 原因 | 题次 |", "|---|---|"]
    for r, c in Counter(d["reason"] for d in disagreements).most_common():
        out.append(f"| {r} | {c} |")
    out += ["", "## 4. 不一致的题（按对比、题合并组合）", ""]
    grouped = defaultdict(list)
    for d in disagreements:
        grouped[(d["pair"], d["case"], d["old"], d["new"], d["reason"])].append(d)
    current = None
    for (pair, cid, o, v, r), ds in grouped.items():
        if pair != current:
            current = pair
            out += ["", f"### {pair}", "", "| 题 | 旧 → 新 | 原因 | 组合（旧变化量；左向量 → 右向量） |", "|---|---|---|---|"]
        detail = "<br>".join(f"{d['combo']}（{d['change']:+}；{d['left']} → {d['right']}"
                             + (f"；{d['leads']}" if d["leads"] else "") + "）" for d in ds)
        out.append(f"| {cid} | {NAMES[o]} → {NAMES[v]} | {r} | {detail} |")
    out += ["", "## 5. 附：各组合明细（新：变好 / 变差 / 各有优势 / 不变；旧：变好 / 变差）", ""]
    out += ["| 对比 | " + " | ".join(combos) + " |", "|---|" + "---|" * len(combos)]
    for name, rows in summary.items():
        cells = []
        for c in combos:
            n, o = rows[c]["new"], rows[c]["old"]
            cells.append(f"{n.get('better', 0)}/{n.get('worse', 0)}/{n.get('mixed', 0)}/{n.get('same', 0)}"
                         f"<br>旧 {o.get('better', 0)}/{o.get('worse', 0)}")
        out.append(f"| {name} | " + " | ".join(cells) + " |")
    (HERE / "tables.md").write_text("\n".join(out) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
