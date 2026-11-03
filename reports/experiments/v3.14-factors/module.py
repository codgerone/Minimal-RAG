"""Module 1 of the comparison page (reports/query/v3.14/), "各措施的效果分析": a total table of measures 0–13 at three
levels (the original question, each sub-question, both pooled) from reports/experiments/v3.14-average/levels.json,
with a reference, the wording noise floor and direct comparisons; then, per group, a collapsible detailed analysis
with fixed items: recommendation, effect alone, every question that got worse or
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
from review import CHAN, FEAT, NOISE, PAIRS, ROOT, SIDE_FINDINGS, STEP, SUBTR, comp, mark, qstab

OUT = ROOT / "reports/query/v3.14/analysis.html"
EVIDENCE = "../../experiments/v3.14-factors/index.html"
KIND = {"P3.2.1": "rewrite", "P3.2.2": "translation", "P3.2.3": "hyde", "P3.2.4": "step_back"}
DOC = {"d359": "合同", "5a4c": "PO 25-19", "169a": "PO 24-12", "5883": "PO 25-15", "817c": "发票"}
UNTESTED = '<span class="down">未验证</span>'


def here(c: dict, text: str) -> str:
    """A link that sets the page's two sides to the comparison's configurations."""
    return f'<a href="#L={quote(c["left"], safe="")}&R={quote(c["right"], safe="")}&k=3">{esc(text)}</a>'


def net(x: float) -> str:
    cls = "up" if x > 0.05 else "down" if x < -0.05 else ""
    return f'<span class="{cls}">{x:+.1f}</span>'


def nums(c: dict) -> str:
    """The comparison's numbers by the rule of 2026-11-02 (rules/evaluation.md): per generation, questions better /
    worse / mixed averaged over the 13 combinations; the net, its mean and range over the four generations; stable
    and flips counted per combination and averaged."""
    s = qstab(c)
    gens = " ".join(f'<span class="up">{d["better"]:.1f}</span>/<span class="down">{d["worse"]:.1f}</span>/{d["mixed"]:.1f}' for d in s["gens"])
    return (f'净值 {net(sum(s["net"]) / 4)}（第 0–3 次 {min(s["net"]):+.1f} … {max(s["net"]):+.1f}）；'
            f'13 组合平均的题数 ▲/▼/◆，第 0–3 次：{gens}；4 次都变好 <span class="up">{s["stable"]["better"]:.1f}</span> / '
            f'都变差 <span class="down">{s["stable"]["worse"]:.1f}</span>（翻转 {s["flips"]:.1f}）')


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


def subchannel(case: str) -> str:
    """Generation 0, RM-T, codes stripped: per information item whose hybrid rank changed, its Chinese
    sub-question → the translation on each retriever (exp_subtrans.py)."""
    d = SUBTR[case]
    out = []
    for item, subs in d["mapping"].items():
        for s in subs:
            r = d["subquestions"][s]["ranks"]
            a, b = (tuple(r[k][ret]["RM-T"][item]["n"] for ret in ("semantic", "bm25", "hybrid")) for k in ("question", "translation"))
            if a[2] != b[2]:
                out.append(f'{item}←{s} {"/".join(ranks([x]) for x in a)} → {"/".join(ranks([x]) for x in b)}')
    return "<br>".join(out) or "RM-T 第 0 次不变"


EXTRA = {"channel": ("通道名次（第 0 次 · RM-T · 去编码）", None), "noise": ("机械改动的判定（RM-T）", noise),
         "subchannel": ("变化的信息项（第 0 次 · RM-T）：中文子问题 → 译文，向量/BM25/混合", subchannel),
         "step": ("补回被去掉的词（混合 · RM-T）", stepback)}


def question_table(forms: list[tuple[str, dict]], causes: dict[str, str], extra: str | None, kind: str | None,
                   later: str | None = None) -> str:
    """Every question with a worse or mixed combination in any column and generation; each cell gives, for
    generations 0–3, how many of the 13 combinations are better / worse. `later`: the cause shown for a
    question that has none in the first column (the measure alone) but only in the others."""
    stabs = [(label, qstab(c)) for label, c in forms]
    bad = lambda s, q: any(x[1] or x[2] for x in s["counts"][q])
    cases = [q for q in forms[0][1]["cells"] if any(bad(s, q) for _, s in stabs)]
    head = "".join(f'<th class="nw">{esc(label)}<br><span class="meta">第 0–3 次 变好/变差（组合数）</span></th>' for label, _ in stabs)
    if extra:
        head += f'<th>{esc(EXTRA[extra][0])}</th>'
    rows = []
    for q in sorted(cases, key=lambda q: (-sum(x[1] for _, s in stabs for x in s["counts"][q]), q)):
        f = FEAT[q]
        tab = sum(f["table_items"])
        cells = "".join('<td class="nw">' + " · ".join(
            f'<span class="up">{b}</span>/<span class="down">{w}</span>' + (f'<span class="meta">◆{m}</span>' if m else "")
            for b, w, m in s["counts"][q]) + "</td>" for _, s in stabs)
        if extra == "channel":
            cells += f'<td class="nw meta">{channel(kind, q)}</td>'
        elif extra:
            cells += f'<td class="meta">{EXTRA[extra][1](q)}</td>'
        rows.append(f'<tr><td class="nw">{esc(q)}<br><span class="meta">{esc(DOC[f["document"][:4]])} · 证据在表 {tab}/{f["items"]}</span></td>'
                    f'{cells}<td>{causes.get(q, later if later and not bad(stabs[0][1], q) else UNTESTED)}</td></tr>')
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
        "random": "作用于原句时不经过 LLM，同一输入 4 次结果相同，没有随机性。作用于 LLM 写法时，效果随写法变化（右两列）：净值平均为正（翻译上 +2.7，HyDE 上 +2.1），但各次生成差别大（翻译上 −0.2 … +4.8，HyDE 上 −1.4 … +7.0），翻转较多（按组合平均 3.4、2.6 题）。",
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
        "forms": [("原问题上（译文代替原句）", "P3.2.2", "原问题 · 两侧去编码"),
                  ("子问题上（译文代替子问题，不合并）", "P3.2.2", "子问题（不合并） · 两侧去编码")],
        "extra": "channel", "kind": "translation",
        "tables": [
            {"title": "原问题上", "forms": [0]},
            {"title": "子问题上（不合并）", "forms": [1], "extra": "subchannel", "causes_key": "sub_causes",
             "intro": """<p>左侧是中文子问题，右侧是该子问题的译文，两侧都去编码；每个信息项看标注给它的子问题。与原问题上相比，净值 +3.6 → +1.0，4 次都变差 3.5 → 6.2 题（按组合平均）；
证据在正文的信息项由 ↑132 ↓39 变为 ↑89 ↓70，正文上的优势明显变小（第 0 次，13 组合）。逐题原因来自实验 F（依据页）：子问题译文的通道拆分、BM25 逐词得分与只改一处的对照。</p>
<p><b>子问题上新出现的变差，机制相同：子问题短、只问一项，译文中能在文档里匹配上的词很少。</b>问到的那个词不是证据单元的用词时，BM25 只能靠 order、contrato、factura 这类到处都有的通用词打分，
排在前面的是碰巧含这些词的正文块，混合后证据被挤后。原问题把几项一起问，总有某个词是证据单元的用词（PO-24-12-Q001 原问题译文里的 unit price、total amount 正是表头），所以原问题上不受影响。
已验证的三种情况：① 译文的词不是文档的词（products、quantity 对表头 Goods of Description、Qty；impuestos 对发票的 IGV），换成文档用词后恢复；
② 中文子问题原样引用了文档的拉丁字母词，译文改了词形（Ítem → ítems，西语词干化不把 items 归并为 item，复数只匹配合同标题所在的正文块），改回单数后部分恢复；
③ 证据单元本身不含问题的词（CONTRACT-105-Q001 的 c0001 是续接的第二方当事人段落），换成文档的条款标题也不能恢复。</p>
<p><b>翻译使分解的缺陷显现</b>：PO-24-12-Q004 e3 的子问题丢了所指（分解 C 类）。中文子问题没有 BM25 匹配，只靠向量仍排第 1；译文引入 BM25 后，payment 匹配到另一条付款条款，补回所指后恢复。</p>
<p><b>其余变差与原问题上的原因相同</b>（实验 B、E）：BM25 让说明性正文排到表格前、译文写不出表格的标签。CONTRACT-105-Q007、PO-25-19-Q009 的中文子问题本身好于原问题，译文回到原问题的水平，相对子问题记为变差。</p>
<p><b>各有优势的原因</b>：同一题中，译文的词正好是证据单元用词的信息项提前（PO-24-12-Q004 e1 的 MDC ORCA fees、CONTRACT-105-Q010 e1 的 entregas parciales、CONTRACT-105-Q002 e2 的 cantidad），不是的退后。
子问题只问一项，命中与否接近全有或全无，所以 ◆ 比原问题多。</p>"""},
        ],
        "why": "把问题译成文档语言。中文问题在英语、西语文档上几乎没有 BM25 匹配（只匹配题面的拉丁字母词），<b>V3.13 的混合检索对中文问题实际上就是向量检索</b>（实验 B）。翻译后 BM25 才起作用，所以翻译的效果 = 向量换了语言 + 新加入的 BM25 通道：",
        "reading": """<p><b>BM25 通道帮正文、伤表格（已验证，实验 B 通道拆分）</b>：表格单元以数字、型号为主，问题里的通用词（delivery、date、amount、entrega、fecha）在说明性正文里更多，BM25 把说明段排到表格前面。</p>
<p><b>译文不会用文档的标签（已验证，实验 E）</b>：变差题的译文逐条看过，都忠实于原题；但译文写"fourth delivery batch""Batch 1"，文档表格写"4th Delivery""1st Delivery"。只把这个短语换成文档用词，BM25 中证据 PO-25-19-Q007 10→2、PO-25-15-Q001/Q002 5→3。</p>
<p>与原句 RRF 融合时 BM25 通道只占一半权重，损失减少而收益基本保留——这是组合方式的效果，见"融合与合并"标签页，不算翻译本身的作用。</p>""",
        "suit": "适合：证据为正文条款的题（合同 Q003、Q011–Q014，PO 24-12 的说明类题，4 次都变好）。不适合：证据全在表格、且文档有大量说明性正文的题（PO 25-15、PO 25-19、发票）。检索前无法知道证据在不在表格，<b>没有可在查询时使用的路由规则</b>，所以只能靠与原句融合来限制损失。",
        "random": "净值 4 次都为正（单独 +2.0 … +4.9，融合 +4.8 … +5.8）。翻转按组合平均：单独 1.1 题、融合 0.6 题；变差的题大多 4 次都变差，少数题只在部分生成中变差（表中逐题可见），这部分是译文措辞的随机性。",
        "itself": "两个来源都是措施固有的：① 加入词汇匹配通道后说明性正文胜过表格；② 翻译只看问题、不看文档，写不出文档特有的标签——提示词里已要求用该语言文档的术语，无法通用地解决。用在子问题上时②更突出：子问题只问一项，没有其他词兜底；另有一题是分解丢了所指，被翻译显现出来（分解的缺陷，不是翻译的）。",
        "sub_causes": {
            "PO-24-12-Q001": "e1 3→4、e3 2→4（RM 等组合）：表头是 Goods of Description、Qty，译文的 products、quantity 匹配不到，BM25 只靠 order 匹配正文块 c0003（descending order 等），表格不在 BM25 结果里，混合后正文块提前。换成 goods、Qty 后 BM25 排 1–2、混合回到 2（实验 F，已验证）。原问题的译文含 unit price、total amount，正是表头的词，所以原问题上不变（BM25 逐词得分）",
            "CONTRACT-105-Q001": "e1 5→10：证据 c0001（续接的第二方当事人段落）除 contrato 外不含译文的任何词，BM25 排 12，排在前面的是含 partes、contrato 的条款（c0020 争议解决等）。换成文档的条款标题 otorgantes 不能恢复（该词只在 c0000）（实验 F，BM25 部分已验证）；向量 5→7 的原因" + UNTESTED,
            "CONTRACT-105-Q002": "e1 1→7：中文子问题原样引用 Ítem，BM25 直接匹配表格；译文写 ítems，词干化不归并为 item，只匹配合同标题所在的正文块。改回单数后 BM25 13→6、混合 7→4（实验 F，已验证），其余差距来自 contrato、compra、productos 等通用词（BM25 逐词得分）。e2 2→1：译文的 cantidad 使 BM25 把表格排第 1。e5 合同总金额属分解 A 类（合同中没有合计数）",
            "PO-24-12-Q004": "e3 1→2：子问题丢了所指（MDC ORCA 费用，分解 C 类），译文的 payment 匹配另一条付款条款 c0003（Hexing Peru Payment to China）；补回 MDC ORCA 后三个通道都回到 1（实验 F，已验证）。e1 3→2：译文的 MDC ORCA fees 直接匹配 c0002。带引导句的组合上 e2 1→2 的原因" + UNTESTED,
            "E001-602-Q001": "第 0 次 e3 1→2：impuestos 不是发票的用词（发票写 IGV），BM25 只靠 factura 匹配，发票抬头 c0000 含 factura 更多而排第 1；换成 IGV 后三个通道都回到 1（实验 F，已验证）。第 1–3 次不变",
            "CONTRACT-105-Q007": "中文子问题 e3 排 1，好于原问题（2）；译文中 BM25 把付款条件条款 c0010（含 pago、plazo、compra）排在付款期限条款 c0011 前，混合 1→2，回到原问题的水平。两条款都讲付款，属于多个单元回答相近内容（BM25 逐词得分）",
            "PO-25-19-Q009": "中文子问题 1·1，好于原问题（2·2）；译文中 serial、number、requirements 使 BM25 把序列号说明段 c0002 与正文块 c0010 排在分配表前（表格 BM25 4–6），混合回到 2·2。与原问题同一机制（实验 B；BM25 逐词得分）",
            "PO-25-19-Q006": "e2 2→3：中文子问题里的 ELPU、DDP 直接匹配表格（BM25 2）；译文加入 date、delivery、batch、goods，这些词在说明性正文 c0010、c0012 里更多，表格退到 BM25 7。与原问题同一机制（实验 B；BM25 逐词得分）。另：ELPU's 被切出 s，去掉所有格后 BM25 7→4，但混合仍为 3，不是本题变差的原因（实验 F，已验证）",
            "PO-25-15-Q001": "同原问题：fourth batch 不是表格标签 4th Delivery，BM25 把条件说明段排在前面，证据 BM25 排 4（实验 B、E 在原问题上已验证，子问题上现象相同）",
            "PO-25-15-Q002": "同 PO-25-15-Q001（sixth batch 对 6th Delivery）",
            "CONTRACT-105-Q010": "e1 5→3：entregas parciales 使 BM25 把交货表排第 2；e2 10→13：fechas、entrega、lote 在正文条款里更多，交货表在 BM25 排 7。与原问题同一机制（实验 B；BM25 逐词得分）",
            "PO-25-19-Q001": "随生成而变。第 0 次 RM-T 不变；交货日期 e5 的译文在 BM25 中把交货说明段排在交货表前，其余信息项的译文（unit price、quantity、公司名）直接匹配分配表、价格表而提前。与原问题同一机制（实验 B）；各次生成的逐项原因" + UNTESTED,
            "PO-25-19-Q003": "e5 2→5（RM-T）：交货日期的译文在 BM25 中把交货说明段 c0011、c0010、c0012 排在交货表前（交货表 BM25 5）；其他信息项随生成提前或退后。与原问题同一机制（实验 B）；各次生成的逐项原因" + UNTESTED,
            "PO-25-19-Q007": "只有一个信息项，子问题与原问题相同，结果与原问题相同：Batch 1 对 1st Delivery（实验 E，已验证）",
            "PO-25-19-Q002": "与原问题同为 ◆；逐项原因" + UNTESTED,
            "CONTRACT-105-Q009": "只在 LL+、S 组合变差 1 名，RM-T 第 0 次不变，" + UNTESTED,
            "CONTRACT-105-Q003": "第 0、2 次变好（e2 11→5，lugar、entrega 匹配交货地点所在的表格），第 1、3 次为 ◆，" + UNTESTED,
            "CONTRACT-105-Q004": "第 0、3 次不变，第 1、2 次变差，" + UNTESTED,
            "CONTRACT-105-Q014": "只在第 0 次的 LL-C、LL-T 变差，e2 在 18–21 名，K 内无影响，" + UNTESTED,
        },
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
<p>与原句融合后净值 4 次都为正（+0.9 … +4.2），但翻转 2.7 题（按组合平均）。叠加在"原句 + 翻译"之上时，净值 4 次都略为正（+0.8 … +2.3），平均约 2 题，翻转 3.1 题；
这个幅度是否超出措辞噪声（实验 C 用的是此前的统计口径），""" + UNTESTED + """，所以<b>仍不能确认叠加有额外收益</b>。</p>""",
        "suit": "与翻译相同：适合证据为正文条款的题，不适合证据在表格的题；检索前无法区分。",
        "random": "单独使用时各次生成的净值为 −2.2、−1.6、+0.9、−3.1，方向不定，第 3 次最差：每次假想答案写法不同。与原句融合后净值 4 次都为正，但翻转反而更多（1.5 → 2.7 题，按组合平均）。",
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
<p><b>整体没有方向</b>：带编码时净值 +0.6（−0.2 … +1.8），去编码时 −1.6（−3.1 … 0.0）；去编码时 4 次都变好 2.7 题、都变差 1.8 题（按组合平均）。不改意思的机械改动（问号改句号、句子倒序）也会使 9/32、10/23 题的判定改变（实验 C），改写的总量在这个范围内。
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
        "random": "净值 4 次都明显为负（−10.9 … −5.5），翻转 0.2 题（按组合平均）：缺陷稳定存在，不是某次写法的偶然。",
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
        "random": "检索层净值 4 次都为正（带编码 +4.5 … +6.2，去编码 +3.8 … +4.8），翻转 0.4、0.1 题（按组合平均）；拆解每次不同，个别题的子问题丢限定条件属于生成波动。",
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

# V3.14.1: the language of the rewrite and the step-back (docs/archive/changes/v3.14.1.md)
LANG_STEPS = ("问题语言 → 文档语言", "问题语言 → 两种", "文档语言 → 两种")
LANG_FORMS = (("单独（代替原句）· 原问题", "原问题 · 两侧去编码 · {name}单独 · {step}"),
              ("单独（代替原句）· 子问题（不合并）", "子问题（不合并） · 两侧去编码 · {name}单独 · {step}"),
              ("与原句融合", "原问题 · 两侧去编码 · 原句 + {name} · {step}"),
              ("叠加在原句 + 翻译上", "原问题 · 两侧去编码 · 原句 + 翻译 + {name} · {step}"))
LANGUAGE_READING = {   # measure id → the written reading (2026-11-02, after the results; numbers by the averaged rule)
    "rewrite": """<p><b>改写用文档语言写明显好于问题语言</b>：单独使用、原问题上，问题语言 → 文档语言 净值 +5.8（4 次 +5.2 … +7.0），4 次都变好 9.2 题、都变差 4.9 题，翻转 0.8 题（按组合平均）。
相对去编码原句，文档语言的改写净值 +4.6（+3.8 … +5.2），问题语言的改写 −1.6，翻译 +3.6；文档语言的改写与翻译直接比较 +3.0（+1.3 … +4.3），但翻转 3.6 题。</p>
<p><b>机制与翻译相同（实验 G 通道拆分，已验证）</b>：问题语言的改写在这些文档上几乎没有 BM25 匹配，混合检索等于向量检索；文档语言的改写打开 BM25 通道，
4 次都变好的题中，BM25 通道把证据排到前列（逐题名次见下表）。4 次都变差的题中已查 4 题，是翻译在 V3.14 中变差的同一类：BM25 把说明性正文排在表格前，或改写的说法不是表格的标签
（PO-25-15 的 fourth batch 对 4th Delivery，换成表格标签后恢复，已验证）；其余各题的原因""" + UNTESTED + """。</p>
<p><b>两种</b>：问题语言 → 两种 净值 +6.1（+5.8 … +6.4）；文档语言 → 两种 −2.5（−3.7 … −1.5），原问题上只用文档语言更好。两种中问题语言一条没有 BM25 匹配，RRF 后文档语言排名的作用减半。
子问题上（不合并）问题语言 → 文档语言 +1.2、→ 两种 +3.7，文档语言 → 两种 −0.3，接近持平；每个子问题单独判定的结果在依据页目录的 exp_lang_subq.json。
2026-11-02 用户决定 V3.15 中改写按两种：测试集题目少，原问题上文档语言的优势可能受此影响。</p>""",
    "stepback": """<p><b>step-back 的语言</b>：单独使用、原问题上，问题语言 → 文档语言 净值 +4.9（−0.6 … +9.1），翻转 6.5 题；问题语言 → 两种 +7.8（+6.1 … +10.1），翻转 2.0 题；文档语言 → 两种 +0.7（−2.1 … +5.2）。
子问题上（不合并）分别为 −0.5、+1.1、+2.9，各次生成差别大。只有两种相对问题语言在原问题上 4 次都为正；汇总原问题与子问题后也是两种最稳定（依据页目录 exp_lang_subq.json），2026-11-02 用户决定 V3.15 中 step-back 按两种。
相对去编码原句，文档语言的 step-back 净值 −3.5（问题语言 −8.9）：V3.14 发现的缺陷（去掉的限定条件正是检索最需要的词）与语言无关，仍然存在。各题变化的原因""" + UNTESTED + """。</p>""",
}
G = "（实验 G，已验证）"
_LANG_CH = __import__("json").loads((ROOT / "reports/experiments/v3.14-factors/exp_lang_channels.json").read_text(encoding="utf-8"))
LANGUAGE_CAUSES = {   # measure id → question → cause written by hand; the rest are filled from the data in language_block
    "rewrite": {
        "PO-25-15-Q001": "文档语言改写写 fourth batch，表格标签是 4th Delivery；BM25 把条件说明段 c0002 等排在前，证据 BM25 3·3，混合 1·2 → 2·2。换成 4th Delivery 后 BM25 1·2、混合 1·2，恢复" + G + "。与翻译相同（实验 E）",
        "PO-25-15-Q002": "同 PO-25-15-Q001（sixth batch 对 6th Delivery）：BM25 4·4，混合 1·2 → 2·3；换成表格标签后混合 1·3，部分恢复" + G,
        "PO-25-19-Q006": "问题语言改写保留 ELPU、DDP，BM25 已直接匹配表格（2·2）；文档语言改写加入 delivery、batch、goods，表格在 BM25 中退到 6·6，说明性正文 c0010、c0012 排前，混合 2·2 → 3·3（通道拆分，实验 G）；逐词得分" + UNTESTED,
        "CONTRACT-105-Q007": "BM25 把付款条件条款 c0010 排在付款期限条款 c0011 前，混合 2·1·1 → 1·2·2：一项提前、两项退后（通道拆分，实验 G）；两条款都讲付款，与翻译在子问题上的情况相同",
    },
}


def channel_cause(q: str, kind: str, stable: str) -> str:
    """The measured ranks of a question with no written cause (RM-T, generation 0)."""
    ch = _LANG_CH[q]
    r = lambda name, ret: ranks(ch[f"{kind}:{name}"][ret]["RM-T"]["n"])
    head = {"better": "问题语言 → 文档语言 13 组合 × 4 次都变好", "worse": "问题语言 → 文档语言 13 组合 × 4 次都变差"}.get(stable, "")
    return ((head + "：" if head else "") + f'BM25 中证据 {r("question", "bm25")} → {r("document", "bm25")}，'
            f'混合 {r("question", "hybrid")} → {r("document", "hybrid")}（RM-T，第 0 次，实验 G）'
            + ("" if stable == "better" else "；原因" + UNTESTED))


def language_block(m: dict) -> str:
    """How the language the measure is written in changes retrieval: the three language pairs in each form,
    then, per question, the verdicts of the pairs used alone on the original question."""
    mod = {"rewrite": "P3.2.1", "stepback": "P3.2.4"}[m["id"]]
    name = {"rewrite": "改写", "stepback": "step-back"}[m["id"]]
    rows = []
    for label, context in LANG_FORMS:
        cells = "".join(f'<td>{here(c, step)}<br>{nums(c)}</td>'
                        for step in LANG_STEPS for c in [comp(f"语言 {mod}", context.format(name=name, step=step))])
        rows.append(f'<tr><th class="nw">{esc(label)}</th>{cells}</tr>')
    table = ('<div class="scroll"><table><tr><th>用法（两侧去编码）</th>'
             + "".join(f'<th>{esc(x)}</th>' for x in LANG_STEPS) + '</tr>' + "".join(rows) + '</table></div>')
    forms = [(step, comp(f"语言 {mod}", LANG_FORMS[0][1].format(name=name, step=step))) for step in LANG_STEPS]
    refs = [c for c in PAIRS if c["factor"] == f"语言 {mod}" and c["layer"] == "参考"]
    ref = ('<p class="meta">参考：' + "；".join(f'{here(c, c["context"].split(" · ")[-1])} {nums(c)}' for c in refs) + '</p>') if refs else ""
    return (f'<h4>用什么语言写（V3.14.1 补充）</h4>'
            f'<p class="meta">问题语言为 V3.14 的写法（写法提示词 v8）；文档语言由另一次调用写出（提示词 lang v1，规则相同，只是用文档语言与文档的术语）；'
            f'两种为两段文本各检索一次后 RRF 融合。每格为左 → 右的对照，点击在本页设置两侧。'
            f'实验 G（通道拆分与换成表格标签）的数据在依据页目录的 exp_lang_channels.json、exp_lang_labels.json。</p>'
            + LANGUAGE_READING.get(m["id"], "") + table + ref
            + '<p class="meta">逐题：单独使用、原问题上的三组对照中，任一次生成有组合变差或各有优势的题。</p>'
            + question_table(forms, language_causes(m["id"], forms[0][1]), None, None))


def language_causes(mid: str, c: dict) -> dict[str, str]:
    """The written causes; every other question gets its measured channel ranks."""
    kind = {"rewrite": "rewrite", "stepback": "step_back"}[mid]
    s = qstab(c)
    stable = {q: v for v in ("better", "worse") for q in s["all"][v]}
    out = {q: channel_cause(q, kind, stable.get(q, "")) for q in c["cells"]}
    out.update(LANGUAGE_CAUSES.get(mid, {}))
    return out


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
    listed = "；".join(f'13 组合 × 4 次都{name}：{esc("、".join(s["all"][v])) or "无"}' for v, name in (("better", "变好"), ("worse", "变差")))
    return (
        f'<h3>{esc(m["name"])} <span class="badge {m["badge"][1]}">{esc(m["badge"][0])}</span></h3>'
        '<table class="kv">'
        f'<tr><th class="nw">做什么、效果</th><td>{m["why"]}<br>{item_split(c)}（第 0 次，单独使用）</td></tr>'
        + "".join(lines)
        + f'<tr><th class="nw">变差、各有优势的原因</th><td>{m["reading"]}</td></tr>'
        f'<tr><th class="nw">适合 / 不适合的题</th><td>{m["suit"]}<br><span class="meta">单独使用时 {listed}</span></td></tr>'
        f'<tr><th class="nw">随机性</th><td>{m["random"]}</td></tr>'
        f'<tr><th class="nw">是不是措施本身的问题</th><td>{m["itself"]}</td></tr>'
        '</table>'
        '<p class="meta">逐题（原问题层级）：任一列、任一次生成中有组合变差或各有优势的题；每格依次为第 0–3 次生成中变好 / 变差的组合数（◆ 为各有优势的组合数）。'
        '先列变差组合数多的题。</p>'
        + "".join(
            (f'<h4>{esc(t["title"])}</h4>' if len(m.get("tables", ())) > 1 else "") + t.get("intro", "")
            + question_table([forms[i] for i in t["forms"]], m[t["causes_key"]] if "causes_key" in t else causes,
                             t.get("extra", m["extra"]), m.get("kind"), m.get("later"))
            for t in m.get("tables", [{"forms": list(range(len(forms)))}]))
        + (language_block(m) if m["id"] in ("rewrite", "stepback") else ""))


LEVELS = (("original", "原问题"), ("sub", "子问题"), ("pooled", "汇总"))
LEVEL_DATA = __import__("json").loads((ROOT / "reports/experiments/v3.14-average/levels.json").read_text(encoding="utf-8"))
BY_ID = {m["id"]: m for m in LEVEL_DATA["measures"]}
GROUPS = [   # id, title, measures, the detailed analysis (MEASURES id)
    ("strip", "0 去编码", [0], "strip"), ("decompose", "1 分解", [1], "decompose"),
    ("translation", "2–3 翻译", [2, 3], "translation"), ("hyde", "4–5 HyDE", [4, 5], "hyde"),
    ("rewrite", "6–9 改写", [6, 7, 8, 9], "rewrite"), ("stepback", "10–13 step-back", [10, 11, 12, 13], "stepback")]
ADVICE = {   # measure → (recommendation, badge class); decision 015 and its addendum
    0: ("固定启用", "ok"), 1: ("V3.15：由 LLM 派生子问题，不合并", "ok"),
    2: ("V3.15：Q0 兜底时补上翻译", "warn"), 3: ("V3.15：新问题第 1 次检索", "ok"),
    4: ("V3.15：兜底方式", "warn"), 5: ("参考", ""),
    6: ("不用（V3.15 按两种）", ""), 7: ("不用（V3.15 按两种）", ""), 8: ("V3.15：兜底方式", "warn"), 9: ("参考", ""),
    10: ("不用（V3.15 按两种）", ""), 11: ("不用（V3.15 按两种）", ""), 12: ("V3.15：兜底方式", "warn"), 13: ("参考", ""),
}
_ITSELF = {m["id"]: m["itself"] for m in MEASURES}
WORSE = {   # measure → whether its losses come from the measure itself
    0: _ITSELF["strip"], 1: _ITSELF["decompose"], 2: _ITSELF["translation"],
    3: "与原句 RRF 融合后 BM25 通道只占一半权重，翻译的变差减少、变好基本保留（组合方式的效果，V3.14）。",
    4: _ITSELF["hyde"],
    5: "与原句融合后净值为正；仍变差的题与翻译同类（BM25 通道 + 叙述体文本，实验 B）。",
    6: _ITSELF["rewrite"],
    7: "变差与翻译同类：BM25 把说明性正文排到表格前，或改写的说法不是表格的标签（实验 G 已验证 4 题）；其余各题" + UNTESTED + "。",
    8: "问题语言一条没有 BM25 匹配（实验 G），RRF 后文档语言排名的作用减半，效果介于两者之间。",
    9: "与原句融合的作用未单独分析，" + UNTESTED + "。",
    10: _ITSELF["stepback"],
    11: "缺陷（去掉的限定条件正是检索最需要的词）与语言无关；语言的作用小，各次生成差别大。",
    12: "同上；两种相对问题语言在原问题上 4 次净值都为正，是三种语言中最稳定的。",
    13: "与原句融合的作用未单独分析，" + UNTESTED + "。",
}


def mean(x: dict, v: str) -> float:
    return sum(g[v] for g in x["gens"]) / len(x["gens"])


def bar(x: float, scale: float = 10.0) -> str:
    """A small bar for the net: right of the centre line when positive, left when negative."""
    width = min(abs(x) / scale, 1) * 40
    side = "left:50%" if x >= 0 else "right:50%"
    color = "var(--ok)" if x > 0 else "var(--bad)"
    return (f'<span style="display:inline-block;position:relative;width:80px;height:8px;background:var(--line);vertical-align:middle;margin-left:6px">'
            f'<span style="position:absolute;{side};top:0;height:8px;width:{width:.0f}px;background:{color}"></span>'
            f'<span style="position:absolute;left:50%;top:-2px;height:12px;width:1px;background:var(--muted)"></span></span>')


def cells(x: dict) -> str:
    """变好 / 变差 / 各有优势 / 不变 (means over the generations), net (range), stable (flips)."""
    nets = x["net"]
    m = sum(nets) / len(nets)
    rng = f'（{min(nets):+.1f} … {max(nets):+.1f}）' if len(nets) > 1 else ""
    stable = (f'<span class="up">{x["stable"]["better"]:.1f}</span> / <span class="down">{x["stable"]["worse"]:.1f}</span>'
              f'<span class="meta">（翻转 {x["flips"]:.1f}）</span>') if len(nets) > 1 else '<span class="meta">—</span>'
    return (f'<td class="num">{mean(x, "better"):.1f}</td><td class="num">{mean(x, "worse"):.1f}</td>'
            f'<td class="num">{mean(x, "mixed"):.1f}</td><td class="num">{mean(x, "same"):.1f}</td>'
            f'<td class="nw">{net(m)}<span class="meta">{rng}</span>{bar(m)}</td><td class="nw">{stable}</td>')


HEAD = ('<th class="num">变好</th><th class="num">变差</th><th class="num">各有优势</th><th class="num">不变</th>'
        '<th>净值（4 次范围）</th><th class="nw">4 次都变好 / 都变差<br><span class="meta">（翻转）</span></th>')


def level_note(x: dict, level: str) -> str:
    n = sum(x["questions"]) / len(x["questions"])
    return f'<span class="meta">{n:.0f} 个' + (f'，稳定按 {x["stable_questions"]} 个' if level != "original" else "") + '</span>'


def link(m: dict) -> str:
    return here({"left": m["left"], "right": m["right"]}, f'{m["id"]} {m["name"]}')


def advice(mid: int) -> str:
    text, cls = ADVICE[mid]
    return f'<span class="badge {cls}">{esc(text)}</span>'


def by_level(group: list) -> str:
    """Within a group, the measures next to each other at each level; the recommendation and the analysis of
    the losses on the original-question rows."""
    rows = []
    for level, title in LEVELS:
        ms = [BY_ID[i] for i in group if level in BY_ID[i]]
        for k, m in enumerate(ms):
            x = m[level]
            lead = (f'<td rowspan="{len(ms)}" class="nw"><b>{title}</b><br>{level_note(x, level)}</td>' if k == 0 else "")
            extra = (f'<td>{advice(m["id"])}</td><td class="meta">{WORSE[m["id"]]}</td>' if level == "original"
                     else '<td></td><td></td>')
            rows.append(f'<tr>{lead}<td class="nw">{link(m)}</td>{cells(x)}{extra}</tr>')
    return "".join(rows)


def by_measure(group: list) -> str:
    """Each measure with its levels together."""
    rows = []
    for i in group:
        m = BY_ID[i]
        levels = [(lv, t) for lv, t in LEVELS if lv in m]
        for k, (level, title) in enumerate(levels):
            x = m[level]
            lead = (f'<td rowspan="{len(levels)}" class="nw">{link(m)}</td>' if k == 0 else "")
            tail = (f'<td rowspan="{len(levels)}">{advice(i)}</td><td rowspan="{len(levels)}" class="meta">{WORSE[i]}</td>'
                    if k == 0 else "")
            rows.append(f'<tr>{lead}<td class="nw">{title} {level_note(x, level)}</td>{cells(x)}{tail}</tr>')
    return "".join(rows)


def total_table() -> str:
    tail = '<th style="min-width:110px">建议（V3.15 中的用法）</th><th style="min-width:360px">变差是不是措施本身的问题</th>'
    a = ['<tr><th>层级</th><th>措施（点击在本页设置两侧）</th>' + HEAD + tail + '</tr>']
    b = ['<tr><th>措施（点击在本页设置两侧）</th><th>层级</th>' + HEAD + tail + '</tr>']
    for _, title, group, _ in GROUPS:
        sep = f'<tr><th colspan="10" style="background:#eef1f5">{esc(title)}</th></tr>'
        a.append(sep + by_level(group))
        b.append(sep + by_measure(group))
    return ('<div class="seg" data-mview>排列：<button type="button" data-v="level" class="on">按层级（组内措施相邻）</button>'
            '<button type="button" data-v="measure">按措施（三个层级相邻）</button></div>'
            '<div class="scroll" data-mtable="level"><table>' + "".join(a) + '</table></div>'
            '<div class="scroll hidden" data-mtable="measure"><table>' + "".join(b) + '</table></div>')


def scale_table() -> str:
    """The reference (an effect known to be real) and the wording noise floor."""
    ref = LEVEL_DATA["reference"]
    rows = [f'<tr><td class="nw">{here(ref, ref["name"])}</td><td class="nw">原问题 {level_note(ref["original"], "original")}</td>{cells(ref["original"])}</tr>']
    for level, title in (("original", "原问题"), ("sub", "子问题（第 0 次的分解）")):
        for name, x in LEVEL_DATA["noise"][level].items():
            rows.append(f'<tr><td class="nw">噪声：{esc(name)}</td><td class="nw">{title} {level_note(x, "original")}</td>{cells(x)}</tr>')
    return ('<div class="scroll"><table><tr><th>参照</th><th>层级</th>' + HEAD + '</tr>' + "".join(rows) + '</table></div>')


def direct_table() -> str:
    rows = []
    for d in LEVEL_DATA["direct"]:
        cell = ""
        for level, _ in LEVELS:
            x = d[level]
            m = sum(x["net"]) / 4
            cell += (f'<td class="nw">{net(m)}<span class="meta">（{min(x["net"]):+.1f} … {max(x["net"]):+.1f}）</span>{bar(m)}<br>'
                     f'<span class="meta">▲{mean(x, "better"):.1f} ▼{mean(x, "worse"):.1f} ◆{mean(x, "mixed"):.1f} · '
                     f'4 次都 ▲{x["stable"]["better"]:.1f} ▼{x["stable"]["worse"]:.1f}（翻转 {x["flips"]:.1f}）</span></td>')
        cross = "（跨组）" if (d["from"], d["to"]) in ((2, 7), (3, 9)) else ""
        rows.append(f'<tr><td class="nw">{here(d, d["name"])}{cross}</td>{cell}</tr>')
    return ('<div class="scroll"><table><tr><th>对照（左 → 右，净值为正表示右侧更好）</th>'
            + "".join(f'<th>{t}</th>' for _, t in LEVELS) + '</tr>' + "".join(rows) + '</table></div>')


def per_generation(group: list) -> str:
    """The detail behind the total table: each generation of each measure and level."""
    rows = []
    for i in group:
        m = BY_ID[i]
        for level, title in LEVELS:
            if level not in m:
                continue
            x = m[level]
            gens = "".join(f'<td class="nw"><span class="up">{g["better"]:.1f}</span> / <span class="down">{g["worse"]:.1f}</span> / '
                           f'{g["mixed"]:.1f} · {net(n)}<span class="meta">（{q} 个）</span></td>'
                           for g, n, q in zip(x["gens"], x["net"], x["questions"]))
            rows.append(f'<tr><td class="nw">{m["id"]} {esc(m["name"])}</td><td>{title}</td>{gens}</tr>')
    return ('<h4>逐次生成（13 组合平均的 变好 / 变差 / 各有优势 · 净值）</h4><div class="scroll"><table><tr><th>措施</th><th>层级</th>'
            + "".join(f'<th>第 {g} 次</th>' for g in range(4)) + '</tr>' + "".join(rows) + '</table></div>')


def combine() -> str:
    rows = "".join(f'<tr><td class="nw">{esc(f)}</td><td>{here(c, x)}</td><td>{nums(c)}</td></tr>'
                   for f, x in COMBINE for c in [comp(f, x)])
    return ('<p>措施怎么组合（不是措施本身的作用）：与原句 RRF 融合后，翻译、HyDE 的变差减少、变好基本保留；改写、step-back 融合后仍变差多于变好；'
            '在子问题上融合翻译仍为正、融合 HyDE 变差略多。分解的主要损失在合并层，加上原问题一路能补回一部分；合并方式留给 V3.15。</p>'
            '<div class="scroll"><table><tr><th>方式</th><th>对照（点击在本页设置两侧）</th><th>结果</th></tr>' + rows + '</table></div>')


def main() -> None:
    analysis = {m["id"]: m for m in MEASURES}
    details = "".join(
        f'<details class="card" id="a-{gid}"><summary><b>{esc(title)}：详细分析</b></summary>'
        + per_generation(group) + measure(analysis[mid]) + '</details>'
        for gid, title, group, mid in GROUPS)
    details += ('<details class="card" id="a-other"><summary><b>其他对照：叠加、合并方式、附带发现</b></summary>'
                + combine() + '<h3>附带发现</h3>' + SIDE_FINDINGS + '</details>')
    html = (
        '<div class="card">'
        '<p><b>怎么读</b>：每个措施只和"只差它一个因素"的配置比较。0 去编码以带编码为基线，1 分解以去编码的原问题为基线（按原题判定，不合并）；'
        '2–13 都在去编码的基础上，以同一层级的原句为基线：原问题层级是去编码的原问题，子问题层级是去编码的子问题本身（每个子问题单独判定，只看它自己的排名与标注给它的信息项）；'
        '汇总把两个层级的题数逐次相加。"+ 原句 RRF"指与被检索的那个问题本身的原句融合。'
        '统计口径（2026-11-02 起，rules/evaluation.md）：13 种组合各自判定、各自计数，再按组合取平均；净值 = 变好 − 变差，括号内为 4 次独立生成（0–3）中的最小与最大；'
        '"4 次都变好 / 都变差"与翻转也按组合计数后取平均。子问题在各次生成中不同，稳定与翻转只在 4 次生成文字相同的子问题上计数。'
        '净值都是相对各自基线的，两个措施谁更好要看下方的直接对比。</p>'
        '<h3>尺度参照</h3><p class="meta">最终配置是 V3.14 中确实有效的改动；噪声是代码做的、不改意思的措辞改动（不调用 LLM，只有一次结果），'
        '净值落在噪声范围内的措施，效果与措辞噪声分不开。这些改动大多使题变好、很少变差：句式本身就会系统地影响向量检索，'
        '所以噪声的净值不在 0 附近；看一个措施时，既要看净值是否超出噪声，也要看它变差的题数是否明显多于噪声的变差题数。</p>'
        + scale_table()
        + '<h3>总表</h3>' + total_table()
        + '<h3>直接对比</h3>' + direct_table()
        + f'<p class="meta">每条原因注明验证它的实验，没做实验的标<span class="down">未验证</span>。实验 A–F 的原始记录见<a href="{EVIDENCE}" target="_blank">依据页</a>（按此前的统计口径）。</p>'
        '</div>' + details
        + '<script>(function(){document.querySelectorAll("[data-mview] button").forEach(function(b){b.addEventListener("click",function(){'
        'document.querySelectorAll("[data-mview] button").forEach(function(x){x.classList.toggle("on",x===b)});'
        'document.querySelectorAll("[data-mtable]").forEach(function(t){t.classList.toggle("hidden",t.dataset.mtable!==b.dataset.v)})})})})();</script>')
    OUT.write_text(html, encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
