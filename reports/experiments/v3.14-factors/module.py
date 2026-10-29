"""Module 1 of the comparison page (reports/query/v3.14/): what each query-side measure does on its own,
measure by measure, with fixed items: recommendation, effect alone, every question that got worse or
mixed (its verdicts in the four generations, the channel ranks, the cause and whether it was tested),
which questions suit it, randomness, and whether the losses come from the measure itself.

Numbers are computed from pairs.json and the experiments; only the causes and readings are written by
hand, and a question without a tested cause shows "未验证".

Writes reports/query/v3.14/analysis.html (a fragment the page embeds as module 1).
Run: python reports/experiments/v3.14-factors/module.py   (after pairs.py; then `python -m rag report`
or review.py re-renders the page)
"""

from __future__ import annotations

from collections import Counter
from urllib.parse import quote

from rag.reports.html import esc
from review import CHAN, FEAT, NOISE, ROOT, SIDE_FINDINGS, STEP, comp, mark, qstab

OUT = ROOT / "reports/query/v3.14/analysis.html"
EVIDENCE = "../../experiments/v3.14-factors/index.html"
KIND = {"P3.2.1": "rewrite", "P3.2.2": "translation", "P3.2.3": "hyde", "P3.2.4": "step_back"}
DOC = {"d359": "合同", "5a4c": "PO 25-19", "169a": "PO 24-12", "5883": "PO 25-15", "817c": "发票"}
UNTESTED = '<span class="down">未验证</span>'


def here(c: dict, text: str) -> str:
    """A link that sets the page's two sides to the comparison's configurations."""
    return f'<a href="#L={quote(c["left"], safe="")}&R={quote(c["right"], safe="")}&k=3">{esc(text)}</a>'


def nums(c: dict) -> str:
    s = qstab(c)
    t = c["counts"]["0"]
    gens = " ".join(f'<span class="up">{d["better"]}</span>/<span class="down">{d["worse"]}</span>/{d["mixed"]}' for d in s["gens"])
    flip = f'，翻转 {len(s["flip"])} 题' if s["flip"] else "，无翻转"
    return (f'第 0 次题次 <span class="up">▲{t["better"]}</span> <span class="down">▼{t["worse"]}</span> ◆{t["mixed"]}'
            f'（13 组合 × 32 题）；按题 ▲/▼/◆，第 0–3 次：{gens}；4 次一致 '
            f'<span class="up">▲{len(s["stable"]["better"])}</span> <span class="down">▼{len(s["stable"]["worse"])}</span> '
            f'◆{len(s["stable"]["mixed"])}{flip}')


def ranks(n) -> str:
    return "·".join("∞" if x is None else str(x) for x in n)


def channel(kind: str, case: str) -> str:
    """Generation 0, RM-T, codes stripped: original text on hybrid → the phrasing on each retriever."""
    c = CHAN[case]
    o, p = c["question"]["hybrid"]["RM-T"]["n"], c[kind]
    bm = p["bm25"]["RM-T"]["n"]
    return (f'原句 {ranks(o)} → 向量 {ranks(p["semantic"]["RM-T"]["n"])} · '
            f'BM25 {"无匹配" if all(x is None for x in bm) else ranks(bm)} · 混合 {ranks(p["hybrid"]["RM-T"]["n"])}')


def noise(case: str) -> str:
    edits = NOISE.get(case, {})
    out = [f'{esc(name)} {mark(e["v"]["RM-T"])}' for name, e in edits.items()]
    return " · ".join(out) or "—"


def stepback(case: str) -> str:
    if case not in STEP:
        return "—（没有去掉题面的拉丁字母词）"
    s = STEP[case]
    n = s["n"]["hybrid"]
    return (f'去掉 {esc("、".join(s["texts"]["dropped"]))}：原句 {ranks(n["原句"]["RM-T"])} → step-back {ranks(n["step-back"]["RM-T"])} '
            f'→ 补回 {ranks(n["补回"]["RM-T"])}')


EXTRA = {"channel": ("通道名次（第 0 次 · RM-T · 去编码）", None), "noise": ("机械改动的判定（RM-T）", noise),
         "step": ("补回被去掉的词（混合 · RM-T）", stepback)}


def question_table(forms: list[tuple[str, dict]], causes: dict[str, str], extra: str | None, kind: str | None,
                   later: str | None = None) -> str:
    """`later`: the cause shown for a question that is neither worse nor mixed in the first column (the
    measure alone) but only in the others."""
    stabs = [(label, qstab(c)) for label, c in forms]
    cases = [q for q in forms[0][1]["cells"]
             if any(set(s["per"][q]) & {"worse", "mixed"} for _, s in stabs)]
    head = "".join(f'<th class="nw">{esc(label)}<br><span class="meta">第 0–3 次</span></th>' for label, _ in stabs)
    if extra:
        head += f'<th>{esc(EXTRA[extra][0])}</th>'
    rows = []
    for q in sorted(cases, key=lambda q: (-sum(s["per"][q].count("worse") for _, s in stabs), q)):
        f = FEAT[q]
        tab = sum(f["table_items"])
        cells = "".join(f'<td class="nw">{"".join(mark(v) for v in s["per"][q])}</td>' for _, s in stabs)
        if extra == "channel":
            cells += f'<td class="nw meta">{channel(kind, q)}</td>'
        elif extra:
            cells += f'<td class="meta">{EXTRA[extra][1](q)}</td>'
        rows.append(f'<tr><td class="nw">{esc(q)}<br><span class="meta">{esc(DOC[f["document"][:4]])} · 证据在表 {tab}/{f["items"]}</span></td>'
                    f'{cells}<td>{causes.get(q, later if later and not set(stabs[0][1]["per"][q]) & {"worse", "mixed"} else UNTESTED)}</td></tr>')
    return ('<div class="scroll"><table><tr><th>题</th>' + head + '<th>原因</th></tr>' + "".join(rows) + '</table></div>')


def item_split(c: dict) -> str:
    out = Counter()
    for case, combos in c["cells"].items():
        for i, tab in enumerate(FEAT[case]["table_items"]):
            for cell in combos.values():
                a, b = cell["l"][0][i], cell["r"][0][i]
                a, b = (99 if a is None else a), (99 if b is None else b)
                out[("表" if tab else "文", "↑" if b < a else "↓" if b > a else "=")] += 1
    return "；".join(f'证据在{"表格" if t == "表" else "正文"}的信息项 <span class="up">↑{out[(t, "↑")]}</span> '
                    f'<span class="down">↓{out[(t, "↓")]}</span>' for t in "表文")


# ---------------------------------------------------------------- the measures

MEASURES = [
    {
        "id": "strip", "name": "P3.2.0 去编码", "badge": ("建议固定启用", "ok"),
        "pair": ("P3.2.0", "原问题 · 原句"),
        "forms": [("原句", "P3.2.0", "原问题 · 原句"), ("翻译", "P3.2.0", "原问题 · 翻译"), ("HyDE", "P3.2.0", "原问题 · HyDE")],
        "extra": None,
        "later": "原句上去编码不变或变好，只在 LLM 写法上有变差：写法每次不同，去编码的效果随之变化（见各次生成的符号），本题的具体原因" + UNTESTED,
        "why": "删去文档过滤识别出的标识编码。编码是用户分配的字符串，不出现在文档里，BM25 匹配不到，只通过向量起作用——而它对向量的作用是噪声：",
        "reading": """<p>证据在正文的信息项从未因去编码变差，变差全部在证据在表格的信息项，幅度 1–2 名。</p>
<p><b>已验证（实验 A，编码替换）</b>：合同正文题只要换掉真实编码的数字（换成其他数字、只剩字母、无意义词）就和删去一样变好——真实编码的数字串（含 2025）把向量拉向含同样数字的块（保函编号 3012025041885 所在的 c0003）。
表格题有两种情况（实验 A 第二部分）：PO-25-19-Q001、Q003、Q005、CONTRACT-105-Q014 中带数字的编码让向量偏向数字密集的表格，删去后表格退后；
PO-25-19-Q002、Q007、PO-24-12-Q004 用任何内容（含无意义词）占住编码的位置都与原样接近，只是删去一个词改变了句子——属于向量对措辞的敏感，与实验 C 同类。</p>""",
        "suit": "适合：证据在正文的题（信息项全部不变或变好）。不适合：证据在数字密集表格的题（小幅变差）。检索前无法知道证据在哪里，无法按题切换。",
        "random": "作用于原句时不经过 LLM，同一输入 4 次结果相同，没有随机性。作用于 LLM 写法时，效果随写法变化（右两列）：总体有利，没有一题 4 次都变差，但个别题在不同生成中方向相反。",
        "itself": "表格题的变差是去掉了一个碰巧有利的噪声，不是去编码引入了错误；前提是编码不出现在文档中（规则如此）。若用户登记了文档里真实出现的编号，删去会丢掉一个 BM25 可匹配的词——本数据中没有这种编码，<b>未验证</b>。",
        "causes": {
            "PO-25-19-Q005": "带数字的编码让向量偏向数字密集的表格，删去后表格退后；换成其他数字与原样相同、只剩字母与删去相同（实验 A，已验证）",
            "CONTRACT-105-Q010": "实验 A 中换成其他数字反而更好、只剩字母一项变好一项变差，不符合\"带数字的编码偏向表格\"，" + UNTESTED,
            "CONTRACT-105-Q014": "与 PO-25-19-Q005 相同：换数字、保留年份接近原样，只剩字母与删去相同（实验 A 第二部分，已验证）；e2 在 17–21 名，K 内无影响",
            "PO-25-19-Q001": "与 PO-25-19-Q005 相同：换数字、保留年份接近原样，只剩字母接近删去（实验 A 第二部分，已验证）",
            "PO-25-19-Q003": "多数组合与 PO-25-19-Q005 相同（带数字有利，实验 A 第二部分，已验证）；LL-C 上换数字反而更差，" + UNTESTED,
            "PO-25-19-Q002": "任何内容占住编码的位置（含无意义词）都接近原样，只有删去变差 → 不是数字的作用，是删去一个词改变了句子（向量对措辞的敏感，实验 A 第二部分，已验证）",
            "PO-25-19-Q007": "同 PO-25-19-Q002：占位即接近原样，删去变差 1–2 名，属于措辞敏感（实验 A 第二部分，已验证）",
            "PO-24-12-Q004": "同 PO-25-19-Q002：无意义词、换数字都与原样相同，删去与只剩字母变差 1 名，属于措辞敏感（实验 A 第二部分，已验证）",
            "PO-25-19-Q009": "带引导句、摘要的组合上删去反而变好（只剩字母最好，与合同正文题同向）；RM-C、RL-C 上变差 1–2 名，" + UNTESTED,
        },
    },
    {
        "id": "translation", "name": "P3.2.2 翻译", "badge": ("建议启用，只与原句 RRF 融合", "ok"),
        "pair": ("P3.2.2", "原问题 · 两侧去编码"),
        "fused": ("RRF 融合", "原问题 · 两侧去编码 · 原句 → 原句 + 翻译"),
        "forms": [("单独（代替原句）", "P3.2.2", "原问题 · 两侧去编码"), ("与原句融合", "RRF 融合", "原问题 · 两侧去编码 · 原句 → 原句 + 翻译")],
        "extra": "channel", "kind": "translation",
        "why": "把问题译成文档语言。中文问题在英语、西语文档上几乎没有 BM25 匹配（只匹配题面的拉丁字母词），<b>V3.13 的混合检索对中文问题实际上就是向量检索</b>（实验 B）。翻译后 BM25 才起作用，所以翻译的效果 = 向量换了语言 + 新加入的 BM25 通道：",
        "reading": """<p><b>BM25 通道帮正文、伤表格（已验证，实验 B 通道拆分）</b>：表格单元以数字、型号为主，问题里的通用词（delivery、date、amount、entrega、fecha）在说明性正文里更多，BM25 把说明段排到表格前面。</p>
<p><b>译文不会用文档的标签（已验证，实验 E）</b>：变差题的译文逐条看过，都忠实于原题；但译文写"fourth delivery batch""Batch 1"，文档表格写"4th Delivery""1st Delivery"。只把这个短语换成文档用词，BM25 中证据 PO-25-19-Q007 10→2、PO-25-15-Q001/Q002 5→3。</p>
<p>与原句 RRF 融合时 BM25 通道只占一半权重，损失减少而收益基本保留（见上面第二行数字）。</p>""",
        "suit": "适合：证据为正文条款的题（合同 Q003、Q011–Q014，PO 24-12 的说明类题，4 次都变好）。不适合：证据全在表格、且文档有大量说明性正文的题（PO 25-15、PO 25-19、发票）。检索前无法知道证据在不在表格，<b>没有可在查询时使用的路由规则</b>，所以只能靠与原句融合来限制损失。",
        "random": "方向稳定：单独使用与融合都没有一题在不同生成中翻转。变差的题大多 4 次都变差；少数题只在部分生成中变差（表中逐题可见），这部分是译文措辞的随机性。",
        "itself": "两个来源都是措施固有的：① 加入词汇匹配通道后说明性正文胜过表格；② 翻译只看问题、不看文档，写不出文档特有的标签——提示词里已要求用该语言文档的术语，无法通用地解决。",
        "causes": {
            "PO-25-15-Q001": "条件说明段 c0002、c0004 含 delivery 等词，BM25 排第 1–2，表格退后（实验 B）；译文\"fourth delivery batch\"与表格标签\"4th Delivery\"不同，换成文档用词 BM25 5→3、e2 仍为 4（实验 E）。已验证，融合后仍 4 次变差",
            "PO-25-15-Q002": "同 PO-25-15-Q001（\"sixth batch\" vs \"6th Delivery\"，实验 B、E，已验证）",
            "PO-25-19-Q007": "向量变好、BM25 排 10、混合 6；\"Batch 1\"换成\"the 1st Delivery\"后 BM25 10→2、混合 6→2（实验 E，已验证）",
            "CONTRACT-105-Q010": "BM25 把价格条款 c0010 等提前，证据在 BM25 排 10、20（实验 B，已验证）",
            "E001-602-Q001": "两个通道都把发票抬头 c0000 排到第 1，证据 1→2（实验 B）；抬头为什么胜出" + UNTESTED,
            "PO-25-19-Q001": "第 0 次 RM-T：BM25 把正文块 c0010 排第 1，e5 混合 2→5（BM25 9）；其他组合中有信息项提前（实验 B）；逐项原因" + UNTESTED,
            "PO-25-19-Q003": "第 0 次 RM-T：e2、e4 混合 4→3，e5 2→5（BM25 8）（实验 B）；逐项原因" + UNTESTED,
            "PO-25-19-Q009": "BM25 把正文块 c0002 排第 1，证据 4（实验 B，第 0 次）；RM-T 混合后不变，变化出现在其他组合，" + UNTESTED,
            "CONTRACT-105-Q009": "RM-T 第 0 次各通道都不变，变差出现在其他组合与生成，" + UNTESTED,
            "PO-25-19-Q006": "第 0 次 RM-T 混合反而变好，变差出现在第 1–3 次，" + UNTESTED,
        },
    },
    {
        "id": "hyde", "name": "P3.2.3 HyDE", "badge": ("只与原句 RRF 融合；单独使用不建议", "warn"),
        "pair": ("P3.2.3", "原问题 · 两侧去编码"),
        "fused": ("RRF 融合", "原问题 · 两侧去编码 · 原句 → 原句 + HyDE"),
        "forms": [("单独（代替原句）", "P3.2.3", "原问题 · 两侧去编码"), ("与原句融合", "RRF 融合", "原问题 · 两侧去编码 · 原句 → 原句 + HyDE"),
                  ("叠加在原句 + 翻译上", "RRF 融合", "原问题 · 去编码 · 原句 + 翻译 → 原句 + 翻译 + HyDE")],
        "extra": "channel", "kind": "hyde",
        "why": "写一段文档语言的假想答案去检索。与翻译一样打开 BM25 通道：",
        "reading": """<p>规律与翻译相同，但对表格更不利（实验 B：加上 BM25 后表格信息项 ↑9 ↓37）。假想段落是叙述句，形似正文条款而不像表格行——这一解释<b>未验证</b>。</p>
<p>与原句融合后净效果为正、方向稳定。叠加在"原句 + 翻译"之上时，4 次都略为正，但多数题是 ◆，幅度接近措辞噪声（实验 C），<b>不能确认叠加有额外收益</b>。</p>""",
        "suit": "与翻译相同：适合证据为正文条款的题，不适合证据在表格的题；检索前无法区分。",
        "random": "单独使用时随机性明显大于翻译：每次假想答案写法不同，第 3 次生成明显更差，有题一次变好三次变差。与原句融合后无翻转。",
        "itself": "对表格不利是措施固有的（BM25 通道 + 叙述体文本）；假想答案的内容可能与文档不符，这是 HyDE 的定义所致，无法通过提示词消除。",
        "causes": {
            "PO-25-15-Q001": "BM25 把条件说明段 c0002 排第 1，证据 4，混合 1·2→3·3（实验 B，第 0 次）；与翻译同一机制，融合后仍 4 次变差",
            "PO-25-15-Q002": "同 PO-25-15-Q001（实验 B）",
            "CONTRACT-105-Q010": "BM25 把价格条款 c0010 排第 1，证据 14、17，混合 6·16（实验 B，第 0 次）",
            "CONTRACT-105-Q001": "向量与 BM25 都退后（见通道列，实验 B，第 0 次）；向量为什么退后" + UNTESTED,
            "PO-25-19-Q006": "向量与混合都 2→3，BM25 把正文块 c0010 排第 1（实验 B，第 0 次）；向量为什么退后" + UNTESTED,
        },
    },
    {
        "id": "rewrite", "name": "P3.2.1 改写", "badge": ("建议不采用", "bad"),
        "pair": ("P3.2.1", "原问题 · 两侧带编码"),
        "forms": [("单独（代替原句）", "P3.2.1", "原问题 · 两侧带编码"), ("去编码时", "P3.2.1", "原问题 · 两侧去编码"),
                  ("与原句融合", "RRF 融合", "原问题 · 两侧去编码 · 原句 → 原句 + 改写")],
        "extra": "noise",
        "why": "把问题改写成更规范的中文。改写仍是中文，不改变 BM25 通道，只改变向量：",
        "reading": """<p><b>改写做的事几乎是固定的</b>：把问句改成名词短语（"…如何收费？"→"…收费标准与计费方式"），4 次生成的改写几乎逐字相同（逐题核对 8 道题的 4 次改写）。
所以它在 4 次中稳定，只说明 LLM 每次写得一样，不能说明改写有真实作用。</p>
<p><b>整体没有方向</b>：4 次都变好与都变差的题一样多。不改意思的机械改动（问号改句号、句子倒序）也会使 9/32、10/23 题的判定改变（实验 C），改写的总量在这个范围内。
但逐题看，<b>改写变差的题在机械改动下多数不变</b>（表中右列），即这几题对"改成名词短语"这种改法敏感，而不是对任意改动敏感；改成名词短语为什么让这些题的证据退后 1 名，""" + UNTESTED + """。</p>
<p>与原句融合也是变差多于变好。</p>""",
        "suit": "找不到稳定适合的题型：4 次都变好与都变差的题数相当。",
        "random": "LLM 的随机性很小（4 次改写几乎相同）；各题的变好、变差是向量对这一种措辞改动的反应，方向因题而异，检索前无法预测。",
        "itself": "改写本身没有可确认的整体作用：它稳定地改变措辞，向量对措辞的反应因题而异、正负相抵；额外一次 LLM 调用换不来可确认的收益。",
        "causes": {},
        "default_cause": "改成名词短语后向量名次变化；本题为什么对这种改法敏感" + UNTESTED,
    },
    {
        "id": "stepback", "name": "P3.2.4 step-back", "badge": ("建议不采用", "bad"),
        "pair": ("P3.2.4", "原问题 · 两侧去编码"),
        "forms": [("单独（代替原句）", "P3.2.4", "原问题 · 两侧去编码"), ("与原句融合", "RRF 融合", "原问题 · 两侧去编码 · 原句 → 原句 + step-back")],
        "extra": "step",
        "why": "把问题退一步问成更一般的问题。各措施中最差：",
        "reading": """<p><b>已验证（实验 D）</b>：step-back 按定义去掉把问题限定到单个实例的条件，而这些条件正是题面里的公司名、型号和西语条款名（ELUC、HXE34K-S1、Garantía Técnico-Comercial）；对中文问题它们还是仅有的 BM25 锚点。
把被去掉的词原样补回（其余不动），多数变差题恢复到原句水平（右列）。
没有拉丁字母词的题，step-back 去掉的是中文限定（哪一批、哪家公司、哪种担保）；补回这些限定后 PO-25-19-Q009、CONTRACT-105-Q014、PO-25-19-Q005 恢复，CONTRACT-105-Q010 部分恢复（实验 D 第二部分，已验证）。</p>
<p>与原句融合仍是变差多于变好。</p>""",
        "suit": "变好的题：答案在通用条款里、按批次统一规定的题（CONTRACT-105-Q012、PO-25-19-Q007），去掉具体批次反而贴近原文；CONTRACT-105-Q011 的提升来自去掉误导 BM25 的\"Ítem\"，属于碰巧。这类题检索前无法识别。",
        "random": "4 次都明显变差、无翻转：缺陷稳定存在，不是某次写法的偶然。",
        "itself": "<b>是措施本身的固有缺陷</b>：它要去掉的恰是检索最需要的词。",
        "causes": {
            "E001-602-Q002": "去掉型号 HXE34K-S1，补回后恢复（实验 D，已验证）",
            "CONTRACT-105-Q002": "去掉 Ítem，补回后大部分恢复（实验 D，已验证）",
            "CONTRACT-105-Q005": "去掉 Garantía Técnico-Comercial，补回后恢复（实验 D，已验证）",
            "CONTRACT-105-Q006": "去掉 Falla Repetitiva，补回后恢复（实验 D，已验证）",
            "PO-25-19-Q002": "去掉 ELUC、HXE34K-S1，补回后恢复（实验 D，已验证）",
            "PO-25-19-Q006": "去掉 ELPU，补回后恢复（实验 D，已验证）",
            "PO-24-12-Q004": "去掉 MDC ORCA，补回后恢复（实验 D，已验证）",
            "PO-25-19-Q001": "补回 ELCTO 不恢复；step-back 还改写了\"交货计划\"的问法，" + UNTESTED,
            "PO-25-19-Q003": "补回 ENSA 不恢复；step-back 还改写了\"交货计划\"的问法，" + UNTESTED,
            "PO-25-19-Q009": "去掉中文限定\"唯一对序列号有额外要求的电力公司\"（改问\"各客户\"），补回后 9→2 恢复（实验 D 第二部分，已验证）",
            "CONTRACT-105-Q014": "去掉\"技术商务担保\"（改问\"各项担保期限\"），补回后恢复（实验 D 第二部分，已验证）",
            "CONTRACT-105-Q010": "改问\"交货条款及时间进度表\"，去掉\"分批交货\"；补回后 e1 恢复、e2 差 1 名（实验 D 第二部分，部分验证）",
            "PO-25-19-Q005": "\"数量最多的一款\"改问\"所有产品明细\"；补回后好于原句（实验 D 第二部分，已验证）",
            "PO-25-15-Q001": "去掉\"第四批次\"，但补回后不恢复——表格写\"4th Delivery\"，中文批次词本来匹配不到；损失在别处，" + UNTESTED,
            "PO-25-15-Q002": "同 PO-25-15-Q001（补回\"第六批次\"不恢复），" + UNTESTED,
            "PO-24-12-Q005": "第 0、1 次的 step-back 与去编码的原句只差一个空格（\"订单 的\"→\"订单的\"）仍变差，变化属于向量对措辞的敏感（已核对文本）；为什么一个空格就改变名次" + UNTESTED,
        },
    },
    {
        "id": "decompose", "name": "P3.1 分解", "badge": ("条件启用候选（V3.15 补救）", "warn"),
        "pair": ("P3.1", "原句 · 带编码"),
        "forms": [("检索层 · 带编码", "P3.1", "原句 · 带编码"), ("检索层 · 去编码", "P3.1", "原句 · 去编码"),
                  ("合并：轮流取", "合并", "不合并 → 轮流取")],
        "extra": None,
        "why": "把问题拆成子问题，各自检索。检索层（不合并，每个信息项看它对应的子问题）：",
        "reading": """<p>检索层对表格信息项尤其有利。<b>主要损失在合并层</b>（表中第三列，轮流取相对不合并）：每个子问题的第 1 名必占一位（即使不是证据）、多路一致的单元没有加分、隐含前提拆成多问被稀释（上次已验证，<code>reports/experiments/v3.14-decomposition-diagnosis/</code>）。</p>
<p>检索层的变差上次逐题做过对照实验，分四类：A 答案不写在文档里、需计算或推断（不是拆解导致）；B 证据单元的表示问题；C 拆解丢了所指或限定条件（真正的拆解缺陷）；D 第二跳依赖第一跳。</p>""",
        "suit": "不适合：答案需从多个数计算或推断的题（A）、第二跳依赖第一跳结果的题（D）——已验证。变好的题有什么共同特征" + UNTESTED + "。",
        "random": "检索层 4 次方向一致、无翻转；拆解每次不同，个别题的子问题丢限定条件属于生成波动。",
        "itself": "C 类是拆解本身的缺陷（补回后不差于原问题）；A、B、D 不是拆解导致的，拆解只是没能补救。合并层的损失是合并方式的问题，留给 V3.15。",
        "causes": {
            "CONTRACT-105-Q002": "A：e5 合同总金额在合同中没有合计数；子问题改问所依据的事实（各 Ítem 总价）后 13 组合都回到第 1（已验证）",
            "CONTRACT-105-Q010": "A：e1 需推断；单独问同样差（已验证）",
            "PO-24-12-Q001": "B：表格文字里没有\"本订单包含哪些产品\"的表达，同一子问题在加引导句的入库方式下排第 1（已验证）",
            "PO-24-12-Q004": "C：子问题丢了所指（MDC ORCA 额外费用的付款），补回后 13 组合都不差于原问题（已验证）",
            "PO-25-19-Q002": "A + C：e2 需推断，且子问题丢了 HXE34K-S1，补回限定后恢复（已验证）",
            "PO-25-19-Q006": "D：第二跳依赖第一跳结果，降 1 名（已验证）",
            "CONTRACT-105-Q001": "带编码时检索层 ◆、合并后反而变好；去编码时变差：去编码使原问题本身 4 次都变好（见 P3.2.0），子问题去编码后没有同样的提升，所以对照的基线变高了。子问题为什么没有提升" + UNTESTED,
            "E001-602-Q001": "同 CONTRACT-105-Q001：只在去编码时变差，去编码使原问题本身 4 次都变好（见 P3.2.0）；子问题为什么没有提升" + UNTESTED,
        },
        "later": "检索层不变或变好，只在合并层变差：轮流取按子问题轮流各取一个，子问题有 k 个时，子问题内第 r 名排到约 (r−1)·k 之后，其他子问题排在前面的非证据单元占了位置（合并算法的必然结果，上次逐题核对过 5 题；各题的合并顺序见模块 6 查询过程）",
    },
]

COMBINE = [("RRF 融合", "原问题 · 两侧去编码 · 原句 → 原句 + 翻译"), ("RRF 融合", "原问题 · 两侧去编码 · 原句 → 原句 + HyDE"),
           ("RRF 融合", "原问题 · 去编码 · 原句 + 翻译 → 原句 + 翻译 + HyDE"),
           ("RRF 融合", "原问题 · 两侧去编码 · 原句 → 原句 + 改写"), ("RRF 融合", "原问题 · 两侧去编码 · 原句 → 原句 + step-back"),
           ("RRF 融合", "子问题（不合并） · 两侧去编码 · 原句 → 原句 + 翻译"), ("RRF 融合", "子问题（不合并） · 两侧去编码 · 原句 → 原句 + HyDE"),
           ("合并", "不合并 → 轮流取"), ("合并", "轮流取 → 轮流取 + 原问题一路"),
           ("合并", "不合并 → 全局 RRF"), ("合并", "全局 RRF → 全局 RRF + 原问题一路")]


def measure(m: dict) -> str:
    c = comp(*m["pair"])
    forms = [(label, comp(f, x)) for label, f, x in m["forms"]]
    causes = dict(m["causes"])
    if "default_cause" in m:
        for q in c["cells"]:
            causes.setdefault(q, m["default_cause"])
    lines = [f'<tr><th class="nw">{esc(label)}</th><td>{here(fc, (fc["left"] or "V3.13") + " → " + fc["right"])}<br>{nums(fc)}</td></tr>'
             for label, fc in forms]
    s = qstab(c)
    listed = "；".join(f'4 次都{name}：{esc("、".join(s["stable"][v])) or "无"}' for v, name in (("better", "变好"), ("worse", "变差")))
    return (
        f'<h3>{esc(m["name"])} <span class="badge {m["badge"][1]}">{esc(m["badge"][0])}</span></h3>'
        '<table class="kv">'
        f'<tr><th class="nw">做什么、效果</th><td>{m["why"]}<br>{item_split(c)}（第 0 次，单独使用）</td></tr>'
        + "".join(lines)
        + f'<tr><th class="nw">变差、各有优势的原因</th><td>{m["reading"]}</td></tr>'
        f'<tr><th class="nw">适合 / 不适合的题</th><td>{m["suit"]}<br><span class="meta">单独使用时{listed}</span></td></tr>'
        f'<tr><th class="nw">随机性</th><td>{m["random"]}</td></tr>'
        f'<tr><th class="nw">是不是措施本身的问题</th><td>{m["itself"]}</td></tr>'
        '</table>'
        '<p class="meta">逐题：所有在任一列、任一次生成中变差（▼）或各有优势（◆）的题；每格 4 个符号依次为第 0–3 次生成的按题判定'
        '（一题在该次生成中有变化的组合同向即为该方向，否则 ◆）。先列变差次数多的题。</p>'
        + question_table(forms, causes, m["extra"], m.get("kind"), m.get("later")))


def overview() -> str:
    rows = []
    for m in MEASURES:
        s = qstab(comp(*m["pair"]))
        g = s["gens"][0]
        fused = ""
        if "fused" in m:
            f = qstab(comp(*m["fused"]))
            fused = (f'<br>与原句融合：<span class="up">▲{f["gens"][0]["better"]}</span> <span class="down">▼{f["gens"][0]["worse"]}</span> '
                     f'◆{f["gens"][0]["mixed"]}')
        rows.append(f'<tr><td class="nw"><a href="#a-{m["id"]}" data-atab="{m["id"]}">{esc(m["name"])}</a></td>'
                    f'<td><span class="badge {m["badge"][1]}">{esc(m["badge"][0])}</span></td>'
                    f'<td class="nw"><span class="up">▲{g["better"]}</span> <span class="down">▼{g["worse"]}</span> ◆{g["mixed"]}{fused}</td>'
                    f'<td class="nw"><span class="up">▲{len(s["stable"]["better"])}</span> <span class="down">▼{len(s["stable"]["worse"])}</span>'
                    f'{" · 翻转 " + str(len(s["flip"])) if s["flip"] else ""}</td><td>{m["itself"]}</td></tr>')
    return ('<div class="scroll"><table><tr><th>措施</th><th>建议</th><th>单独使用（第 0 次，按题）</th><th>4 次都同向（题）</th>'
            '<th>变差是不是措施本身的问题</th></tr>' + "".join(rows) + '</table></div>')


def combine() -> str:
    rows = "".join(f'<tr><td class="nw">{esc(f)}</td><td>{here(c, x)}</td><td>{nums(c)}</td></tr>'
                   for f, x in COMBINE for c in [comp(f, x)])
    return ('<p>措施怎么组合（不是措施本身的作用）：与原句 RRF 融合后，翻译、HyDE 的变差减少、变好基本保留；改写、step-back 融合后仍变差多于变好；'
            '在子问题上融合翻译仍为正、融合 HyDE 变差略多。分解的主要损失在合并层，加上原问题一路能补回一部分；合并方式留给 V3.15。</p>'
            '<div class="scroll"><table><tr><th>方式</th><th>对照（点击在本页设置两侧）</th><th>结果</th></tr>' + rows + '</table></div>')


def main() -> None:
    tabs = [(m["id"], m["name"], measure(m)) for m in MEASURES] + [("combine", "融合与合并", combine())]
    on, hidden = ' class="on"', ' class="hidden"'
    nav = '<div class="seg" data-atabs>' + "".join(
        f'<button type="button" data-v="{t}"{on if i == 0 else ""}>{esc(n)}</button>' for i, (t, n, _) in enumerate(tabs)) + '</div>'
    panels = "".join(f'<div data-apanel="{t}" id="a-{t}"{"" if i == 0 else hidden}>{body}</div>'
                     for i, (t, _, body) in enumerate(tabs))
    html = (
        '<div class="card">'
        '<p><b>怎么读</b>：每个措施只和"只差它一个因素"的配置比较——一个问题只用一条检索文本（不融合）、分解时不合并（每个信息项看它对应的子问题），'
        '避免把几种因素的影响混在一起。数字为 13 组合 × 32 题；每个措施都在 4 次独立生成（0–3）中重算，看效果是否随 LLM 每次写法不同而变化；注意 4 次写法几乎相同时（改写）稳定是必然的，不能据此判断措施有真实作用。'
        '每条原因注明验证它的实验，没做实验的标<span class="down">未验证</span>。实验数据与逐项依据见'
        f'<a href="{EVIDENCE}" target="_blank">各措施单独效果的依据页</a>（实验 A–E）。</p>'
        + overview() + '</div>'
        '<div class="card">' + nav + panels + '</div>'
        '<div class="card"><h3>附带发现</h3>' + SIDE_FINDINGS + '</div>'
        '<script>(function(){var bs=document.querySelectorAll("[data-atabs] button");function show(v){'
        'bs.forEach(function(b){b.classList.toggle("on",b.dataset.v===v)});'
        'document.querySelectorAll("[data-apanel]").forEach(function(p){p.classList.toggle("hidden",p.dataset.apanel!==v)})}'
        'bs.forEach(function(b){b.addEventListener("click",function(){show(b.dataset.v)})});'
        'document.querySelectorAll("[data-atab]").forEach(function(a){a.addEventListener("click",function(e){e.preventDefault();show(a.dataset.atab);'
        'document.querySelector("[data-atabs]").scrollIntoView({behavior:"smooth"})})})})();</script>')
    OUT.write_text(html, encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
