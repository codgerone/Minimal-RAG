"""Table evidence tabs of a comparison page: metrics 1–3 of every run side by side.

Shown when the runs record table evidence (V3.13 on). A table spec, given with `compare
--table-spec`, may add contrasts (pairs of runs that differ in one factor), table evidence groups
left out of a second view, and notes; definitions are in docs/rules/evaluation.md.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from statistics import mean, median
from typing import Any

from rag.reports.html import badge, esc, pct

CURVE_KS = (1, 2, 3, 4, 5, 6, 8, 10, 15, 20)


@dataclass(frozen=True)
class TableSpec:
    pairs: tuple[tuple[str, str, str], ...] = ()                 # (contrast, before, after) by label
    excluded: dict[tuple[str, str], str] = field(default_factory=dict)   # (case, group) -> reason
    notes: tuple[tuple[str, str], ...] = ()                     # (kind, text)

    @classmethod
    def from_json(cls, data: dict[str, Any] | None) -> "TableSpec":
        data = data or {}
        return cls(tuple(tuple(p) for p in data.get("pairs", ())),
                   {(c, g): why for c, g, why in data.get("excluded", ())},
                   tuple(tuple(n) for n in data.get("notes", ())))


def label(result: dict[str, Any]) -> str:
    return result.get("display_name") or result["config"] + (" · 整表" if result.get("table_return_unit") == "table" else "")


def _questions(run: dict[str, Any], excluded: dict) -> dict[str, dict[str, Any]]:
    """Per table question: its kept groups, metric 2 (k), metric 3 (tokens) and the unit lower bound."""
    out = {}
    for case in run["cases"]:
        table = case.get("table")
        if not table:
            continue
        groups = [g for g in table["groups"] if (case["case_id"], g["group_id"]) not in excluded]
        if not groups:
            continue
        ks = [g["required_k"] for g in groups]
        k = None if None in ks else max(ks)
        lower = min(len(set().union(*combo)) for combo in
                    itertools.product(*[[set(s) for s in g["unit_sets"]] for g in groups]))
        out[case["case_id"]] = {"groups": groups, "k": k, "lower": lower,
                                "tokens": sum(table["leading_tokens"][:k]) if k else None}
    return out


def _stats(questions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ranks = [g["chunk_rank"] for q in questions.values() for g in q["groups"]]
    found = [r for r in ranks if r]
    ks = [q["k"] for q in questions.values() if q["k"] is not None]
    tokens = [q["tokens"] for q in questions.values() if q["tokens"] is not None]
    return {"questions": len(questions), "groups": len(ranks),
            "m1_mean": mean(found) if found else None, "m1_median": median(found) if found else None,
            "m1_first": sum(r == 1 for r in ranks), "m1_top3": sum(r is not None and r <= 3 for r in ranks),
            "m2_mean": mean(ks) if ks else None, "m2_median": median(ks) if ks else None,
            "m2_k3": sum(k <= 3 for k in ks), "m2_never": len(questions) - len(ks),
            "m3_mean": mean(tokens) if tokens else None, "m3_median": median(tokens) if tokens else None,
            "lower_mean": mean(q["lower"] for q in questions.values()) if questions else None}


def per_table_cost(run: dict[str, Any]) -> dict[str, float] | None:
    """LLM cost of one ingest divided by the tables processed and by the calls made; None without LLM."""
    cost = run.get("ingest_cost")
    if not cost or not cost.get("tables"):
        return None
    tables, calls = cost["tables"], cost["calls"] or 1
    return {"usd": cost["cost_usd"] / tables, "seconds": cost["seconds"] / tables, "calls": cost["calls"] / tables,
            "usd_call": cost["cost_usd"] / calls, "seconds_call": cost["seconds"] / calls,
            "degraded": cost["degraded_tables"], "tables": tables}


def _num(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:,.{digits}f}"


def _overview(runs: list[tuple[str, dict]], views: dict[str, dict]) -> str:
    first = _stats(next(iter(views.values())))
    q, g = first["questions"], first["groups"]
    rows = []
    for _, run in runs:
        s, c, m = _stats(views[label(run)]), per_table_cost(run), run["metrics"]
        n = m["hit_rate"]["denominator"]
        cost = (f"<td class=num>${c['usd']:.4f}</td><td class=num>{c['seconds']:.1f}</td><td class=num>{c['calls']:.2f}</td>"
                f"<td class=num>${c['usd_call']:.4f} · {c['seconds_call']:.1f} 秒</td>"
                f"<td class=num>{c['degraded']} / {c['tables']}</td>") if c else "<td class=num colspan=5>不调用 LLM</td>"
        rows.append(
            f"<tr><td class=nw><b>{esc(label(run))}</b></td>"
            f"<td class=num>{_num(s['m1_mean'])}</td><td class=num>{_num(s['m1_median'], 1)}</td>"
            f"<td class=num>{s['m1_first']} / {g}</td><td class=num>{s['m1_top3']} / {g}</td>"
            f"<td class=num>{_num(s['m2_mean'])}</td><td class=num>{_num(s['m2_median'], 1)}</td>"
            f"<td class=num>{s['m2_k3']} / {q}</td>"
            f"<td class=num>{_num(s['m3_mean'], 0)}</td><td class=num>{_num(s['m3_median'], 0)}</td>"
            f"<td class=num>{_num(s['lower_mean'])}</td>"
            f"<td class=num>{m['hit_rate']['numerator']:.0f} / {n}</td>"
            f"<td class=num>{m['complete_coverage']['numerator']:.0f} / {n}</td>"
            f"<td class=num>{pct(m['mrr']['value'])}</td><td class=num>{run['returned_tokens']['value']:.0f}</td>{cost}</tr>")
    never = sum(_stats(v)["m2_never"] for v in views.values())
    note = (f'<p class="meta">有 {never} 处表格题在全量排名中始终覆盖不全，不计入指标 2、3 的平均。</p>' if never else "")
    return (f'<div class="card"><h3>总表</h3>{note}<div class="scroll" data-grid><table>'
            f'<tr><th rowspan=2>组合</th><th colspan=4>指标 1：表格 chunk 名次<br><span class=meta>每个表格证据组一个值，共 {g} 组</span></th>'
            f'<th colspan=3>指标 2：表格证据所需返回单元数<br><span class=meta>每道表格题一个值，共 {q} 题</span></th>'
            f'<th colspan=2>指标 3：表格证据所需 token<br><span class=meta>每道表格题一个值，共 {q} 题</span></th>'
            f'<th>所需单元下限<br><span class=meta>每道表格题</span></th>'
            f'<th colspan=4>护栏：全部题，K={runs[0][1]["top_k"]}</th>'
            f'<th colspan=5>入库成本：每张表平均（一次入库）</th></tr>'
            f'<tr><th class=num>平均名次</th><th class=num>中位数</th><th class=num>排第 1 的组</th><th class=num>排进前 3 的组</th>'
            f'<th class=num>平均</th><th class=num>中位数</th><th class=num>所需单元数 ≤ 3 的题</th>'
            f'<th class=num>平均</th><th class=num>中位数</th><th class=num>平均</th>'
            f'<th class=num>命中的题</th><th class=num>完整覆盖的题</th><th class=num>MRR</th><th class=num>每题平均返回 token</th>'
            f'<th class=num>LLM 费用</th><th class=num>LLM 耗时（秒）</th><th class=num>调用次数（含重试）</th>'
            f'<th class=num>每次调用</th><th class=num>退回规则的表</th></tr>'
            f'{"".join(rows)}</table></div></div>')


def _dominance(runs: list[tuple[str, dict]], views: dict[str, dict]) -> str:
    dims = {}
    for _, run in runs:
        s, c = _stats(views[label(run)]), per_table_cost(run)
        dims[label(run)] = (s["m2_mean"] or 99.0, s["m3_mean"] or 1e9, round(c["usd"], 5) if c else 0.0)
    rows = []
    for key, d in dims.items():
        by = [o for o, e in dims.items() if o != key and all(x <= y for x, y in zip(e, d)) and e != d]
        verdict = badge("被支配", "bad") + " " + esc("、".join(by)) if by else badge("未被支配，保留", "ok")
        rows.append(f"<tr><td class=nw><b>{esc(key)}</b></td><td class=num>{d[0]:.2f}</td><td class=num>{d[1]:,.0f}</td>"
                    f"<td class=num>${d[2]:.4f}</td><td>{verdict}</td></tr>")
    return (f'<div class="card"><h3>支配判定</h3>'
            f'<p>目的：先筛掉“有另一个组合在三个维度上都不比它差、至少一个维度更好”的组合，剩下的组合之间才需要按取舍来选。'
            f'每个组合与其余全部组合逐一比较。三个维度都越小越好：指标 2 的平均、指标 3 的平均、每张表的 LLM 费用。'
            f'若 B 在三个维度上都不比 A 大、且至少一个维度更小，则 A 被 B 支配，“判定”列列出支配 A 的全部组合。</p>'
            f'<p class="meta">只比数值大小，不考虑差异是否超出随机扰动，也不看护栏；“被支配”只说明在这三个维度上不占优。</p>'
            f'<div class="scroll" data-grid><table>'
            f'<tr><th>组合</th><th class=num>指标 2 平均</th><th class=num>指标 3 平均</th>'
            f'<th class=num>每张表 LLM 费用</th><th>判定</th></tr>{"".join(rows)}</table></div></div>')


def contrast_counts(before: dict[str, dict], after: dict[str, dict]) -> dict[str, Any]:
    """Per table question, which way each metric moved; see docs/rules/evaluation.md."""
    m1 = {"better": 0, "worse": 0, "mixed": 0, "floor": 0}
    m2 = {"better": 0, "worse": 0}
    m3 = {"better": 0, "worse": 0, "same_k": 0}
    for cid, a in before.items():
        b = after[cid]
        ra = {g["group_id"]: g["chunk_rank"] or 10 ** 6 for g in a["groups"]}
        rb = {g["group_id"]: g["chunk_rank"] or 10 ** 6 for g in b["groups"]}
        up, down = any(rb[k] < ra[k] for k in ra), any(rb[k] > ra[k] for k in ra)
        if up and down:
            m1["mixed"] += 1
        elif up:
            m1["better"] += 1
        elif down:
            m1["worse"] += 1
        m1["floor"] += all(r == 1 for r in ra.values())
        ka, kb = a["k"] or 10 ** 6, b["k"] or 10 ** 6
        m2["better"] += kb < ka
        m2["worse"] += kb > ka
        ta, tb = a["tokens"] or 10 ** 9, b["tokens"] or 10 ** 9
        m3["better"] += tb < ta
        m3["worse"] += tb > ta
        m3["same_k"] += tb != ta and ka == kb
    return {"m1": m1, "m2": m2, "m3": m3, "questions": len(before)}


def _contrasts(spec: TableSpec, runs: list[tuple[str, dict]], views: dict[str, dict]) -> str:
    known = {label(r) for _, r in runs}
    cards: dict[str, list[str]] = {}
    for name, before, after in spec.pairs:
        if before not in known or after not in known:
            continue
        c = contrast_counts(views[before], views[after])
        sa, sb = _stats(views[before]), _stats(views[after])

        def moved(better: int, worse: int) -> str:
            return ((badge(f"变好 {better}", "ok") if better else "变好 0") + " "
                    + (badge(f"变差 {worse}", "bad") if worse else "变差 0"))
        n = c["questions"]
        cards.setdefault(name, []).append(
            f"<tr><td class=nw>{esc(before)} → {esc(after)}</td>"
            f"<td class=nw>{moved(c['m1']['better'], c['m1']['worse'])} 有好有坏 {c['m1']['mixed']} "
            f"<span class=meta>/ {n} · 其中只有 {n - c['m1']['floor']} 题可能变好</span></td>"
            f"<td class=nw>{moved(c['m2']['better'], c['m2']['worse'])} <span class=meta>/ {n}</span></td>"
            f"<td class=nw>{moved(c['m3']['better'], c['m3']['worse'])} <span class=meta>/ {n} · "
            f"其中 {c['m3']['same_k']} 题排名没变、只是返回的单元变长或变短</span></td>"
            f"<td class=num>{_num(sa['m2_mean'])} → {_num(sb['m2_mean'])}</td>"
            f"<td class=num>{_num(sa['m3_mean'], 0)} → {_num(sb['m3_mean'], 0)}</td></tr>")
    if not cards:
        return ""
    body = "".join(
        f'<div class="card"><h3>{esc(name)}</h3><div class="scroll" data-grid><table>'
        f'<tr><th>对照（前 → 后）</th><th>指标 1：表格 chunk 名次</th><th>指标 2：所需返回单元数</th>'
        f'<th>指标 3：所需 token</th><th class=num>指标 2 平均</th><th class=num>指标 3 平均</th></tr>'
        f'{"".join(rows)}</table></div></div>' for name, rows in cards.items())
    return ('<h2>对照</h2><p class="meta">每组对照只差一个因素；计数单位都是表格题（“/ N”）。指标 1：一题中有表格证据组名次变小、'
            '没有变大的为变好，反之为变差，两者都有为有好有坏。比较前表格证据组已全部排第 1 的题只可能不变或变差，'
            '所以“可能变好”的题少于 N，变差不受此限。指标 2、3：所需单元数或 token 变小为变好。指标 3 的变化有两个来源：'
            '排名变了（所需单元数随之变化），或排名没变、返回的单元本身变长或变短（如每块多了引导句）；后者单独计数。'
            '判断以平均值为主，计数为辅。</p>' + body)


def _per_question(runs: list[tuple[str, dict]], views: dict[str, dict]) -> str:
    keys = [label(r) for _, r in runs]
    ids = list(views[keys[0]])
    rows = []
    for cid in ids:
        best = min((views[k][cid]["k"] or 10 ** 6) for k in keys)
        cells = []
        for key in keys:
            q = views[key][cid]
            ranks = "、".join(f"{g['group_id']}:{g['chunk_rank'] or '—'}" for g in q["groups"])
            mark = ' style="background:var(--ok-bg)"' if (q["k"] or 10 ** 6) == best else ""
            cells.append(f"<td class=num{mark}><b>{q['k'] if q['k'] is not None else '—'}</b>"
                         f"<div class=meta>{q['tokens'] or '—'} token · 下限 {q['lower']}</div>"
                         f"<div class=meta>名次 {ranks}</div></td>")
        rows.append(f"<tr><td class=nw>{esc(cid)}</td>{''.join(cells)}</tr>")
    head = "".join(f"<th class=num>{esc(k)}</th>" for k in keys)
    return (f'<div class="card"><h3>表格题逐题</h3><p class="meta">每格：粗体为指标 2；其下为指标 3 与所需单元下限；'
            f'最后一行为指标 1（组号:名次）。绿色为该题各组合中指标 2 最小的。</p>'
            f'<div class="scroll" data-grid><table><tr><th>表格题</th>{head}</tr>{"".join(rows)}</table></div></div>')


def _curve(runs: list[tuple[str, dict]], views: dict[str, dict]) -> str:
    rows = []
    for _, run in runs:
        questions = views[label(run)]
        te = run["table_evidence"]
        points = {p["k"]: p for p in te["curve"]}
        cells = "".join(
            f"<td class=num>{sum(q['k'] is not None and q['k'] <= k for q in questions.values())} / {len(questions)}"
            f"<div class=meta>{points[k]['complete']} / {te['answerable']} · {points[k]['tokens'] / te['answerable']:,.0f}</div></td>"
            for k in CURVE_KS if k in points)
        rows.append(f"<tr><td class=nw><b>{esc(label(run))}</b></td>{cells}</tr>")
    head = "".join(f"<th class=num>K={k}</th>" for k in CURVE_KS)
    return (f'<div class="card"><h3>K–覆盖–token 曲线</h3><p class="meta">每格第一行：表格证据在前 K 个返回单元内全部覆盖的表格题 / '
            f'表格题数；第二行：全部可回答题中完整覆盖的题 / 可回答题数 · 每题前 K 个返回单元的平均 token。</p>'
            f'<div class="scroll" data-grid><table><tr><th>组合</th>{head}</tr>{"".join(rows)}</table></div></div>')


def _excluded(spec: TableSpec) -> str:
    rows = "".join(f"<tr><td class=nw>{esc(c)} {esc(g)}</td><td>{esc(why)}</td></tr>" for (c, g), why in spec.excluded.items())
    return (f'<div class="card"><h3>排除的表格证据组</h3><p class="meta">与表格怎么入库无关的证据组，这一视图不计入指标 1–3。</p>'
            f'<div class="scroll" data-grid><table><tr><th>证据组</th><th>排除原因</th></tr>{rows}</table></div></div>')


def _definitions(run: dict[str, Any]) -> str:
    te = run["table_evidence"]
    return (f'<div class="card"><h3>指标怎么读</h3><ul>'
            f'<li><b>表格证据组、表格题</b>：词坐标全部落在解析器给出的表格框内的 excerpt 为表格 excerpt（{te["excerpts"]} 段）；'
            f'含表格 excerpt 的证据组为表格证据组（{te["groups"]} 组），含表格证据组的题为表格题（{te["questions"]} 题）。</li>'
            f'<li><b>指标 1 表格 chunk 名次</b>（每个表格证据组）：全量 chunk 排名中（换成整表之前），覆盖该组表格 excerpt 任一词的 '
            f'chunk 里最靠前者的名次。</li>'
            f'<li><b>指标 2 所需返回单元数</b>（每道表格题）：全量返回单元排名中，覆盖该题全部表格 excerpt 需要前几个单元；'
            f'题内取各表格证据组的最大值。“≤ 3 的题”是 K=3 下表格证据能完整返回的题。</li>'
            f'<li><b>指标 3 所需 token</b>（每道表格题）：前“指标 2”个返回单元的 token 之和。</li>'
            f'<li><b>所需单元下限</b>（每道表格题）：与排名无关，覆盖表格证据至少要几个返回单元（整表算一个）。</li>'
            f'<li><b>平均与中位数</b>：平均会被个别很差的题拉高，中位数不受影响；三者一起看。</li></ul></div>')


def table_tabs(runs: list[tuple[str, dict[str, Any]]], spec: TableSpec) -> list[tuple[str, str, str]]:
    """(tab id, tab title, panel html) for the table evidence views; empty when no run records them."""
    if not all(r.get("table_evidence") for _, r in runs):
        return []
    notes = "".join(f"<li>{badge(kind, 'bad' if kind == '问题' else 'ok')} {esc(text)}</li>" for kind, text in spec.notes)
    head = (f'<div class="card"><h3>结论</h3><ul>{notes}</ul></div>' if notes else "") + _definitions(runs[0][1])
    tabs = []
    for key, title, excluded in (("table-all", "表格证据", {}),
                                 ("table-kept", f"表格证据（排除 {len(spec.excluded)} 组）", spec.excluded)):
        if key == "table-kept" and not excluded:
            continue
        views = {label(r): _questions(r, excluded) for _, r in runs}
        panel = ((head if key == "table-all" else _excluded(spec)) + _overview(runs, views) + _dominance(runs, views)
                 + _contrasts(spec, runs, views) + _per_question(runs, views) + _curve(runs, views))
        tabs.append((key, title, panel))
    return tabs
