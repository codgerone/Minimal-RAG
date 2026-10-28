"""Review page for steps 5–8 of the V3.14 handover: the single-factor comparisons (pairs.json), the
perturbation test (the independent generations in reports/query/v3.14/data.json) and the proposed
category of every module (decision 015, awaiting the user's decision).

Writes index.html next to this script. Run after pairs.py:
python reports/experiments/v3.14-layers/review.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from urllib.parse import quote

from rag.eval.query_compare import QueryConfig, completion_vector, verdict
from rag.reports.html import esc, page, write_assets

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
DATA = json.loads((ROOT / "reports/query/v3.14/data.json").read_text(encoding="utf-8"))
PAIRS = json.loads((HERE / "pairs.json").read_text(encoding="utf-8"))["groups"]
COMBOS = [c["label"] for c in DATA["combos"]]
ACCEPTANCE = (3, 6, 9, 12)
PAGE = "../../query/v3.14/index.html"
CONFIG_NAME = {"": "V3.13"}


def label(key: str) -> str:
    if key in CONFIG_NAME:
        return CONFIG_NAME[key]
    c = QueryConfig.parse(key)
    names = {"P3.2.1": "改写", "P3.2.2": "翻译", "P3.2.3": "HyDE", "P3.2.4": "step-back"}
    parts = []
    if c.decompose:
        parts.append("分解（" + ({"rr": "轮流取", "rrf": "全局 RRF"}.get(c.merge, "不合并"))
                     + ("+原问题一路" if c.original_route else "") + "）")
    if c.phrasings:
        parts.append(" + ".join(names[m] for m in c.phrasings) + ("（RRF）" if c.fuse else "（不融合）"))
    return " · ".join(parts)


def link(left: str, right: str, text: str = "对比页") -> str:
    return f'<a href="{PAGE}#L={quote(left, safe="")}&R={quote(right, safe="")}&k=3" target="_blank">{esc(text)}</a>'


def counts(t: dict) -> str:
    return (f'<span class="up">▲{t.get("better", 0)}</span> <span class="down">▼{t.get("worse", 0)}</span> '
            f'◆{t.get("mixed", 0)} <span class="same">={t.get("same", 0)}</span>')


def lean(t: dict) -> str:
    b, w = t.get("better", 0), t.get("worse", 0)
    return "ok" if b > w else "bad" if w > b else ""


# ---------- single-factor comparisons ----------

FAMILIES = (   # tab id, title, group-name prefixes
    ("route", "原问题一路", ("原问题一路",)),
    ("merge", "合并方式", ("轮流取 → 全局 RRF", "分解不合并 → ")),
    ("decompose", "分解", ("分解 不开",)),
    ("rewrite", "改写 P3.2.1", ("P3.2.1 ",)),
    ("translation", "翻译 P3.2.2", ("P3.2.2 ",)),
    ("hyde", "HyDE P3.2.3", ("P3.2.3 ",)),
    ("step_back", "step-back P3.2.4", ("P3.2.4 ",)),
)


def group_table(g: dict) -> str:
    rows = []
    for p in g["pairs"]:
        q = p["consistency"]
        reg = sum(len(r.get("regress", [])) for r in p["combos"].values()) if p["merged"] else "—"
        c = p["cost"]
        detail = "；".join(f'{n}：{"、".join(q[k])}' for k, n in (("better", "一致变好"), ("worse", "一致变差"),
                                                                 ("mixed", "一致各有优势"), ("split", "方向不一")) if q[k])
        rows.append(
            f'<tr><td class="nw">{esc(p["context"])}</td><td class="num {lean(p["total"])}">{counts(p["total"])}</td>'
            f'<td class="num">{counts(p["table"])}</td><td class="num">{counts(p["chunk"])}</td>'
            f'<td class="num" title="{esc(detail)}"><span class="up">{len(q["better"])}</span> / '
            f'<span class="down">{len(q["worse"])}</span> / {len(q["mixed"])} / {len(q["split"])}</td>'
            f'<td class="num">{reg}</td><td class="num">{c["left"][0]:.1f} → {c["right"][0]:.1f}</td>'
            f'<td class="nw">{link(p["left"], p["right"])}</td></tr>')
    total = sum((Counter(p["total"]) for p in g["pairs"]), Counter())
    sides = Counter("+" if p["total"].get("better", 0) > p["total"].get("worse", 0) else
                    "−" if p["total"].get("worse", 0) > p["total"].get("better", 0) else "=" for p in g["pairs"])
    return (f'<h3>{esc(g["name"])}</h3><div class="meta">各语境合计（题次）：{counts(total)}；'
            f'变好多于变差的语境 {sides["+"]} 个、变差多于变好的 {sides["−"]} 个、相等的 {sides["="]} 个（共 {len(g["pairs"])} 个）。</div>'
            '<div class="scroll"><table><tr><th>语境（另一因素的状态）</th><th class="num">13 种组合合计（题次）</th>'
            '<th class="num">返回整表 7 种</th><th class="num">返回命中 chunk 6 种</th>'
            '<th class="num" title="变化方向在各组合间一致的题数（悬停看题号）：一致变好 / 一致变差 / 一致各有优势 / 方向因组合而异">题：▲ / ▼ / ◆ / 不一</th>'
            '<th class="num" title="验收 K（3、6、9、12）中任一档由完整覆盖变为不完整，13 种组合合计题次；任一侧不合并时不计算">最坏退化（题次）</th>'
            '<th class="num">每题检索次数</th><th></th></tr>' + "".join(rows) + '</table></div>')


def comparisons() -> tuple[str, str]:
    tabs, panels = [], []
    for tid, title, prefixes in FAMILIES:
        groups = [g for g in PAIRS if g["name"].startswith(prefixes)]
        tabs.append(f'<a href="#{tid}" data-tab="{tid}">{esc(title)}</a>')
        panels.append(f'<div data-panel="{tid}">' + "".join(group_table(g) for g in groups) + "</div>")
    return '<div class="tabs">' + "".join(tabs) + "</div>", "".join(panels)


# ---------- perturbation ----------

def n_of(key: str, g: int, combo: str, case: str):
    if g == 0:
        return DATA["configs"][key][combo][case]["n"]
    return DATA["perturbation"]["configs"][key][str(g)][combo][case]


def generations() -> list[int]:
    return [0] + DATA["perturbation"]["generations"] if "perturbation" in DATA else [0]


def stability(left: str, right: str) -> dict:
    """Per generation the verdict counts, and the questions × combinations whose verdict is the same in
    every generation."""
    gens = generations()
    per_gen = {g: Counter() for g in gens}
    stable = Counter()
    unstable = Counter()
    for combo in COMBOS:
        for case in DATA["configs"][right][combo]:
            vs = [verdict(completion_vector(n_of(left, g, combo, case)), completion_vector(n_of(right, g, combo, case)))
                  for g in gens]
            for g, v in zip(gens, vs):
                per_gen[g][v] += 1
            if len(set(vs)) == 1:
                stable[vs[0]] += 1
            else:
                unstable[case] += 1
    return {"per_gen": per_gen, "stable": stable, "unstable": unstable}


def acceptance(key: str, g: int) -> tuple[int, list[str]]:
    """Combinations passing acceptance item 1 against V3.13 in generation g, and the failing ones."""
    fails = []
    for combo in COMBOS:
        d = [sum(all(x is not None and x <= k for x in n_of(key, g, combo, c)) for c in DATA["configs"][key][combo])
             - sum(all(x is not None and x <= k for x in n_of("", g, combo, c)) for c in DATA["configs"][""][combo])
             for k in ACCEPTANCE]
        if not (min(d) >= 0 and sum(x >= 2 for x in d) >= 2):
            fails.append(f'{combo} {"/".join(f"{x:+d}" for x in d)}')
    return len(COMBOS) - len(fails), fails


def perturbation_section(pairs: list[tuple[str, str, str]], candidates: list[str]) -> str:
    if "perturbation" not in DATA:
        return '<div class="meta">扰动测试尚未完成。</div>'
    gens = generations()
    rows = []
    for title, left, right in pairs:
        s = stability(left, right)
        per = " ｜ ".join(f'第 {g} 次 {counts(s["per_gen"][g])}' for g in gens)
        worst = "、".join(f"{c}（{n}）" for c, n in s["unstable"].most_common(6))
        rows.append(f'<tr><td>{esc(title)}<div class="meta">{esc(label(left))} → {esc(label(right))}</div></td>'
                    f'<td class="num">{counts(s["stable"])}</td><td class="meta">{per}</td>'
                    f'<td class="meta">{esc(worst) or "无"}</td><td class="nw">{link(left, right)}</td></tr>')
    acc = []
    for key in candidates:
        cells = []
        for g in gens:
            ok, fails = acceptance(key, g)
            cells.append(f'<td class="num {"ok" if ok == len(COMBOS) else ""}" title="{esc("；".join(fails))}">{ok} / {len(COMBOS)}</td>')
        acc.append(f'<tr><td>{esc(label(key))}</td>' + "".join(cells) + "</tr>")
    plans = DATA["perturbation"]["plans"]
    dec = DATA["perturbation"]["decomposition"]
    base = {c: [q["text"] for q in p["decomposed"]["questions"]] for c, p in DATA["plans"].items()}
    quality = []
    for g in ["0"] + [str(x) for x in DATA["perturbation"]["generations"]]:
        marks = DATA["decomposition"] if g == "0" else dec[g]
        same = "—" if g == "0" else sum(plans[g][c]["decomposed"] == base[c] for c in base)
        omitted = sum(sum(not v for v in m["mapping"].values()) for m in marks.values())
        quality.append(f'<tr><td>第 {g} 次</td><td class="num">{same}</td><td class="num">{omitted}</td>'
                       f'<td class="num">{sum(len(m["extra"]) for m in marks.values())}</td>'
                       f'<td class="num">{sum(len(m["multi"]) for m in marks.values())}</td>'
                       f'<td class="num">{sum(len(m["lost_qualifier"]) for m in marks.values())}</td>'
                       f'<td class="num">{sum(len(p["degraded"]) for p in plans[g].values()) if g != "0" else 0}</td></tr>')
    return ('<h3>候选配置与逐个去掉一个模块</h3><div class="meta">每行一对配置。"稳定"为 13 种组合 × 32 题中，'
            f'{len(gens)} 次生成（第 0 次冻结 + 独立生成）判定都相同的题次，按类列出；右列为判定不稳定次数最多的题（括号内为不稳定的组合数）。</div>'
            '<div class="scroll"><table><tr><th>比较</th><th class="num">稳定的题次</th><th>各次生成的题次</th><th>不稳定最多的题</th><th></th></tr>'
            + "".join(rows) + '</table></div>'
            '<h3>验收第 1 条（相对 V3.13）</h3><div class="meta">每格为满足"K = 3、6、9、12 下完整覆盖都不低于 V3.13，且至少两档多 2 题及以上"的组合数；悬停看不满足的组合与四档差值。</div>'
            '<div class="scroll"><table><tr><th>配置</th>' + "".join(f'<th class="num">第 {g} 次</th>' for g in gens) + '</tr>'
            + "".join(acc) + '</table></div>'
            '<h3>分解的生成质量</h3><div class="meta">子问题与信息项的对应由助手逐次标注（判定标准见提案"分层评估"）。'
            '"与第 0 次相同"为子问题文字完全相同的题数（32 题）。</div>'
            '<div class="scroll"><table><tr><th>生成</th><th class="num">与第 0 次相同</th><th class="num">遗漏的信息项</th>'
            '<th class="num">多余子问题</th><th class="num">一问多项</th><th class="num">丢失限定条件</th><th class="num">规划降级</th></tr>'
            + "".join(quality) + '</table></div>')


def render(conclusion: str, perturbation_pairs, candidates) -> str:
    tabs, panels = comparisons()
    body = (
        '<style>td.ok{background:var(--ok-bg)}td.bad{background:var(--bad-bg)}</style>'
        '<h1>V3.14 分层评估与归类（待你审核）</h1>'
        f'<div class="sub">冻结的规划（第 0 次生成）上 120 种配置 × 13 种组合 × 32 题；判定按提案拍板问题 3（凑齐向量的支配关系）。'
        f'数据：reports/query/v3.14/data.json；对照：pairs.json；生成：review.py。</div>'
        + conclusion +
        '<h2>单因素对照</h2><div class="meta">每组两侧只差一个因素；同一组在另一因素的每个状态（"语境"）下各比一次，'
        '看效果是否依赖语境。13 种组合共用同一批题，组合数只表示一致程度；"题"一列按题数计，比题次更接近独立样本。'
        '点"对比页"打开这一对配置的逐题结果。</div>'
        + tabs + panels +
        '<h2>扰动测试</h2>' + perturbation_section(perturbation_pairs, candidates))
    return page("V3.14 分层评估与归类", body, root="../../", crumbs=[("报告首页", "../../index.html")])


if __name__ == "__main__":
    conclusion_path = HERE / "conclusion.html"
    conclusion = conclusion_path.read_text(encoding="utf-8") if conclusion_path.is_file() else ""
    config = json.loads((HERE / "candidates.json").read_text(encoding="utf-8")) if (HERE / "candidates.json").is_file() \
        else {"pairs": [], "candidates": []}
    write_assets(ROOT / "reports")
    (HERE / "index.html").write_text(render(conclusion, config["pairs"], config["candidates"]), encoding="utf-8")
    print("written", HERE / "index.html")
