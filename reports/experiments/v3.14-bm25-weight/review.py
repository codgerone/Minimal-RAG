"""Review page for the BM25 weight experiment (run.py): every α against α = 1 on the same retrieval
text, by the verdicts of proposal question 3, in generations 0–3; the adoption rule of question 6.

Writes summary.json and index.html next to this script. Run: python reports/experiments/v3.14-bm25-weight/review.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from rag.reports.html import esc, page, write_assets

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
RANKS = json.loads((HERE / "ranks.json").read_text(encoding="utf-8"))
ALPHAS = list(RANKS)
GENS = list(RANKS["1.0"])
TEXTS = [("P3.2.0", "原问题 · 原句"), ("P3.2.0_P3.2.2", "原问题 · 只用翻译"), ("P3.2.0_P3.2.2@rrf", "原问题 · 原句 + 翻译 RRF"),
         ("P3.1_P3.2.0", "子问题 · 原句"), ("P3.1_P3.2.0_P3.2.2", "子问题 · 只用翻译"),
         ("P3.1_P3.2.0_P3.2.2@rrf", "子问题 · 原句 + 翻译 RRF")]
SIGN = {"better": ('▲', "up"), "worse": ('▼', "down"), "mixed": ('◆', ""), "same": ('·', "same")}
PAGE = "../../query/v3.14/index.html"


def vec(n):
    return sorted(math.inf if x is None else x for x in n)


def verdict(left, right) -> str:
    a, b = vec(left), vec(right)
    e, l = any(y < x for x, y in zip(a, b)), any(y > x for x, y in zip(a, b))
    return "mixed" if e and l else "better" if e else "worse" if l else "same"


def question(alpha: str, g: str, key: str, case: str) -> str:
    """A question's verdict in one generation: the one direction its changed combinations share, or mixed."""
    base, mine = RANKS["1.0"][g][key], RANKS[alpha][g][key]
    s = {verdict(base[c][case], mine[c][case]) for c in base} - {"same"}
    return "same" if not s else s.pop() if len(s) == 1 else "mixed"


def compare(alpha: str, key: str) -> dict:
    cases = list(next(iter(RANKS["1.0"]["0"][key].values())))
    per = {q: [question(alpha, g, key, q) for g in GENS] for q in cases}
    stable = {v: [q for q, x in per.items() if len(set(x)) == 1 and x[0] == v] for v in ("better", "worse", "mixed")}
    combos = {v: sum(verdict(RANKS["1.0"][g][key][c][q], RANKS[alpha][g][key][c][q]) == v
                     for g in GENS for c in RANKS["1.0"][g][key] for q in cases) for v in ("better", "worse", "mixed")}
    return {"per": per, "stable": stable, "flip": [q for q, x in per.items() if "better" in x and "worse" in x],
            "combos": combos}


def mark(v: str) -> str:
    s, cls = SIGN[v]
    return f'<span class="{cls}">{s}</span>'


def main() -> None:
    result = {a: {k: compare(a, k) for k, _ in TEXTS} for a in ALPHAS if a != "1.0"}
    passing = [a for a, by in result.items()
               if all(len(c["stable"]["better"]) > len(c["stable"]["worse"]) for c in by.values())]
    net = {a: sum(len(c["stable"]["better"]) - len(c["stable"]["worse"]) for c in by.values()) for a, by in result.items()}
    chosen = max(passing, key=lambda a: (net[a], float(a))) if passing else None
    (HERE / "summary.json").write_text(json.dumps(
        {"passing": passing, "net": net, "chosen": chosen,
         "stable": {a: {k: {v: len(x) for v, x in c["stable"].items()} for k, c in by.items()} for a, by in result.items()}},
        ensure_ascii=False, indent=1), encoding="utf-8")

    rows = []
    for a, by in result.items():
        cells = []
        for k, _ in TEXTS:
            s = by[k]["stable"]
            ok = len(s["better"]) > len(s["worse"])
            bg = "var(--ok-bg)" if ok else "var(--bad-bg)" if len(s["worse"]) > len(s["better"]) else ""
            cells.append(f'<td class="num nw" style="background:{bg}"><span class="up">▲{len(s["better"])}</span> '
                         f'<span class="down">▼{len(s["worse"])}</span> ◆{len(s["mixed"])}'
                         + (f' <span class="meta">翻转 {len(by[k]["flip"])}</span>' if by[k]["flip"] else "") + '</td>')
        rows.append(f'<tr><td class="num nw"><b>α = {a}</b></td>{"".join(cells)}<td class="num">{net[a]:+d}</td>'
                    f'<td>{"满足" if a in passing else "—"}</td></tr>')
    table = ('<div class="scroll"><table><tr><th>α（对 α = 1）</th>' + "".join(f'<th class="num">{esc(n)}</th>' for _, n in TEXTS)
             + '<th class="num">净值合计</th><th>采纳规则</th></tr>' + "".join(rows) + '</table></div>')

    combo_rows = "".join(
        f'<tr><td class="num nw">α = {a}</td>' + "".join(
            f'<td class="num nw"><span class="up">{by[k]["combos"]["better"]}</span>/<span class="down">{by[k]["combos"]["worse"]}</span>/{by[k]["combos"]["mixed"]}</td>'
            for k, _ in TEXTS) + '</tr>' for a, by in result.items())

    tabs = []
    for k, name in TEXTS:
        cases = list(result["0.0"][k]["per"])
        changed = [q for q in cases if any(set(result[a][k]["per"][q]) != {"same"} for a in result)]
        body = "".join(f'<tr><td class="nw">{esc(q)}</td>' + "".join(
            f'<td class="nw">{"".join(mark(v) for v in result[a][k]["per"][q])}</td>' for a in result) + '</tr>' for q in changed)
        tabs.append((k, name, f'<p class="sub">{len(changed)} / {len(cases)} 题在某档 α、某次生成中有变化；每格 4 个符号依次为第 0–3 次生成的按题判定（对 α = 1）。</p>'
                     '<div class="scroll"><table><tr><th>题</th>' + "".join(f'<th class="num">{a}</th>' for a in result) + '</tr>'
                     + body + '</table></div>'))
    nav = '<div class="tabs">' + "".join(f'<a href="#t{i}" data-tab="t{i}">{esc(n)}</a>' for i, (_, n, _) in enumerate(tabs)) + '</div>'
    panels = "".join(f'<div data-panel="t{i}" class="card">{b}</div>' for i, (_, _, b) in enumerate(tabs))

    verdict_text = (f'按采纳规则，<b>α = {chosen}</b> 入选（满足规则的档：{"、".join(passing)}；多档满足时取净值合计最大、相同取更接近 1 的一档，此条待你确认）。'
                    if chosen else '<b>没有一档 α 满足采纳规则</b>（6 种检索文本上都是 4 次都变好的题多于 4 次都变差的题），按规则保持 α = 1。')
    body = (
        '<h1>混合检索的 BM25 权重</h1>'
        '<div class="sub">V3.14 · 提案问题 6 · 决策 017 · 13 组合 × 32 题 × 第 0–3 次生成 · 判定为凑齐向量的支配关系（提案问题 3）</div>'
        f'<div class="card"><h3>结论</h3><p>{verdict_text}</p>{{conclusion}}</div>'
        '<div class="card"><h3>各档 α 对 α = 1（按题，4 次生成都相同才计入）</h3>'
        '<p class="sub">融合得分 = 1/(60 + 向量名次) + α/(60 + BM25 名次)。每格：4 次都变好 ▲ / 4 次都变差 ▼ / 4 次都各有优势 ◆ 的题数，"翻转"为某次变好、另一次变差的题数。'
        '每格与同一检索文本的 α = 1 比较；"原句"指去编码的原句。一题在某次生成中有变化的组合同向即为该方向，否则为 ◆。'
        '绿：▲ 多于 ▼；红：▼ 多于 ▲。采纳规则：一档 α 在 6 列都是绿色才采纳。</p>' + table + '</div>'
        '<div class="card"><h3>题次（13 组合 × 32 题 × 4 次生成）</h3><p class="sub">每格 变好 / 变差 / 各有优势 的题次，供看幅度；不参与采纳规则。</p>'
        '<div class="scroll"><table><tr><th>α</th>' + "".join(f'<th class="num">{esc(n)}</th>' for _, n in TEXTS) + '</tr>'
        + combo_rows + '</table></div></div>'
        '<h2>逐题</h2>' + nav + panels)
    conclusion = (HERE / "conclusion.html").read_text(encoding="utf-8") if (HERE / "conclusion.html").exists() else ""
    write_assets(ROOT / "reports")
    (HERE / "index.html").write_text(page("BM25 权重实验", body.replace("{conclusion}", conclusion), root="../../",
                                          crumbs=[("报告", "../../index.html"), ("V3.14 对比页", PAGE)]), encoding="utf-8")
    print("passing", passing, "net", net, "chosen", chosen)


if __name__ == "__main__":
    main()
