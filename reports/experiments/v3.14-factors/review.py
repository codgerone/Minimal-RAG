"""Review page: what each query-side measure does on its own, why some questions get worse or mixed, and
which questions suit it (proposal v3.14, 2026-10-29). Conclusions first; every cause names its
experiment, or "未验证".

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
CHAN = json.loads((HERE / "exp_channels.json").read_text(encoding="utf-8"))
NOISE = json.loads((HERE / "exp_noise.json").read_text(encoding="utf-8"))
STEP = json.loads((HERE / "exp_stepback.json").read_text(encoding="utf-8"))
TERMS = json.loads((HERE / "exp_terms.json").read_text(encoding="utf-8"))
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


def card(title: str, body: str, kind: str = "") -> str:
    return f'<div class="card {kind}"><h3>{title}</h3>{body}</div>'


# ---------------------------------------------------------------- conclusions

CONCLUSIONS = [
    ("P3.2.0 去编码", "建议固定启用", "ok", """
<p><b>作用</b>：只删去文档过滤识别出的标识编码，不调用 LLM、没有随机性（同一输入结果恒定）。原问题上 13 组合 × 32 题：▲82 ▼51 ◆1；
按信息项：<b>证据在正文的信息项从未变差（↑134 ↓0）</b>，变差全部发生在证据在表格的信息项（↑60 ↓78），且多为 1–2 名。</p>
<p><b>原因（已验证，实验 A）</b>：编码是用户分配的字符串，不出现在文档里，BM25 匹配不到，作用只经过向量。把编码换成别的写法对照：
① 合同类正文题（Q001、Q003、Q004、Q007）只要换掉真实编码的数字（换成其他数字、只剩字母、无意义词）就和删去一样变好——真实编码的数字串（含 2025）把向量拉向含同样数字的块（如保函编号 3012025041885 的 c0003）；
② 表格题只在 PO-25-19-Q005 上得到干净的结论：换成别的数字、保留年份都与原样相同（e2 第 4），只剩字母与删去相同（第 6）——带数字的编码让向量偏向数字密集的表格。
CONTRACT-105-Q010 换数字反而更好、只剩字母则一项变好一项变差；PO-25-19-Q001、Q003 在实验的 3 个组合上删去几乎不变（变差出现在其他组合，各 1–2 名）。表格题变差的机制只在 1 题上验证，其余<b>未验证</b>。</p>
<p><b>是不是措施本身的问题</b>：表格信息项的小幅变差是"去掉了一个碰巧有利的噪声"，不是去编码引入的新错误；正文信息项的改善是去掉了有害的噪声。两者都来自编码本身不是文档内容。</p>
<p><b>适用条件</b>：编码按规则由用户分配、不出现在文档中（<code>rules/document-filter.md</code>）。若用户登记了文档里真实出现的编号，删去会丢掉一个 BM25 可匹配的词——本数据中没有这种编码，<b>未验证</b>。</p>"""),
    ("P3.2.2 翻译", "建议启用，但只与原句 RRF 融合使用", "ok", """
<p><b>关键事实（已验证，实验 B）</b>：中文问题在英语、西语文档上几乎没有 BM25 匹配（只匹配题面里的拉丁字母词），<b>V3.13 的"混合检索"对中文问题实际上就是向量检索</b>。翻译成文档语言后，BM25 通道才真正起作用。所以翻译的效果 = 向量通道的语言变化 + 新加入的 BM25 通道。</p>
<p><b>单独使用</b>（两侧去编码）：▲130 ▼83 ◆16；按信息项 表 ↑104 ↓127、文 ↑132 ↓39。通道拆分（RM-T、RM-C）：只看向量时 表 ↑21 ↓14、文 ↑12 ↓18；加上 BM25 后 表 ↑15 ↓21、文 ↑20 ↓6。
<b>BM25 通道帮正文、伤表格</b>：表格单元以数字、型号为主，问题里的通用词（delivery、date、amount、entrega、fecha）在说明性正文里更多。</p>
<p><b>变差的题</b>：PO-25-15-Q001/Q002（条件说明段 c0002、c0004 含 delivery 等词，BM25 排第 1–2，表格 1→3）、CONTRACT-105-Q010（BM25 把价格条款 c0010 等提前，证据在 BM25 排 10、20）、PO-25-19-Q007（向量变好 3→2，BM25 排 10，混合后 6）、PO-25-19-Q001 e5、E001-602-Q001（两个通道都把发票抬头 c0000 排到第 1，证据 1→2；抬头为什么胜出<b>未验证</b>）。
变差题的译文逐条看过，都忠实于原题，没有误译；但译文用通用说法（"fourth delivery batch""Batch 1"），文档表格写的是"4th Delivery""1st Delivery"。
<b>只把这个短语换成文档用词（已验证，实验 E）</b>：BM25 中证据名次 PO-25-19-Q007 10→2、PO-25-15-Q001/Q002 5→3；混合检索 Q007 6→2（好于中文原句的 3），PO-25-15 两题 3→2/1 但 e2 仍为 4（说明性正文的干扰仍在）。
所以变差有两个来源：① 加入词汇匹配通道后说明性正文胜过表格——固有代价；② 翻译不看文档，写不出文档特有的标签——同样是"只凭问题翻译"的固有局限，提示词无法通用地解决（"用该语言文档的术语"已写在提示词里）。</p>
<p><b>题目特性</b>：变好集中在证据为正文条款的题（合同 Q003、Q011–Q014，PO 24-12 的说明类题）；变差集中在证据全在表格、且文档有大量说明性正文的题（PO 25-15、PO 25-19、发票）。检索前无法知道证据在不在表格，<b>没有可在查询时使用的路由规则</b>。</p>
<p><b>怎么启用</b>：与原句 RRF 融合时 BM25 通道只占一半权重：原句 → 原句 + 翻译 ▲147 ▼58（单独替换原句时 ▲150 ▼83），损失明显减少而收益基本保留。</p>"""),
    ("P3.2.3 HyDE", "可与翻译同样以 RRF 融合使用；单独使用不建议", "warn", """
<p>HyDE 写的是文档语言的假想答案，与翻译同样打开 BM25 通道，规律相同但对表格更不利：单独使用（两侧去编码）▲109 ▼137 ◆23；按信息项 表 ↑80 ↓202、文 ↑121 ↓73。
通道拆分：只看向量 表 ↑13 ↓18、文 ↑8 ↓20；加上 BM25 后 表 ↑9 ↓37、文 ↑22 ↓10（已验证，实验 B）。假想段落是叙述句，形似正文条款而不像表格行（<b>未验证</b>）。</p>
<p>与原句 RRF 融合：▲162 ▼88，净收益为各措施中最大；在子问题上融合则 ▲101 ▼136，不再有利。与翻译叠加的效果需扰动测试确认（见页底"未完成"）。</p>"""),
    ("P3.2.1 改写", "建议不采用", "bad", """
<p>改写仍是中文，不改变 BM25 通道。原问题上（两侧带编码）13 组合中 4 种看到的题：15/32 题有变化，▲24 ▼23，正负相当。</p>
<p><b>与措辞噪声无法区分（已验证，实验 C）</b>：不改意思的机械改动（问号改句号、句子倒序）就会使 9/32、10/23 题的判定改变；"句子倒序"▲17 ▼8 ◆6。改写变化的题数与方向都落在这个范围内。
它的变化是向量对措辞的敏感，不是改写本身有作用；额外花一次 LLM 调用没有可确认的收益。</p>"""),
    ("P3.2.4 step-back", "建议不采用", "bad", """
<p>单独使用（两侧去编码）▲43 ▼183 ◆34，在所有措施中最差；向量通道 表 ↑2 ↓43、文 ↑4 ↓38。</p>
<p><b>原因（已验证，实验 D）</b>：step-back 按定义去掉把问题限定到单个实例的条件，而这些条件正是题面里的公司名、型号和西语条款名（ELUC、HXE34K-S1、Garantía Técnico-Comercial）；对中文问题它们还是仅有的 BM25 锚点。
把被去掉的词原样补回（其余不动），混合检索下 9 道变差题中 6 道完全恢复到原句水平、CONTRACT-105-Q002 大部分恢复；PO-25-19-Q001、Q003 不恢复（它们的 step-back 还改写了"交货计划"的问法，原因<b>未验证</b>）。<b>这是措施本身的固有缺陷</b>：它要去掉的恰是检索最需要的词。</p>
<p>它变好的题：CONTRACT-105-Q011 的提升来自去掉"Ítem"——这个词在 BM25 中匹配到错误的块（补回后又退到 12 名），属于碰巧；CONTRACT-105-Q012、PO-25-19-Q007 的答案在通用条款里（按批次统一规定），去掉具体批次反而贴近原文。这类题检索前无法识别。</p>"""),
    ("P3.1 分解", "维持上次结论：条件启用候选（V3.15 补救）", "warn", """
<p>检索层（不合并，每个信息项在对应子问题中）：▲111 ▼38 ◆39；按信息项 表 ↑139 ↓81、文 ↑104 ↓26，对表格信息项尤其有利。去编码后 ▲106 ▼53 ◆22。
变差的原因上次已逐条做过对照实验（<code>reports/experiments/v3.14-decomposition-diagnosis/</code>）：A 答案需计算或推断、不是拆解导致；B 证据单元的表示问题；C 拆解丢了所指或限定条件（真正的拆解缺陷，补回后不差于原问题）；D 第二跳依赖第一跳。</p>
<p>分解的主要损失在合并层：不合并 → 轮流取 ▲35 ▼101、→ 全局 RRF ▲32 ▼117（每个子问题的第 1 名必占一位、隐含前提拆成多问被稀释，上次已验证）。</p>"""),
]

SIDE_FINDINGS = """
<ul>
<li><b>V3.13 的混合检索对中文问题等于向量检索</b>（实验 B）：32 题中只有题面含拉丁字母词（公司名、型号、西语条款名）的 15 题有 BM25 匹配。这类词也会误导：CONTRACT-105-Q011 只用向量时证据排 1、3、3，混合后排 13（"Ítem"匹配到别的块）。这是现有系统的特性，与本版措施无关，记入 roadmap 供以后处理。</li>
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
        rows.append(f'<tr><td class="nw"><b>{esc(c["factor"])}</b></td><td>{esc(c["context"])}</td>'
                    f'<td class="nw">{cnt}</td><td class="num">{q}</td><td class="nw">{item_split(c)} · {link(c)}</td></tr>')
    return ('<p class="sub">每行两侧只差一个因素（左 → 右）。题次 = 13 组合 × 32 题，第 0 次生成；"变化题数"为至少一个组合判定不是"不变"的题数；'
            '最后一列按信息项计（所有组合），分证据在表格 / 正文。</p><div class="scroll"><table><tr><th>因素</th><th>固定的条件</th>'
            '<th>判定（题次）</th><th class="num">变化题数</th><th>信息项名次（表 / 文）</th></tr>' + "".join(rows) + '</table></div>')


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
            '"无意义词"在 9 题上都不差于删去，但这不是可用的做法（用什么词替换没有依据）。</p>')


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
            '<th class="num">判定有变化的题</th><th>判定（题次）</th></tr>' + "".join(rows) + '</table></div>')


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
            '<th class="num">补回（混合）</th><th class="num">只用向量：step-back → 补回</th></tr>' + "".join(rows) + '</table></div>')


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
    concl = "".join(card(f'{esc(name)} <span class="badge {kind}">{esc(cat)}</span>', body) for name, cat, kind, body in CONCLUSIONS)
    tabs = [("summary", "单因素总表", summary_table()), ("heat", "逐题 × 措施", heat()),
            ("codes", "实验 A：编码替换", exp_codes()),
            ("chan-tr", "实验 B：翻译的通道拆分", exp_channels("translation")),
            ("chan-hy", "实验 B：HyDE 的通道拆分", exp_channels("hyde")),
            ("noise", "实验 C：措辞噪声基线", exp_noise()),
            ("step", "实验 D：step-back 补回", exp_step()),
            ("terms", "实验 E：译文换成文档用词", exp_terms()),
            ("v8", "v8 写法核对", v8_check())]
    nav = '<div class="tabs">' + "".join(f'<a href="#{t}" data-tab="{t}">{esc(n)}</a>' for t, n, _ in tabs) + '</div>'
    panels = "".join(f'<div data-panel="{t}" class="card">{body}</div>' for t, _, body in tabs)
    body = (
        '<h1>查询侧各措施的单独效果与变差原因</h1>'
        '<div class="sub">V3.14 · 2026-10-29 · 写法提示词 v8 · 第 0 次生成 · 13 组合 × 32 题 · 判定为凑齐向量的支配关系（提案问题 3）</div>'
        + card('怎么读', '<p>每个措施只和"只差它一个因素"的配置比较：一个问题只用一条检索文本（不融合），分解时不合并（每个信息项看它对应的子问题）。'
               '融合、合并单列为"组合方式"。每条原因注明验证它的实验；没做实验的标"未验证"。'
               '变好是设计预期，下面只解释变差与各有优势。</p>'
               '<p class="down"><b>未完成</b>：3 次独立生成（扰动测试）在收集时因系统内存不足被终止，本页只有第 0 次生成。'
               '去编码与机械改动不经过 LLM，结果恒定；翻译、HyDE、改写、step-back、分解的稳定性待重跑后补上。</p>')
        + '<h2>结论</h2>' + concl
        + card('附带发现', SIDE_FINDINGS)
        + '<h2>依据</h2>' + nav + panels)
    write_assets(ROOT / "reports")
    (HERE / "index.html").write_text(page("查询侧措施的单独效果", body, root="../../",
                                          crumbs=[("报告", "../../index.html"), ("V3.14 对比页", PAGE)]),
                                     encoding="utf-8")
    print("index.html")


if __name__ == "__main__":
    main()
