"""V3.13 对比页：7 种入库方式 × 2 种返回方式（去掉 S-C）共 13 种组合，按提案 1.2 的对照与 2.9 的指标。

用法（仓库根目录）：python reports/experiments/v3.13-llm-tables/compare_page.py <日期>
读取 reports/eval/<日期>_<配置>_k3_filter-on_hybrid[_table][_N]/result.json（同名取最新一次），
以及 unrelated/subquestions.json（排除证据组的对照实验），写入 reports/compare/v3.13-llm-tables/。
"""
import itertools
import json
import sys
from pathlib import Path
from statistics import mean, median

from rag.reports.html import badge, esc, page, pct, write_assets

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).parent
OUT = ROOT / "reports" / "compare" / "v3.13-llm-tables"
MODEL = "gemini38f"
METHODS = [  # 代号, 配置, 说明
    ("RM", "structured_markdown_rows", "规则 Markdown"),
    ("RL", "structured", "规则表头映射"),
    ("LM", f"structured_llm_md_{MODEL}", "LLM Markdown"),
    ("LL", f"structured_llm_labeled_{MODEL}", "LLM 表头映射"),
    ("LM+", f"structured_llm_md_lead_{MODEL}", "LLM Markdown + 引导句"),
    ("LL+", f"structured_llm_labeled_lead_{MODEL}", "LLM 表头映射 + 引导句"),
    ("S", f"structured_summary_{MODEL}", "LLM 摘要"),
]
UNITS = [("C", ""), ("T", "_table")]
CONTRASTS = [  # 对照, 因素, (前, 后) 对
    ("返回单元", "命中 chunk（C）→ 整表（T）", [(f"{m}-C", f"{m}-T") for m, _, _ in METHODS if m != "S"]),
    ("写法", "Markdown → 表头映射", [(f"{a}-{u}", f"{b}-{u}") for a, b in (("RM", "RL"), ("LM", "LL"), ("LM+", "LL+"))
                                  for u in "CT"]),
    ("规则 vs LLM", "规则表头判定 → LLM 结构标注", [(f"{a}-{u}", f"{b}-{u}") for a, b in (("RM", "LM"), ("RL", "LL")) for u in "CT"]),
    ("引导句", "不加 → 加", [(f"{a}-{u}", f"{b}-{u}") for a, b in (("LM", "LM+"), ("LL", "LL+")) for u in "CT"]),
    ("摘要", "表格 chunk 返回整表（X-T）→ 摘要返回整表（S-T）", [(f"{m}-T", "S-T") for m, _, _ in METHODS if m != "S"]),
]
# 与表格入库无关、在第二个视图中排除的表格证据组（依据见 unrelated/subquestions.json 与页面说明）
EXCLUDED = {
    ("CONTRACT-105-Q003", "e2"): "交货地点：表格正文只有一个地址（同一地址在文档中出现 4 次），说明它是交货地点的是表前正文；"
                                 "原问题下 7 种入库方式都排第 20–22，换成只问交货地点的子问题仍排第 11–14，LLM 引导句与摘要都没有改变",
    ("PO-25-19-Q001", "e5"): "交货日期：复合问题（C4），原问题下排第 3–15，只问交货日期的子问题在 7 种入库方式下都排第 1",
    ("PO-25-19-Q003", "e5"): "交货日期：同上（C4）",
}
FINDINGS: list[tuple[str, str]] = []   # 由助手查看结果后写入


def load(date: str) -> dict[str, dict]:
    runs = {}
    for code, config, _ in METHODS:
        for unit, suffix in UNITS:
            if code == "S" and unit == "C":
                continue
            # A name evaluated again the same day gets _2, _3 …; the latest one counts.
            base = f"{date}_{config}_k3_filter-on_hybrid{suffix}"
            found = [p for p in (ROOT / "reports" / "eval").iterdir()
                     if p.name == base or (p.name.startswith(base + "_") and p.name[len(base) + 1:].isdigit())]
            results = [json.loads((p / "result.json").read_text(encoding="utf-8")) for p in found]
            runs[f"{code}-{unit}"] = max(results, key=lambda r: r["created_at"])
    return runs


def table_cases(run: dict) -> dict[str, dict]:
    return {c["case_id"]: c for c in run["cases"] if c.get("table")}


def view(run: dict, exclude: bool) -> dict[str, dict]:
    """Per table question, its kept table groups and the question's metrics 2, 3 and lower bound."""
    out = {}
    for cid, case in table_cases(run).items():
        t = case["table"]
        groups = [g for g in t["groups"] if not (exclude and (cid, g["group_id"]) in EXCLUDED)]
        if not groups:
            continue
        ks = [g["required_k"] for g in groups]
        k = None if None in ks else max(ks)
        lower = min(len(set().union(*combo)) for combo in itertools.product(*[[set(s) for s in g["unit_sets"]] for g in groups]))
        out[cid] = {"groups": groups, "k": k, "tokens": sum(t["leading_tokens"][:k]) if k else None, "lower": lower}
    return out


def stats(run: dict, exclude: bool) -> dict:
    questions = view(run, exclude)
    ranks = [g["chunk_rank"] for q in questions.values() for g in q["groups"]]
    ks = [q["k"] for q in questions.values() if q["k"] is not None]
    tokens = [q["tokens"] for q in questions.values() if q["tokens"] is not None]
    return {"questions": len(questions), "groups": len(ranks), "uncovered": len(questions) - len(ks),
            "m1_mean": mean(r for r in ranks if r), "m1_median": median(r for r in ranks if r),
            "m1_first": sum(r == 1 for r in ranks), "m1_top3": sum(r is not None and r <= 3 for r in ranks),
            "m2_mean": mean(ks), "m2_median": median(ks), "m2_k3": sum(k <= 3 for k in ks),
            "m3_mean": mean(tokens), "m3_median": median(tokens),
            "lower_mean": mean(q["lower"] for q in questions.values())}


def cost(run: dict) -> dict:
    return run.get("ingest_cost") or {"cost_usd": 0.0, "seconds": 0.0, "retries": 0, "degraded_tables": 0, "calls": 0}


def overview(runs: dict, exclude: bool) -> str:
    first = stats(next(iter(runs.values())), exclude)
    q, g = first["questions"], first["groups"]
    rows = []
    for key, run in runs.items():
        s, c, m = stats(run, exclude), cost(run), run["metrics"]
        n = m["hit_rate"]["denominator"]
        rows.append(
            f"<tr><td class=nw><b>{esc(key)}</b></td>"
            f"<td class=num>{s['m1_mean']:.2f}</td><td class=num>{s['m1_median']:g}</td>"
            f"<td class=num>{s['m1_first']} / {g}</td><td class=num>{s['m1_top3']} / {g}</td>"
            f"<td class=num>{s['m2_mean']:.2f}</td><td class=num>{s['m2_median']:g}</td><td class=num>{s['m2_k3']} / {q}</td>"
            f"<td class=num>{s['m3_mean']:,.0f}</td><td class=num>{s['m3_median']:,.0f}</td>"
            f"<td class=num>{s['lower_mean']:.2f}</td>"
            f"<td class=num>{m['hit_rate']['numerator']:.0f} / {n}</td>"
            f"<td class=num>{m['complete_coverage']['numerator']:.0f} / {n}</td>"
            f"<td class=num>{pct(m['mrr']['value'])}</td><td class=num>{run['returned_tokens']['value']:.0f}</td>"
            f"<td class=num>${c['cost_usd']:.4f}</td><td class=num>{c['seconds']:.0f}</td>"
            f"<td class=num>{c['retries']}</td><td class=num>{c['degraded_tables']}</td></tr>")
    return (f'<div class="card"><h3>总表：13 种组合</h3><div class="scroll" data-grid><table>'
            f'<tr><th rowspan=2>组合</th><th colspan=4>指标 1：表格 chunk 名次<br><span class=meta>每个表格证据组一个值，共 {g} 组</span></th>'
            f'<th colspan=3>指标 2：表格证据所需返回单元数<br><span class=meta>每道表格题一个值，共 {q} 题</span></th>'
            f'<th colspan=2>指标 3：表格证据所需 token<br><span class=meta>每道表格题一个值，共 {q} 题</span></th>'
            f'<th>所需单元下限<br><span class=meta>每道表格题</span></th>'
            f'<th colspan=4>护栏：全部 {n} 题，K=3</th><th colspan=4>入库成本：12 张表一次入库</th></tr>'
            f'<tr><th class=num>平均名次</th><th class=num>中位数</th><th class=num>排第 1 的组</th><th class=num>排进前 3 的组</th>'
            f'<th class=num>平均</th><th class=num>中位数</th><th class=num>所需单元数 ≤ 3 的题</th>'
            f'<th class=num>平均</th><th class=num>中位数</th><th class=num>平均</th>'
            f'<th class=num>命中的题</th><th class=num>完整覆盖的题</th><th class=num>MRR</th><th class=num>每题平均返回 token</th>'
            f'<th class=num>LLM 花费</th><th class=num>LLM 调用秒数</th><th class=num>重试次数</th><th class=num>降级的表</th></tr>'
            f'{"".join(rows)}</table></div></div>')


def dominance(runs: dict, exclude: bool) -> str:
    dims = {k: (stats(r, exclude)["m2_mean"], stats(r, exclude)["m3_mean"], round(cost(r)["cost_usd"], 4))
            for k, r in runs.items()}
    rows = []
    for key, d in dims.items():
        by = [o for o, e in dims.items() if o != key and all(x <= y for x, y in zip(e, d)) and e != d]
        verdict = badge("被支配", "bad") + " " + esc("、".join(by)) if by else badge("未被支配，保留", "ok")
        rows.append(f"<tr><td class=nw><b>{esc(key)}</b></td><td class=num>{d[0]:.2f}</td><td class=num>{d[1]:,.0f}</td>"
                    f"<td class=num>${d[2]:.4f}</td><td>{verdict}</td></tr>")
    return (f'<div class="card"><h3>支配判定</h3>'
            f'<p><b>目的</b>：13 种组合在效果、返回 token、入库成本上各有长短。这一节先筛掉“有另一个组合在三方面都不比它差、'
            f'至少一方面更好”的组合——选它没有任何理由；剩下的组合之间才需要按取舍来选。</p>'
            f'<p><b>比较范围</b>：每个组合都与其余 12 个组合逐一比较，所有组合的比较范围相同。</p>'
            f'<p><b>三个维度</b>（都越小越好）：表格证据 = 指标 2 的平均（每道表格题覆盖其表格证据所需的返回单元数）；'
            f'返回 token = 指标 3 的平均（每道表格题覆盖其表格证据所需的 token）；入库成本 = 一次入库 12 张表的 LLM 花费。</p>'
            f'<p><b>判定</b>：若组合 B 在三个维度上都不比 A 大、且至少一个维度更小，则 A 被 B 支配。“判定”列列出支配 A 的全部组合；'
            f'一个都没有就保留。</p>'
            f'<p class="meta">局限：只比数值大小，不考虑差异是否超出随机扰动（平均所需单元数差零点几、只因一两道题）；'
            f'也不看护栏（K=3 的完整覆盖等）。所以“被支配”只说明在这三个维度上不占优，取舍仍要结合逐题对照。</p>'
            f'<div class="scroll" data-grid><table>'
            f'<tr><th>组合</th><th class=num>表格证据：平均所需返回单元数</th><th class=num>返回 token：平均所需 token</th>'
            f'<th class=num>入库成本</th><th>判定</th></tr>{"".join(rows)}</table></div></div>')


def contrasts(runs: dict, exclude: bool) -> str:
    cards = []
    for name, factor, pairs in CONTRASTS:
        rows = []
        for before, after in pairs:
            a, b = view(runs[before], exclude), view(runs[after], exclude)
            ga = {(cid, g["group_id"]): g["chunk_rank"] or 99 for cid, q in a.items() for g in q["groups"]}
            gb = {(cid, g["group_id"]): g["chunk_rank"] or 99 for cid, q in b.items() for g in q["groups"]}
            m1 = (sum(gb[k] < ga[k] for k in ga), sum(gb[k] > ga[k] for k in ga), len(ga))
            m2 = (sum((b[c]["k"] or 99) < (a[c]["k"] or 99) for c in a), sum((b[c]["k"] or 99) > (a[c]["k"] or 99) for c in a), len(a))
            m3 = (sum((b[c]["tokens"] or 10 ** 6) < (a[c]["tokens"] or 10 ** 6) for c in a),
                  sum((b[c]["tokens"] or 10 ** 6) > (a[c]["tokens"] or 10 ** 6) for c in a), len(a))
            sa, sb = stats(runs[before], exclude), stats(runs[after], exclude)

            def cell(v):
                better = badge(f"变好 {v[0]}", "ok") if v[0] else "变好 0"
                worse = badge(f"变差 {v[1]}", "bad") if v[1] else "变差 0"
                return f"<td class=nw>{better} {worse} <span class=meta>/ {v[2]}</span></td>"
            rows.append(f"<tr><td class=nw>{esc(before)} → {esc(after)}</td>{cell(m1)}{cell(m2)}{cell(m3)}"
                        f"<td class=num>{sa['m2_mean']:.2f} → {sb['m2_mean']:.2f}</td>"
                        f"<td class=num>{sa['m3_mean']:,.0f} → {sb['m3_mean']:,.0f}</td></tr>")
        cards.append(f'<div class="card"><h3>{esc(name)}（{esc(factor)}）</h3><div class="scroll" data-grid><table>'
                     f'<tr><th>对照（前 → 后）</th><th>指标 1 名次变好 / 变差的表格证据组</th>'
                     f'<th>指标 2 所需单元数变好 / 变差的表格题</th><th>指标 3 所需 token 变好 / 变差的表格题</th>'
                     f'<th class=num>指标 2 平均（每道表格题）</th><th class=num>指标 3 平均（每道表格题）</th></tr>'
                     f'{"".join(rows)}</table></div></div>')
    return ('<p class="meta">以下各组对照只差一个因素。指标 2、3 只计各题的表格证据（表格证据组中的表格 excerpt），不含同一题中的正文证据；'
            '“/ N”是参与比较的表格证据组数或表格题数。变好 = 名次、所需单元数或 token 变小。</p>' + "".join(cards))


def per_question(runs: dict, exclude: bool) -> str:
    keys = list(runs)
    views = {k: view(r, exclude) for k, r in runs.items()}
    ids = list(views[keys[0]])
    head = "".join(f"<th class=num>{esc(k)}</th>" for k in keys)
    rows = []
    for cid in ids:
        best = min((views[k][cid]["k"] or 99) for k in keys)
        cells = []
        for key in keys:
            q = views[key][cid]
            m1 = "、".join(f"{g['group_id']}:{g['chunk_rank']}" for g in q["groups"])
            mark = ' style="background:var(--ok-bg)"' if (q["k"] or 99) == best else ""
            cells.append(f"<td class=num{mark}><b>{q['k'] if q['k'] is not None else '—'}</b>"
                         f"<div class=meta>{q['tokens'] or '—'} token · 下限 {q['lower']}</div>"
                         f"<div class=meta>名次 {m1}</div></td>")
        rows.append(f"<tr><td class=nw>{esc(cid)}</td>{''.join(cells)}</tr>")
    return (f'<div class="card"><h3>表格题逐题</h3><p class="meta">每格：粗体为指标 2（覆盖本题表格证据所需的返回单元数）；'
            f'其下为指标 3（这些单元的 token）与所需单元下限；最后一行为指标 1（各表格证据组的表格 chunk 名次，组号:名次）。'
            f'绿色为该题各组合中指标 2 最小的。</p>'
            f'<div class="scroll" data-grid><table><tr><th>表格题</th>{head}</tr>{"".join(rows)}</table></div></div>')


def curve(runs: dict, exclude: bool) -> str:
    ks = [1, 2, 3, 4, 5, 6, 8, 10, 15, 20]
    head = "".join(f"<th class=num>K={k}</th>" for k in ks)
    rows = []
    for key, run in runs.items():
        questions = view(run, exclude)
        points = {p["k"]: p for p in run["table_evidence"]["curve"]}
        cells = "".join(f"<td class=num>{sum(q['k'] is not None and q['k'] <= k for q in questions.values())} / {len(questions)}"
                        f"<div class=meta>{points[k]['complete']} / {run['table_evidence']['answerable']} · "
                        f"{points[k]['tokens'] / run['table_evidence']['answerable']:,.0f}</div></td>" for k in ks)
        rows.append(f"<tr><td class=nw><b>{esc(key)}</b></td>{cells}</tr>")
    return (f'<div class="card"><h3>K–覆盖–token 曲线</h3><p class="meta">每格第一行：表格证据在前 K 个返回单元内全部覆盖的表格题 / 表格题数；'
            f'第二行：全部 24 题中完整覆盖（所有证据组）的题 / 24 · 24 题前 K 个返回单元的平均 token。</p>'
            f'<div class="scroll" data-grid><table><tr><th>组合</th>{head}</tr>{"".join(rows)}</table></div></div>')


def excluded_card() -> str:
    rows = "".join(f"<tr><td class=nw>{esc(c)} {esc(g)}</td><td>{esc(why)}</td></tr>" for (c, g), why in EXCLUDED.items())
    data = json.loads((HERE / "unrelated" / "subquestions.json").read_text(encoding="utf-8"))
    probes = {}
    for r in data:
        probes.setdefault((r["case_id"], r["group_id"], r["subquestion"]), []).append(r)
    trs = []
    for (cid, gid, sub), items in probes.items():
        cells = "".join(f"<td class=num>{r['original']['chunk_rank']} → {r['sub']['chunk_rank']}</td>" for r in items)
        trs.append(f"<tr><td class=nw>{esc(cid)} {esc(gid)}</td><td>{esc(sub)}<div class=meta>{esc(items[0]['reason'])}</div></td>{cells}</tr>")
    head = "".join(f"<th class=num>{esc(r['config'])}</th>" for r in next(iter(probes.values())))
    return (f'<div class="card"><h3>排除的表格证据组</h3><p>判据：同一索引、同一证据组，只把原问题换成只问这一个信息项的子问题。'
            f'子问题在 7 种入库方式下都排得上来、原问题排不上来，原因在问题（复合问题 C4）；7 种入库方式无论哪种问法结果都相近、'
            f'都排不上来，原因在表格之外。两种情况都与表格怎么入库无关，第二个视图把它们排除后再算指标 1–3。</p>'
            f'<div class="scroll" data-grid><table><tr><th>证据组</th><th>排除原因</th></tr>{rows}</table></div>'
            f'<h4>对照实验：表格 chunk 名次（指标 1），原问题 → 子问题</h4>'
            f'<div class="scroll" data-grid><table><tr><th>证据组</th><th>子问题</th>{head}</tr>{"".join(trs)}</table></div>'
            f'<p class="meta">未排除的：CONTRACT-105-Q003 e1、PO-24-12-Q004 e1 的子问题与原问题名次相同，名次随入库方式变化，与入库有关；'
            f'PO-25-19 的单价（e2）需要跨表连接（C5），但名次随入库方式变化很大（原问题下 S 第 2、RL 第 12），保留；'
            f'总金额（e4）的表格 chunk 都排第 1，所需单元数由“需要单价与数量两张表”决定，保留。'
            f'数据：reports/experiments/v3.13-llm-tables/unrelated/subquestions.json。</p></div>')


def findings() -> str:
    if not FINDINGS:
        return ""
    items = "".join(f"<li>{badge(kind, 'bad' if kind == '问题' else 'ok')} {esc(text)}</li>" for kind, text in FINDINGS)
    return f'<div class="card"><h3>结论</h3><ul>{items}</ul></div>'


def definitions(runs: dict) -> str:
    te = next(iter(runs.values()))["table_evidence"]
    return (f'<div class="card"><h3>指标怎么读</h3><ul>'
            f'<li><b>表格证据组、表格题</b>：词坐标全部落在解析器给出的表格框内的 excerpt 为表格 excerpt（{te["excerpts"]} 段）；'
            f'含表格 excerpt 的证据组为表格证据组（{te["groups"]} 组），含表格证据组的题为表格题（{te["questions"]} 题）。</li>'
            f'<li><b>指标 1 表格 chunk 名次</b>（每个表格证据组）：全量 chunk 排名中（换成整表之前），覆盖该组表格 excerpt 任一词的 chunk 里'
            f'最靠前者的名次。只看“最容易找到的一块”。</li>'
            f'<li><b>指标 2 表格证据所需返回单元数</b>（每道表格题）：在全量返回单元排名中，覆盖该题全部表格证据（只算表格 excerpt）'
            f'需要前几个单元；题内取各表格证据组的最大值。K=3 时只看前 3 个单元，所以“≤ 3”的题就是默认设置下表格证据能完整返回的题。</li>'
            f'<li><b>指标 3 表格证据所需 token</b>（每道表格题）：上述前“指标 2”个返回单元的 token 之和。</li>'
            f'<li><b>所需单元下限</b>（每道表格题）：与排名无关，覆盖表格证据至少要几个返回单元（整表算一个）。</li>'
            f'<li><b>平均与中位数</b>：平均反映整体水平，但会被个别很差的题拉高（如 CONTRACT-105-Q003 需要约 20 个单元）；'
            f'中位数不受个别题影响；“≤ 3 的题”回答“默认设置下能完整返回几题”。三者一起看。</li></ul></div>')


def main(date: str) -> None:
    runs = load(date)
    tabs = ('<div class="tabs"><a href="#all" data-tab="all">全部表格证据组</a>'
            '<a href="#kept" data-tab="kept">排除与表格入库无关的证据组后</a></div>')
    panels = "".join(
        f'<section data-panel="{key}"{" class=hidden" if exclude else ""}>'
        + (excluded_card() if exclude else "")
        + overview(runs, exclude) + dominance(runs, exclude) + contrasts(runs, exclude)
        + per_question(runs, exclude) + curve(runs, exclude) + "</section>"
        for key, exclude in (("all", False), ("kept", True)))
    body = (f'<h1>V3.13 对比：LLM 结构标注、引导句、表格摘要</h1>'
            f'<div class="sub">7 种入库方式 × 返回命中 chunk（C）/ 整表（T），共 13 种组合（摘要只有 T）；混合检索，K=3，512 token；'
            f'LLM 为 google/gemini-3.8-flash。代号：RM 规则 Markdown，RL 规则表头映射，LM / LL LLM 结构标注 + Markdown / 表头映射，'
            f'“+”加引导句，S 摘要。</div>'
            f'{findings()}{definitions(runs)}{tabs}{panels}')
    write_assets(ROOT / "reports")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "index.html").write_text(page("V3.13 对比", body, root="../../",
                                         crumbs=[("报告首页", "../../index.html")]), encoding="utf-8")
    print(OUT / "index.html")


if __name__ == "__main__":
    main(sys.argv[1])
