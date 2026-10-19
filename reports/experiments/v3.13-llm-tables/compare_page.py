"""V3.13 对比页：7 种入库方式 × 2 种返回方式（去掉 S-C）共 13 种组合，按提案 1.2 的对照与 2.9 的指标。

用法（仓库根目录）：python reports/experiments/v3.13-llm-tables/compare_page.py <日期> [V3.12 基线评估目录名]
读取 reports/eval/<日期>_<配置>_k3_filter-on_hybrid[_table]/result.json，写入 reports/compare/v3.13-llm-tables/。
"""
import json
import sys
from pathlib import Path

from rag.reports.html import badge, esc, page, pct, write_assets

ROOT = Path(__file__).resolve().parents[3]
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
    ("返回单元", "X-C → X-T", [(f"{m}-C", f"{m}-T") for m, _, _ in METHODS if m != "S"]),
    ("写法", "Markdown → 表头映射", [(f"{a}-{u}", f"{b}-{u}") for a, b in (("RM", "RL"), ("LM", "LL"), ("LM+", "LL+"))
                                  for u in "CT"]),
    ("规则 vs LLM", "规则 → LLM", [(f"{a}-{u}", f"{b}-{u}") for a, b in (("RM", "LM"), ("RL", "LL")) for u in "CT"]),
    ("引导句", "不加 → 加", [(f"{a}-{u}", f"{b}-{u}") for a, b in (("LM", "LM+"), ("LL", "LL+")) for u in "CT"]),
    ("摘要", "X-T → S-T", [(f"{m}-T", "S-T") for m, _, _ in METHODS if m != "S"]),
]
FINDINGS = [  # 由助手查看结果后写入（2026-10-19）
    ("结论", "支配判定后只剩 RM-T 与 LM+-T：RM-T 零成本；LM+-T 表格证据所需 K 合计 40（RM-T 43）、所需 token 16,774（18,001），"
     "入库多花 $0.025 / 12 张表。其余 11 种组合都被支配。差距小，逐题差异集中在 5 道题（PO-25-19-Q001–Q003、PO-24-12-Q004、"
     "CONTRACT-105-Q010），每种 LLM 方式只跑了一次，按提案不下“哪个更好”的结论，只列取舍。"),
    ("结论", "返回单元：每种入库方式都是整表（T）优于命中 chunk（C），所需 K 合计 54→43（RM）、70→44（RL）、60→44（LM），"
     "所需 token 也更少；代价是 24 题 K=3 时每题返回 token 多约 120–150。"),
    ("结论", "写法：C 模式下 Markdown 的表格 chunk 名次更好（规则 83 对 108，差在 CONTRACT-105-Q010 与 PO-25-19）；"
     "T 模式下两种写法几乎相同（整表文本相同，差异只来自排名）。"),
    ("问题", "规则 vs LLM：不加引导句时，LLM 结构标注没有带来稳定收益。RM→LM 所需 K 合计 C 54→60、T 43→44；"
     "RL→LL C 70→64、T 44→45。12 张表的结构标注全部正确，但现有表格中只有 PO 25-19 数量表有分节，"
     "其余都只是表头判定（与规则判定的结果相同），LLM 能改变的只有这一张表的切法。"),
    ("结论", "引导句：LM→LM+ 所需 K 合计 C 60→53、T 44→40；LL→LL+ C 64→64、T 45→41。变好集中在 PO-24-12-Q004（3→2）、"
     "PO-25-19-Q002（C 模式 6→3）、CONTRACT-105-Q010（LM-C 2→1）；变差 1 题：CONTRACT-105-Q002（1→2，原因未做对照实验）。"),
    ("结论", "摘要：S-T 的表格 chunk 名次最好（前 3 名 34/35 组，名次合计 66，其次 RM 83），所需 K 合计 42，"
     "与 LM+-T（40）、LL+-T（41）相当；入库花费 $0.045，被 LM+-T 支配。变差 1 题：E001-602-Q002（1→2）。"),
    ("结论", "护栏（K=3，24 题完整覆盖）：RL-T、LL-T、LM+-T、LL+-T、S-T 都是 20 题，RM-T、LM-T 18 题。RL-T 的命中与完整覆盖与 "
     "V3.12 相同（22 / 20 题），整表改存 Markdown 后每题返回 token 1,860 → 1,259。"),
    ("问题", "CONTRACT-105-Q003 在所有组合中都需要 20–22 个单元（第 5 页交货地点表的证据组排在第 20 名附近），约占所需 K 合计的一半，"
     "各组合同样受影响；去掉这题后所需 K 合计 RM-T 22、LM+-T 20、S-T 20、RL-T 24。"),
    ("结论", "入库成本：结构标注 12 张表一次调用全部通过（0 重试、0 降级），$0.025、52 秒，四种 LLM 写法共用；摘要 12 张表 $0.045、84 秒。"),
]


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


def scalars(run: dict) -> dict:
    """Totals over table questions: metric 1 summed over groups, metric 2 and 3 summed over questions."""
    cases = table_cases(run).values()
    m1 = [g["chunk_rank"] for c in cases for g in c["table"]["groups"]]
    m2 = [c["table"]["required_k"] for c in cases]
    m3 = [c["table"]["tokens"] for c in cases]
    return {"questions": len(m2), "groups": len(m1),
            "m1_top3": sum(r is not None and r <= 3 for r in m1), "m1_sum": sum(r or 0 for r in m1),
            "m2_k3": sum(k is not None and k <= 3 for k in m2), "m2_sum": sum(k or 0 for k in m2),
            "m2_missing": sum(k is None for k in m2), "m3_sum": sum(t or 0 for t in m3),
            "lower_sum": sum(c["table"]["fewest_units"] or 0 for c in cases)}


def cost(run: dict) -> dict:
    return run.get("ingest_cost") or {"cost_usd": 0.0, "seconds": 0.0, "retries": 0, "degraded_tables": 0, "calls": 0}


def overview(runs: dict) -> str:
    rows = []
    for key, run in runs.items():
        s, c, m = scalars(run), cost(run), run["metrics"]
        rows.append(
            f"<tr><td class=nw><b>{esc(key)}</b></td><td class=num>{s['m1_top3']}/{s['groups']}</td>"
            f"<td class=num>{s['m1_sum']}</td><td class=num>{s['m2_k3']}/{s['questions']}</td>"
            f"<td class=num>{s['m2_sum']}{'（' + str(s['m2_missing']) + ' 题未覆盖）' if s['m2_missing'] else ''}</td>"
            f"<td class=num>{s['m3_sum']:,}</td><td class=num>{s['lower_sum']}</td>"
            f"<td class=num>{m['hit_rate']['numerator']:.0f}/{m['hit_rate']['denominator']}</td>"
            f"<td class=num>{m['complete_coverage']['numerator']:.0f}/{m['complete_coverage']['denominator']}</td>"
            f"<td class=num>{pct(m['mrr']['value'])}</td><td class=num>{run['returned_tokens']['value']:.0f}</td>"
            f"<td class=num>${c['cost_usd']:.4f}</td><td class=num>{c['seconds']:.0f}</td>"
            f"<td class=num>{c['retries']}</td><td class=num>{c['degraded_tables']}</td></tr>")
    return (f'<div class="card"><h3>总表：13 种组合</h3><div class="scroll" data-grid><table>'
            f'<tr><th rowspan=2>组合</th><th colspan=2>指标 1 表格 chunk 名次</th><th colspan=3>指标 2、3 表格证据</th>'
            f'<th rowspan=2 class=num>所需单元<br>下限合计</th><th colspan=4>护栏（K=3，24 题）</th><th colspan=4>入库成本</th></tr>'
            f'<tr><th class=num>前 3 名的组</th><th class=num>名次合计</th><th class=num>K≤3 的题</th>'
            f'<th class=num>所需 K 合计</th><th class=num>所需 token 合计</th><th class=num>命中</th>'
            f'<th class=num>完整覆盖</th><th class=num>MRR</th><th class=num>每题 token</th>'
            f'<th class=num>LLM 花费</th><th class=num>调用秒数</th><th class=num>重试</th><th class=num>降级表</th></tr>'
            f'{"".join(rows)}</table></div>'
            f'<p class="meta">指标按表格题统计（表格证据组、表格题的定义见提案 2.9）。名次、所需 K、所需 token、下限都是越小越好；'
            f'指标 1 在返回单元映射之前，同一入库方式的 C、T 相同。入库成本只计 LLM 调用（缓存命中的调用按首次调用的耗时与花费记）。</p></div>')


def per_question(runs: dict) -> str:
    keys = list(runs)
    ids = list(table_cases(runs[keys[0]]))
    head = "".join(f"<th class=num>{esc(k)}</th>" for k in keys)
    rows = []
    for cid in ids:
        cells = []
        best = min((table_cases(r)[cid]["table"]["required_k"] or 99) for r in runs.values())
        for key in keys:
            t = table_cases(runs[key])[cid]["table"]
            m1 = "/".join(str(g["chunk_rank"]) for g in t["groups"])
            mark = ' style="background:var(--ok-bg)"' if (t["required_k"] or 99) == best else ""
            cells.append(f"<td class=num{mark}>{t['required_k'] if t['required_k'] is not None else '—'}"
                         f"<div class=meta>{t['tokens'] or '—'} tok · 名次 {m1} · 下限 {t['fewest_units']}</div></td>")
        rows.append(f"<tr><td class=nw>{esc(cid)}</td>{''.join(cells)}</tr>")
    return (f'<div class="card"><h3>表格题逐题：指标 2（所需单元数），下方为指标 3、指标 1（各表格证据组）、下限</h3>'
            f'<div class="scroll" data-grid><table><tr><th>题</th>{head}</tr>{"".join(rows)}</table></div>'
            f'<p class="meta">绿色为该题各组合中所需单元最少的。</p></div>')


def contrasts(runs: dict) -> str:
    cards = []
    for name, factor, pairs in CONTRASTS:
        rows = []
        for before, after in pairs:
            a, b = table_cases(runs[before]), table_cases(runs[after])
            counts = {}
            for metric in ("m1", "m2", "m3"):
                better = worse = 0
                for cid in a:
                    x, y = a[cid]["table"], b[cid]["table"]
                    if metric == "m1":
                        u, v = sum(g["chunk_rank"] or 99 for g in x["groups"]), sum(g["chunk_rank"] or 99 for g in y["groups"])
                    elif metric == "m2":
                        u, v = x["required_k"] or 99, y["required_k"] or 99
                    else:
                        u, v = x["tokens"] or 10 ** 6, y["tokens"] or 10 ** 6
                    better += v < u
                    worse += v > u
                counts[metric] = (better, worse)
            sa, sb = scalars(runs[before]), scalars(runs[after])
            cell = lambda m: f"<td class=num>{badge(f'↑{counts[m][0]}', 'ok') if counts[m][0] else '↑0'} {badge(f'↓{counts[m][1]}', 'bad') if counts[m][1] else '↓0'}</td>"
            rows.append(f"<tr><td class=nw>{esc(before)} → {esc(after)}</td>{cell('m1')}{cell('m2')}{cell('m3')}"
                        f"<td class=num>{sa['m2_sum']} → {sb['m2_sum']}</td><td class=num>{sa['m3_sum']:,} → {sb['m3_sum']:,}</td></tr>")
        cards.append(f'<div class="card"><h3>{esc(name)}（{esc(factor)}）</h3><div class="scroll" data-grid><table>'
                     f'<tr><th>对照</th><th class=num>指标 1 变好/变差的题</th><th class=num>指标 2</th><th class=num>指标 3</th>'
                     f'<th class=num>所需 K 合计</th><th class=num>所需 token 合计</th></tr>{"".join(rows)}</table></div></div>')
    return "".join(cards)


def curve(runs: dict) -> str:
    ks = [1, 2, 3, 4, 5, 6, 8, 10, 15, 20]
    head = "".join(f"<th class=num>K={k}</th>" for k in ks)
    rows = []
    for key, run in runs.items():
        points = {p["k"]: p for p in run["table_evidence"]["curve"]}
        cells = "".join(f"<td class=num>{points[k]['table_complete']} / {points[k]['complete']}"
                        f"<div class=meta>{points[k]['tokens']:,}</div></td>" for k in ks)
        rows.append(f"<tr><td class=nw><b>{esc(key)}</b></td>{cells}</tr>")
    return (f'<div class="card"><h3>K–覆盖–token 曲线</h3><div class="scroll" data-grid><table>'
            f'<tr><th>组合</th>{head}</tr>{"".join(rows)}</table></div>'
            f'<p class="meta">每格：表格证据完整覆盖的表格题数 / 完整覆盖的题数（24 题），下方为 24 题前 K 个返回单元的 token 之和。</p></div>')


def dominance(runs: dict) -> str:
    dims = {k: (scalars(r)["m2_sum"], scalars(r)["m3_sum"], cost(r)["cost_usd"]) for k, r in runs.items()}
    rows = []
    for key, d in dims.items():
        by = [o for o, e in dims.items() if o != key and all(x <= y for x, y in zip(e, d)) and e != d]
        verdict = badge("被支配：" + "、".join(by), "bad") if by else badge("保留", "ok")
        rows.append(f"<tr><td class=nw><b>{esc(key)}</b></td><td class=num>{d[0]}</td><td class=num>{d[1]:,}</td>"
                    f"<td class=num>${d[2]:.4f}</td><td>{verdict}</td></tr>")
    return (f'<div class="card"><h3>支配判定</h3><div class="scroll" data-grid><table>'
            f'<tr><th>组合</th><th class=num>表格证据：所需 K 合计</th><th class=num>返回 token：所需 token 合计</th>'
            f'<th class=num>入库成本</th><th>判定</th></tr>{"".join(rows)}</table></div>'
            f'<p class="meta">三项都越小越好；另一组合三项都不差于它且至少一项更好，则被支配。</p></div>')


def findings() -> str:
    if not FINDINGS:
        return ""
    items = "".join(f"<li>{badge(kind, 'bad' if kind == '问题' else 'ok')} {esc(text)}</li>" for kind, text in FINDINGS)
    return f'<div class="card"><h3>结论</h3><ul>{items}</ul></div>'


def main(date: str) -> None:
    runs = load(date)
    first = next(iter(runs.values()))["table_evidence"]
    body = (f'<h1>V3.13 对比：LLM 结构标注、引导句、表格摘要</h1>'
            f'<div class="sub">7 种入库方式 × 返回命中 chunk（C）/ 整表（T），共 13 种组合；混合检索，K=3，512 token；'
            f'LLM 为 google/gemini-3.8-flash。表格题 {first["questions"]} 道（{first["groups"]} 个表格证据组，'
            f'{first["excerpts"]} 段表格 excerpt）。代号见提案 1.1。</div>'
            f'{findings()}{overview(runs)}{dominance(runs)}{contrasts(runs)}{per_question(runs)}{curve(runs)}')
    write_assets(ROOT / "reports")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "index.html").write_text(page("V3.13 对比", body, root="../../",
                                         crumbs=[("报告首页", "../../index.html")]), encoding="utf-8")
    print(OUT / "index.html")


if __name__ == "__main__":
    main(sys.argv[1])
