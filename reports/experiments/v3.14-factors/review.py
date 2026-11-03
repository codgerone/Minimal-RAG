"""Evidence page for module 1 of the comparison page (module.py): the single-factor table, the per-question
verdicts in the four generations and experiments A–E (proposal v3.14, 2026-10-29).

Reads pairs.json, features.json and the experiments exp_*.json next to this script, and the v8 plans.
Writes index.html next to this script. Run: python reports/experiments/v3.14-factors/review.py
"""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from urllib.parse import quote

from rag.reports.html import esc, page, write_assets

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
PAIRS = json.loads((HERE / "pairs.json").read_text(encoding="utf-8"))
FEAT = json.loads((HERE / "features.json").read_text(encoding="utf-8"))
CODES = json.loads((HERE / "exp_codes.json").read_text(encoding="utf-8"))
CODES2 = json.loads((HERE / "exp_codes2.json").read_text(encoding="utf-8"))
CHAN = json.loads((HERE / "exp_channels.json").read_text(encoding="utf-8"))
NOISE = json.loads((HERE / "exp_noise.json").read_text(encoding="utf-8"))
STEP = json.loads((HERE / "exp_stepback.json").read_text(encoding="utf-8"))
STEP2 = json.loads((HERE / "exp_stepback2.json").read_text(encoding="utf-8"))
TERMS = json.loads((HERE / "exp_terms.json").read_text(encoding="utf-8"))
SUBTR = json.loads((HERE / "exp_subtrans.json").read_text(encoding="utf-8"))
SUBTR2 = json.loads((HERE / "exp_subtrans2.json").read_text(encoding="utf-8"))
PLANS = json.loads((HERE.parent / "v3.14-phrasings-v8/plans.json").read_text(encoding="utf-8"))
PAGE = "../../query/v3.14/index.html"
DOCS = {"d359": "合同 CONT2025001（西语）", "5a4c": "订单 PO 25-19（英语）", "169a": "订单 PO 24-12（英语）",
        "5883": "订单 PO 25-15（英语）", "817c": "发票 E001-602（西语）"}
SIGN = {"better": ("▲", "up"), "worse": ("▼", "down"), "mixed": ("◆", ""), "same": ("·", "same")}
CHAN_NAME = {"question": "原句", "rewrite": "改写", "translation": "翻译", "hyde": "HyDE", "step_back": "step-back"}


def comp(factor: str, context: str) -> dict:
    return next(c for c in PAIRS if c["factor"] == factor and c["context"] == context)


def link(c: dict, text: str = "对比页") -> str:
    return (f'<a href="{PAGE}#L={quote(c["left"], safe="")}&R={quote(c["right"], safe="")}&k=3" '
            f'target="_blank">{esc(text)}</a>')


def counts(c: dict) -> str:
    t = c["counts"]["0"]
    questions = sum(1 for cells in c["cells"].values() if {x["v"][0] for x in cells.values()} - {"same"})
    return (f'<span class="up">▲{t["better"]}</span> <span class="down">▼{t["worse"]}</span> ◆{t["mixed"]} '
            f'<span class="same">={t["same"]}</span>', questions)


def item_split(c: dict) -> str:
    """Item-level completion-rank moves (all combinations), by where the item's evidence is."""
    out = Counter()
    for case, combos in c["cells"].items():
        for i, tab in enumerate(FEAT[case]["table_items"]):
            for cell in combos.values():
                a, b = cell["l"][0][i], cell["r"][0][i]
                a, b = (99 if a is None else a), (99 if b is None else b)
                out[("表" if tab else "文", "↑" if b < a else "↓" if b > a else "=")] += 1
    return " ".join(f'{t}：<span class="up">↑{out[(t, "↑")]}</span> <span class="down">↓{out[(t, "↓")]}</span>'
                    for t in "表文")


def rk(x) -> str:
    return "∞" if x is None else str(x)


def ns(n) -> str:
    return "·".join(rk(x) for x in n)


def vec(n):
    return sorted(math.inf if x is None else x for x in n)


def ver(a, b) -> str:
    a, b = vec(a), vec(b)
    e, l = any(y < x for x, y in zip(a, b)), any(y > x for x, y in zip(a, b))
    return "mixed" if e and l else "better" if e else "worse" if l else "same"


def mark(v: str) -> str:
    s, cls = SIGN[v]
    return f'<span class="{cls}">{s}</span>'


def qv_retired(cells: dict, g: int) -> str:
    """The rule until 2026-11-02: the one direction every changed combination shares, otherwise mixed."""
    s = {x["v"][g] for x in cells.values()} - {"same"}
    return "same" if not s else s.pop() if len(s) == 1 else "mixed"


def qstab(c: dict) -> dict:
    """By the rule of 2026-11-02 (rules/evaluation.md "跨组合汇总"): every combination counts on its own and the
    counts are averaged over the combinations. Per generation the questions better / worse / mixed / same
    ("gens") and the net; stable (better, or worse, in all four generations) and flips (better in one, worse
    in another); per question and generation, how many combinations are better / worse / mixed ("counts");
    the questions better (worse) in every combination and generation ("all")."""
    cells = c["cells"]
    n = len(next(iter(cells.values())))
    gens = [{v: sum(x["v"][g] == v for cs in cells.values() for x in cs.values()) / n
             for v in ("better", "worse", "mixed", "same")} for g in range(4)]
    stable = {v: sum(all(x["v"][g] == v for g in range(4)) for cs in cells.values() for x in cs.values()) / n
              for v in ("better", "worse")}
    flips = sum("better" in x["v"] and "worse" in x["v"] for cs in cells.values() for x in cs.values()) / n
    counts = {q: [[sum(x["v"][g] == v for x in cs.values()) for v in ("better", "worse", "mixed")] for g in range(4)]
              for q, cs in cells.items()}
    every = {v: [q for q, cs in cells.items() if all(x["v"][g] == v for x in cs.values() for g in range(4))]
             for v in ("better", "worse")}
    return {"gens": gens, "net": [x["better"] - x["worse"] for x in gens], "stable": stable, "flips": flips,
            "counts": counts, "all": every}


def qstab_retired(c: dict) -> dict:
    """qstab by the rule until 2026-11-02 (qv_retired), as this evidence page was written."""
    per = {q: [qv_retired(cells, g) for g in range(4)] for q, cells in c["cells"].items()}
    return {"per": per,
            "gens": [{v: sum(1 for x in per.values() if x[g] == v) for v in ("better", "worse", "mixed")} for g in range(4)],
            "stable": {v: [q for q, x in per.items() if len(set(x)) == 1 and x[0] == v] for v in ("better", "worse", "mixed")},
            "flip": [q for q, x in per.items() if "better" in x and "worse" in x]}


def seq(vs) -> str:
    return "".join(mark(v) for v in vs)


def card(title: str, body: str, kind: str = "") -> str:
    return f'<div class="card {kind}"><h3>{title}</h3>{body}</div>'


# ---------------------------------------------------------------- conclusions

SIDE_FINDINGS = """
<ul>
<li><b>V3.13 的混合检索对中文问题等于向量检索</b>（实验 B）：32 题中只有题面含拉丁字母词（公司名、型号、西语条款名）的 15 题有 BM25 匹配。这类词也会误导：CONTRACT-105-Q011 只用向量时证据排 1、3、3，混合后排 13（"Ítem"匹配到别的块）。这是现有系统的特性，与本版措施无关，记入 roadmap 供以后处理。</li>
<li><b>英语所有格被切出一个 s</b>（实验 F）：分析器把 ELPU's 切成 elpu 与 s，s 不在停用词表里，出现在 49 个块中的 13 个，给这些块都加分。PO-25-19-Q006 去掉所有格后证据在 BM25 中 7→4，混合名次不变。属于现有分析器的问题，与本版措施无关，可记入 roadmap。</li>
<li><b>问号改句号</b>就使 9/32 题变好（▲32 ◆1，无变差）：bge-m3 对陈述句与文档更接近。改写的"写成陈述句"可能有一点真实作用，但被其余改动的噪声淹没（实验 C）。</li>
<li><b>13 种组合几乎总是同向</b>（一题要么 13 组合都变好，要么都变差）：效果由检索文本决定，与表格入库方式无关。因此有效样本是 32 道题，不是 416 个组合 × 题。</li>
</ul>"""


# ---------------------------------------------------------------- sections

def summary_table() -> str:
    rows = []
    layer_seen = None
    for c in PAIRS:
        if c["layer"] != layer_seen:
            layer_seen = c["layer"]
            rows.append(f'<tr><th colspan="5" style="background:var(--code)">{esc(layer_seen)}</th></tr>')
        cnt, q = counts(c)
        later = " ".join(f'<span class="up">{c["counts"][g]["better"]}</span>/<span class="down">{c["counts"][g]["worse"]}</span>'
                         for g in ("1", "2", "3"))
        s = qstab_retired(c)
        st = (f'<span class="up">▲{len(s["stable"]["better"])}</span> <span class="down">▼{len(s["stable"]["worse"])}</span> '
              f'◆{len(s["stable"]["mixed"])}' + (f' · 翻转 {len(s["flip"])}' if s["flip"] else ""))
        rows.append(f'<tr><td class="nw"><b>{esc(c["factor"])}</b></td><td>{esc(c["context"])}</td>'
                    f'<td class="nw">{cnt}</td><td class="num">{q}</td><td class="nw">{later}</td><td class="nw">{st}</td>'
                    f'<td class="nw">{item_split(c)} · {link(c)}</td></tr>')
    return ('<p class="sub">每行两侧只差一个因素（左 → 右）。题次 = 13 组合 × 32 题；"判定""变化题数""信息项名次"为第 0 次生成，'
            '"变化题数"为至少一个组合判定不是"不变"的题数；"第 1–3 次"为另三次独立生成的 ▲/▼ 题次；'
            '"4 次一致（题）"按题计：一题在某次生成中有变化的组合同向即为该方向，否则为 ◆，4 次相同才计入，"翻转"为某次变好、另一次变差的题数；'
            '信息项名次按信息项计（所有组合），分证据在表格 / 正文。</p><div class="scroll"><table><tr><th>因素</th><th>固定的条件</th>'
            '<th>判定（题次）</th><th class="num">变化题数</th><th>第 1–3 次</th><th>4 次一致（题）</th><th>信息项名次（表 / 文）</th></tr>'
            + "".join(rows) + '</table></div>')


STAB = [("P3.2.0", "原问题 · 原句", "去编码"), ("P3.2.1", "原问题 · 两侧带编码", "改写"),
        ("P3.2.2", "原问题 · 两侧去编码", "翻译"), ("P3.2.3", "原问题 · 两侧去编码", "HyDE"),
        ("P3.2.4", "原问题 · 两侧去编码", "step-back"), ("P3.1", "原句 · 带编码", "分解"),
        ("RRF 融合", "原问题 · 原句 → 原句 + 翻译", "原句+翻译"), ("RRF 融合", "原问题 · 原句 → 原句 + HyDE", "原句+HyDE"),
        ("P3.2.0", "原问题 · 翻译", "翻译去编码"), ("P3.2.0", "原问题 · HyDE", "HyDE 去编码")]


def stability() -> str:
    comps = [(comp(f, x), n) for f, x, n in STAB]
    stabs = [qstab_retired(c) for c, _ in comps]
    head = "".join(f'<th class="nw">{esc(n)}</th>' for _, n in comps)
    summary = "".join(
        f'<tr><td class="nw">{esc(n)}</td>'
        + "".join(f'<td class="num nw"><span class="up">{d["better"]}</span>/<span class="down">{d["worse"]}</span>/{d["mixed"]}</td>' for d in s["gens"])
        + f'<td class="nw"><span class="up">▲{len(s["stable"]["better"])}</span> <span class="down">▼{len(s["stable"]["worse"])}</span> ◆{len(s["stable"]["mixed"])}</td>'
        f'<td>{esc("、".join(s["flip"])) or "—"}</td></tr>' for (c, n), s in zip(comps, stabs))
    rows = []
    for case in comps[0][0]["cells"]:
        cells = []
        for s in stabs:
            vs = s["per"][case]
            bg = ("" if set(vs) == {"same"} else "var(--ok-bg)" if set(vs) == {"better"} else
                  "var(--bad-bg)" if set(vs) == {"worse"} else "var(--warn-bg)")
            cells.append(f'<td class="nw" style="background:{bg}">{seq(vs)}</td>')
        rows.append(f'<tr><td class="nw">{esc(case)}</td>{"".join(cells)}</tr>')
    return ('<p class="sub">按题计的判定在 4 次独立生成（0–3）中的变化。一题在某次生成中有变化的组合同向即为该方向，否则为 ◆。'
            '去编码作用于原句时不经过 LLM，4 次相同是必然的。</p>'
            '<div class="scroll"><table><tr><th>措施</th><th class="num">第 0 次 ▲/▼/◆</th><th class="num">第 1 次</th>'
            '<th class="num">第 2 次</th><th class="num">第 3 次</th><th>4 次一致（题）</th><th>翻转的题</th></tr>' + summary + '</table></div>'
            '<p class="sub">逐题：每格 4 个符号依次为第 0–3 次生成；4 次都变好标绿、都变差标红，其余有变化标黄。</p>'
            f'<div class="scroll"><table><tr><th>题</th>{head}</tr>' + "".join(rows) + '</table></div>')


HEAT = [("P3.2.0", "原问题 · 原句", "去编码"), ("P3.2.1", "原问题 · 两侧去编码", "改写"),
        ("P3.2.2", "原问题 · 两侧去编码", "翻译"), ("P3.2.3", "原问题 · 两侧去编码", "HyDE"),
        ("P3.2.4", "原问题 · 两侧去编码", "step-back"), ("P3.1", "原句 · 带编码", "分解"),
        ("P3.2.2", "子问题（不合并） · 两侧去编码", "子问题翻译"), ("P3.2.3", "子问题（不合并） · 两侧去编码", "子问题 HyDE")]


def heat() -> str:
    comps = [comp(f, x) for f, x, _ in HEAT]
    head = "".join(f'<th class="num">{esc(n)}</th>' for *_, n in HEAT)
    rows = []
    for case in comps[0]["cells"]:
        f = FEAT[case]
        tab = sum(f["table_items"])
        cells = []
        for c in comps:
            vs = [x["v"][0] for x in c["cells"][case].values()]
            b, w, m = vs.count("better"), vs.count("worse"), vs.count("mixed")
            bg = ("var(--ok-bg)" if b >= 7 else "var(--bad-bg)" if w >= 7 else "var(--warn-bg)" if b + w + m else "")
            txt = " ".join(x for x in (f"▲{b}" if b else "", f"▼{w}" if w else "", f"◆{m}" if m else "") if x) or "·"
            cells.append(f'<td class="num nw" style="background:{bg}">{txt}</td>')
        rows.append(f'<tr><td class="nw">{esc(case)}</td><td class="nw">{esc(DOCS[f["document"][:4]].split("（")[0])}</td>'
                    f'<td class="nw">{esc(f["type"])}</td><td class="num">{tab}/{f["items"]}</td>{"".join(cells)}</tr>')
    return ('<p class="sub">每格是 13 个组合中变好 ▲ / 变差 ▼ / 各有优势 ◆ 的组合数；≥7 个组合变好标绿、变差标红，其余有变化标黄。'
            '"表格项"为证据在表格中的信息项数 / 信息项数。</p><div class="scroll"><table><tr><th>题</th><th>文档</th><th>题型</th>'
            f'<th class="num">表格项</th>{head}</tr>' + "".join(rows) + '</table></div>')


def exp_codes() -> str:
    names = ["原样", "删去", "换数字", "保留年份", "只有字母", "无意义词"]
    rows = []
    for case, d in CODES.items():
        base = d["原样"]["ranks"]["RM-T"]
        cells = "".join(f'<td class="num nw">{mark(ver(list(base.values()), list(d[n]["ranks"]["RM-T"].values())))} '
                        f'{ns(d[n]["ranks"]["RM-T"].values())}</td>' for n in names)
        rows.append(f'<tr><td class="nw">{esc(case)}</td>{cells}</tr>')
    ex = CODES["CONTRACT-105-Q010"]
    sample = "；".join(f"{n}：{esc(ex[n]['text'][:28])}…" for n in names)
    return ('<p>只改动题面中的编码，其余一字不动；每格为 RM-T 下各信息项的凑齐名次，符号是相对"原样"的判定（RM-C、LM-C 结论相同，见 exp_codes.json）。'
            f'前 4 题删去编码后变差，后 5 题变好。</p><p class="sub">例（CONTRACT-105-Q010）：{sample}</p>'
            '<div class="scroll"><table><tr><th>题</th>' + "".join(f'<th class="num">{n}</th>' for n in names)
            + '</tr>' + "".join(rows) + '</table></div>'
            '<p><b>读法</b>：后 5 题中合同 4 题换成别的数字也变好 → 有害的是真实编码这串数字（发票题换数字不变好、换成无意义词才变好，原因未验证）；'
            'PO-25-19-Q005 换数字与原样相同、只剩字母与删去相同 → 有利的是"带数字"这一形式；其余 3 道表格题结论不一致。'
            '"无意义词"在 9 题上都不差于删去，但这不是可用的做法（用什么词替换没有依据）。</p>'
            + exp_codes2())


def exp_codes2() -> str:
    names = ["原样", "删去", "换数字", "保留年份", "只有字母", "无意义词"]
    combos = list(next(iter(CODES2.values()))["原样"]["ranks"])
    rows = []
    for case, d in CODES2.items():
        for i, combo in enumerate(combos):
            base = d["原样"]["ranks"][combo]
            cells = "".join(f'<td class="num nw">{mark(ver(base, d[n]["ranks"][combo]))} {ns(d[n]["ranks"][combo])}</td>' for n in names)
            rows.append(f'<tr>{f"<td rowspan={len(combos)} class=nw>{esc(case)}</td>" if i == 0 else ""}<td class="nw">{combo}</td>{cells}</tr>')
    return ('<h3>第二部分：其余去编码后变差的题</h3>'
            '<p>上面 3 个组合看不到这些题的变化（它们只在带引导句、摘要的入库方式上变差），所以在变差的组合上重做（<code>exp_codes2.py</code>）。</p>'
            '<div class="scroll"><table><tr><th>题</th><th>组合</th>' + "".join(f'<th class="num">{n}</th>' for n in names)
            + '</tr>' + "".join(rows) + '</table></div>'
            '<p><b>读法</b>：CONTRACT-105-Q014、PO-25-19-Q001、PO-25-19-Q003（LL-C 除外）换成别的数字或保留年份时接近原样、只剩字母时接近删去 → 与 PO-25-19-Q005 相同，有利的是"带数字"。'
            'PO-25-19-Q002、PO-25-19-Q007、PO-24-12-Q004 用任何内容占住编码的位置（含无意义词）都接近原样，只有删去（以及部分组合上的只剩字母）变差 → 不是数字的作用，'
            '是删去一个词改变了句子，属于向量对措辞的敏感（与实验 C 同类）。PO-25-19-Q009 在这些组合上删去反而变好（只剩字母最好），与合同正文题同向。</p>')


def exp_channels(kind: str) -> str:
    rows = []
    for case, d in CHAN.items():
        q = d["question"]["hybrid"]
        k = d[kind]
        vs = {combo: (ver(q[combo]["n"], k["semantic"][combo]["n"]), ver(q[combo]["n"], k["hybrid"][combo]["n"]))
              for combo in ("RM-T", "RM-C")}
        if all(v == ("same", "same") for v in vs.values()):
            continue
        rows.append(f'<tr><td class="nw">{esc(case)}</td><td class="num nw">{ns(q["RM-T"]["n"])}</td>'
                    f'<td class="num nw">{mark(vs["RM-T"][0])} {ns(k["semantic"]["RM-T"]["n"])}</td>'
                    f'<td class="num nw">{ns(k["bm25"]["RM-T"]["n"])}</td>'
                    f'<td class="num nw">{mark(vs["RM-T"][1])} {ns(k["hybrid"]["RM-T"]["n"])}</td>'
                    f'<td class="num nw">{mark(vs["RM-C"][0])} / {mark(vs["RM-C"][1])}</td></tr>')
    return (f'<p>同一条{CHAN_NAME[kind]}文本分别只用向量、只用 BM25、混合检索（两侧去编码，不分解）。基准是原句的混合检索（中文原句几乎没有 BM25 匹配，等于向量）。'
            '每格为各信息项凑齐名次，符号是相对原句的判定。</p><div class="scroll"><table><tr><th>题</th><th class="num">原句（混合）</th>'
            f'<th class="num">{CHAN_NAME[kind]} · 只用向量</th><th class="num">· 只用 BM25</th><th class="num">· 混合</th>'
            '<th class="num">RM-C 向量 / 混合</th></tr>' + "".join(rows) + '</table></div>')


def exp_noise() -> str:
    edits = ["问号改句号", "前加请问", "句子倒序"]
    rows = []
    for e in edits:
        c = Counter(v for d in NOISE.values() if e in d for v in d[e]["v"].values())
        n = sum(1 for d in NOISE.values() if e in d)
        changed = sum(1 for d in NOISE.values() if e in d and set(d[e]["v"].values()) - {"same"})
        rows.append(f'<tr><td>机械改动：{esc(e)}</td><td class="num">{changed} / {n}</td><td class="nw">'
                    f'<span class="up">▲{c["better"]}</span> <span class="down">▼{c["worse"]}</span> ◆{c["mixed"]}</td></tr>')
    combos = ("RM-T", "RM-C", "LM-C", "LL-T")
    for f, x, name in [("P3.2.1", "原问题 · 两侧带编码", "改写"), ("P3.2.2", "原问题 · 两侧带编码", "翻译"),
                       ("P3.2.3", "原问题 · 两侧带编码", "HyDE"), ("P3.2.4", "原问题 · 两侧带编码", "step-back"),
                       ("P3.2.0", "原问题 · 原句", "去编码")]:
        cmp_ = comp(f, x)
        c = Counter(cell["v"][0] for case in cmp_["cells"].values() for k, cell in case.items() if k in combos)
        changed = sum(1 for case in cmp_["cells"].values() if {case[k]["v"][0] for k in combos} - {"same"})
        rows.append(f'<tr><td><b>措施：{esc(name)}</b></td><td class="num">{changed} / 32</td><td class="nw">'
                    f'<span class="up">▲{c["better"]}</span> <span class="down">▼{c["worse"]}</span> ◆{c["mixed"]}</td></tr>')
    return ('<p>对原问题做只改形式、不改意思的机械改动（由代码完成，不调用 LLM），与各措施在相同的 4 个组合（RM-T、RM-C、LM-C、LL-T）上比较。'
            '"句子倒序"只适用于含多句的 23 题。判定为题次（4 组合 × 题）。</p><div class="scroll"><table><tr><th>改动</th>'
            '<th class="num">判定有变化的题</th><th>判定（题次）</th></tr>' + "".join(rows) + '</table></div>'
            '<p><b>读法</b>：按总量，改写变化的题数落在这个范围内；但逐题看，改写在 4 次生成中都变差的题（PO-24-12-Q003、Q005、PO-25-19-Q006 等）'
            '在这些机械改动下判定都不变——它们对"改成名词短语"这种改法敏感，而不是对任意改动敏感。所以本实验只能说明改写的总量与措辞噪声同量级，'
            '不能把单题的变差归为随机噪声。</p>')


def exp_step() -> str:
    rows = []
    for case, d in STEP.items():
        h = d["n"]["hybrid"]
        s = d["n"]["semantic"]
        rows.append(f'<tr><td class="nw">{esc(case)}</td><td>{esc("、".join(d["texts"]["dropped"]))}</td>'
                    f'<td class="num nw">{ns(h["原句"]["RM-T"])}</td>'
                    f'<td class="num nw">{mark(ver(h["原句"]["RM-T"], h["step-back"]["RM-T"]))} {ns(h["step-back"]["RM-T"])}</td>'
                    f'<td class="num nw">{mark(ver(h["原句"]["RM-T"], h["补回"]["RM-T"]))} {ns(h["补回"]["RM-T"])}</td>'
                    f'<td class="num nw">{ns(s["step-back"]["RM-T"])} → {ns(s["补回"]["RM-T"])}</td></tr>')
    sample = STEP["PO-25-19-Q002"]["texts"]
    return ('<p>step-back 文本去掉了题面中的拉丁字母词（公司、型号、西语条款名）的题：把去掉的词原样加在括号里补回，其余不动（两侧去编码，RM-T）。</p>'
            f'<p class="sub">例（PO-25-19-Q002）：step-back「{esc(sample["step-back"])}」→ 补回「{esc(sample["补回"])}」</p>'
            '<div class="scroll"><table><tr><th>题</th><th>被去掉的词</th><th class="num">原句（混合）</th><th class="num">step-back（混合）</th>'
            '<th class="num">补回（混合）</th><th class="num">只用向量：step-back → 补回</th></tr>' + "".join(rows) + '</table></div>'
            + exp_step2())


def exp_step2() -> str:
    combos = list(next(iter(STEP2.values()))["n"]["原句"])
    rows = "".join(
        f'<tr><td class="nw">{esc(case)}</td><td>{esc("、".join(d["texts"]["dropped"]))}</td>'
        + "".join(f'<td class="num nw">{" / ".join(ns(d["n"][n][c]) for c in combos)}</td>' for n in ("原句", "step-back", "补回")) + '</tr>'
        for case, d in STEP2.items())
    return ('<h3>第二部分：去掉的中文限定</h3>'
            '<p>没有拉丁字母词的题，step-back 去掉的是中文限定（哪一批、哪家公司、哪种担保）。把去掉的限定原样加在括号里补回（<code>exp_stepback2.py</code>，限定由人从题面读出，是本实验的输入）。'
            f'每格依次为 {" / ".join(combos)} 的凑齐名次（混合检索，两侧去编码）。</p>'
            '<div class="scroll"><table><tr><th>题</th><th>补回的限定</th><th class="num">原句</th><th class="num">step-back</th><th class="num">补回</th></tr>'
            + rows + '</table></div>'
            '<p><b>读法</b>：PO-25-19-Q009、CONTRACT-105-Q014 补回后恢复到原句，PO-25-19-Q005 补回后好于原句，CONTRACT-105-Q010 部分恢复；'
            'PO-25-15-Q001/Q002 补回"第四批次""第六批次"不恢复——表格写的是"4th Delivery""6th Delivery"，中文批次词本来就匹配不到（与实验 E 同一原因），'
            'step-back 的损失在别处，' '<span class="down">未验证</span>。</p>')


def exp_terms() -> str:
    rows = []
    for case, d in TERMS.items():
        cells = "".join(f'<td class="num nw">{ns(d["n"][r]["译文"]["RM-T"])} → '
                        f'{mark(ver(d["n"][r]["译文"]["RM-T"], d["n"][r]["换成文档用词"]["RM-T"]))} {ns(d["n"][r]["换成文档用词"]["RM-T"])}</td>'
                        for r in ("semantic", "bm25", "hybrid"))
        base = CHAN[case]["question"]["hybrid"]["RM-T"]["n"]
        rows.append(f'<tr><td class="nw">{esc(case)}</td><td>{esc(d["phrase"])} → {esc(d["label"])}</td>'
                    f'<td class="num nw">{ns(base)}</td>{cells}</tr>')
    return ('<p>翻译变差的订单题：译文只把批次的说法换成文档表格的标签，其余一字不动（去编码，RM-T；RM-C 相同，见 exp_terms.json）。每格为 译文 → 换成文档用词。</p>'
            '<div class="scroll"><table><tr><th>题</th><th>替换</th><th class="num">中文原句（混合）</th><th class="num">只用向量</th>'
            '<th class="num">只用 BM25</th><th class="num">混合</th></tr>' + "".join(rows) + '</table></div>'
            '<p>向量通道对这处用词不敏感，差别全在 BM25：词汇匹配要求用词与文档一致。</p>')


def exp_subtrans() -> str:
    rows = []
    for case, d in SUBTR.items():
        for item, subs in d["mapping"].items():
            for sq in subs:
                r = d["subquestions"][sq]["ranks"]
                a = [r["question"][ret]["RM-T"][item]["n"] for ret in ("semantic", "bm25", "hybrid")]
                b = [r["translation"][ret]["RM-T"][item]["n"] for ret in ("semantic", "bm25", "hybrid")]
                if a[2] == b[2]:
                    continue
                t = d["subquestions"][sq]["texts"]
                rows.append(f'<tr><td class="nw">{esc(case)}</td><td class="nw">{item} ← {sq}</td>'
                            f'<td>{esc(t["question"])}<br><span class="meta">{esc(t["translation"])}</span></td>'
                            f'<td class="num nw">{" / ".join(rk(x) for x in a)}</td>'
                            f'<td class="num nw">{mark(ver([a[2]], [b[2]]))} {" / ".join(rk(x) for x in b)}</td></tr>')
    tests = []
    for v in SUBTR2:
        names = list(v["texts"])
        cells = "".join(f'<td class="num nw">{" / ".join(rk(v["n"][ret][n][c]["n"]) for ret in ("semantic", "bm25", "hybrid"))}</td>'
                        for n in names for c in ("RM-T",))
        tests.append(f'<tr><td class="nw">{esc(v["case"])}</td><td class="nw">{v["item"]} ← {v["sub"]}</td><td>{esc(v["name"])}：'
                     f'{esc(v["old"])} → {esc(v["new"])}</td>{cells}</tr>')
    return ('<p>对照"P3.2.2 · 子问题（不合并） · 两侧去编码"：分解后不合并，每个信息项看标注给它的子问题；左侧中文子问题、右侧它的译文，都去编码（<code>exp_subtrans.py</code>，第 0 次生成）。'
            '下表列出 RM-T 上混合名次有变化的信息项，每格为 向量 / BM25 / 混合 的凑齐名次，符号是混合名次的变化。中文子问题只有含拉丁字母词时才有 BM25 匹配。</p>'
            '<div class="scroll"><table><tr><th>题</th><th>信息项 ← 子问题</th><th>中文子问题 / 译文</th><th class="num">中文：向量 / BM25 / 混合</th>'
            '<th class="num">译文</th></tr>' + "".join(rows) + '</table></div>'
            '<h3>第二部分：只改一处的对照</h3>'
            '<p>先用 BM25 逐词得分（<code>bm25_explain.py</code>，与索引相同的公式）找出把正文块排到证据前面的词，再只改译文中的一处（<code>exp_subtrans2.py</code>，RM-T；LM+-T 见 exp_subtrans2.json）。'
            '每格为 向量 / BM25 / 混合。</p>'
            '<div class="scroll"><table><tr><th>题</th><th>信息项</th><th>改动</th><th class="num">中文子问题</th><th class="num">译文</th>'
            '<th class="num">改后</th></tr>' + "".join(tests) + '</table></div>'
            '<p><b>读法</b>：换成文档用词（goods、Qty、IGV）或补回所指（MDC ORCA）后都恢复到中文子问题的水平；ítems 改回单数部分恢复（混合 7→4）。'
            'PO-24-12-Q001 "去掉误匹配的通用词"（删去 in order）使向量也变了（3→1），不能单独说明 BM25 的作用，只作参考；BM25 的作用由逐词得分直接看出：'
            '译文唯一能匹配的词是 order，只出现在正文块里。CONTRACT-105-Q001 换成条款标题 otorgantes <b>没有恢复</b>（BM25 12→19）：该词只在 c0000，证据 c0001 本身不含问题的词。'
            '去掉所有格只改善 BM25，混合不变。</p>')


def v8_check() -> str:
    rows = []
    for g, cases in PLANS["generations"].items():
        c = Counter()
        for pp in cases.values():
            for mode in ("plain", "decomposed"):
                for q in pp[mode]["questions"]:
                    c[("原句", q["stripped"] > 0)] += 1
                    for x in q["phrasings"]:
                        if x["kind"] != "question":
                            c[(CHAN_NAME[x["kind"]], x["stripped"] > 0)] += 1
        cells = "".join(f'<td class="num">{c[(k, True)]} / {c[(k, True)] + c[(k, False)]}</td>'
                        for k in ("原句", "改写", "翻译", "HyDE", "step-back"))
        rows.append(f'<tr><td>第 {g} 次</td>{cells}</tr>')
    sample = PLANS["generations"]["0"]["CONTRACT-105-Q003"]["plain"]["questions"][0]["phrasings"]
    ex = "".join(f'<tr><td class="nw">{esc(CHAN_NAME[x["kind"]])}</td><td>{esc(x["text"])}</td></tr>' for x in sample)
    return ('<p>写法提示词 v8（只删去"不要写入编码"一行）。表中为含编码（P3.2.0 能删去）的文本数 / 文本总数。'
            f'改写、翻译、HyDE 全部保留了编码；step-back 约 40% 不含编码（按定义去掉限定条件），这些条目开不开 P3.2.0 结果相同。没有规划降级；4 次生成费用 ${PLANS["usd"]}。</p>'
            '<div class="scroll"><table><tr><th>生成</th><th class="num">原句</th><th class="num">改写</th><th class="num">翻译</th>'
            '<th class="num">HyDE</th><th class="num">step-back</th></tr>' + "".join(rows) + '</table></div>'
            '<details><summary>示例：CONTRACT-105-Q003 第 0 次生成</summary><table>' + ex + '</table></details>')


def main() -> None:
    tabs = [("summary", "单因素总表", summary_table()), ("heat", "逐题 × 措施", heat()),
            ("stab", "4 次生成稳定性", stability()),
            ("codes", "实验 A：编码替换", exp_codes()),
            ("chan-tr", "实验 B：翻译的通道拆分", exp_channels("translation")),
            ("chan-hy", "实验 B：HyDE 的通道拆分", exp_channels("hyde")),
            ("noise", "实验 C：措辞噪声基线", exp_noise()),
            ("step", "实验 D：step-back 补回", exp_step()),
            ("terms", "实验 E：译文换成文档用词", exp_terms()),
            ("subtr", "实验 F：子问题上的翻译", exp_subtrans()),
            ("v8", "v8 写法核对", v8_check())]
    nav = '<div class="tabs">' + "".join(f'<a href="#{t}" data-tab="{t}">{esc(n)}</a>' for t, n, _ in tabs) + '</div>'
    panels = "".join(f'<div data-panel="{t}" class="card">{body}</div>' for t, _, body in tabs)
    body = (
        '<h1>查询侧各措施单独效果的依据</h1>'
        '<div class="sub">V3.14 · 2026-10-29 · 写法提示词 v8 · 第 0–3 次独立生成 · 13 组合 × 32 题 · 判定为凑齐向量的支配关系（提案问题 3）</div>'
        + card('怎么读', '<p>每个措施只和"只差它一个因素"的配置比较：一个问题只用一条检索文本（不融合），分解时不合并（每个信息项看它对应的子问题）。'
               '融合、合并单列为"组合方式"。总表的数字默认取第 0 次生成，另列第 1–3 次与 4 次一致的题数；逐题见"4 次生成稳定性"标签。</p>'
               '<p class="sub"><b>统计口径</b>：本页按 2026-11-02 之前的口径（一题在一次生成中，有变化的组合同向才取该方向，否则为 ◆），是 V3.14 各项实验的原始记录。'
               '2026-11-02 起改为按 13 组合取平均，现行数字见对比页模块 1 与决策 015 补充。</p>')
        + card('结论', f'<p>各措施的结论（建议、单独效果、逐题的变差原因、适合的题、随机性、是不是措施本身的问题）写在'
               f'<a href="{PAGE}#m-analysis">V3.14 对比页的模块 1</a>（<code>module.py</code> 生成）。本页是它引用的依据：单因素总表、逐题判定与实验 A–F。</p>')
        + '<h2>依据</h2>' + nav + panels)
    write_assets(ROOT / "reports")
    (HERE / "index.html").write_text(page("查询侧措施的单独效果", body, root="../../",
                                          crumbs=[("报告", "../../index.html"), ("V3.14 对比页", PAGE)]),
                                     encoding="utf-8")
    print("index.html")


if __name__ == "__main__":
    main()
