"""The multi-round evaluation page (reports/multi_round/NAME/): every metric of docs/rules/evaluation.md "多轮检索的评估",
computed from the stored facts (rag/eval/multi_round.py), per call limit.

At limit L a run made more than L − 1 planning calls is cut before planning call L: what was handed to the
LLM and accepted is the state after planning call L − 1, and the answering call made for L closes it.
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean
from typing import Any

from rag.eval.query_compare import completion_vector, verdict
from rag.jsonio import read_json, write_atomic
from rag.paths import Workspace
from rag.reports.html import esc, page, write_assets

TERMINATION = {"answered": "Q0 已回答", "limit": "达到调用上限", "nothing_pending": "没有待检索的问题"}
VERDICT = {"better": ("▲ 变好", "ok"), "worse": ("▼ 变差", "bad"), "mixed": ("◆ 各有优势", "warn"), "same": ("不变", "")}
MODES = (("first_q0", "Q0 第 1 次检索（去编码原句）"), ("first_derived", "派生问题第 1 次检索（原句 + 译文）"),
         ("continue", "往下取"), ("rewrite", "改写"), ("hyde", "HyDE"), ("step_back", "step-back"),
         ("translation", "补上翻译（只用于 Q0）"))
FALLBACK = ("continue", "rewrite", "hyde", "step_back", "translation")


# ---------------------------------------------------------------- facts at a limit

def completion(seq: list[list[str]], groups: list[dict[str, Any]]) -> list[int | None]:
    """Each group's completion rank in `seq` (lists of chunk ids), None when never complete."""
    first: dict[str, int] = {}
    for rank, chunks in enumerate(seq, start=1):
        for c in chunks:
            first.setdefault(c, rank)
    out = []
    for g in groups:
        reached = [max(first[c] for c in s) for s in g["sets"] if s and all(c in first for c in s)]
        out.append(min(reached) if reached else None)
    return out


def holds(chunks: list[str], group: dict[str, Any]) -> bool:
    return any(set(chunks) & set(s) for s in group["sets"])


def evidence_of(lst: list[dict[str, Any]]) -> set[str]:
    return {u for q in lst for u in q["evidence"]}


def outputs(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for c in calls:
        try:
            out.append(json.loads(c["output"]))
        except ValueError:
            out.append({})
    return out


def at_limit(case: dict[str, Any], run: dict[str, Any], limit: int, baselines: dict[str, dict]) -> dict[str, Any]:
    """Everything the page shows of one run at one limit."""
    cut = limit - 1 if run["plan_calls"] > limit - 1 else None
    seq = [u for u in run["order"] if cut is None or run["units"][u]["call"] <= cut]
    state = run["calls"][cut - 1]["list"] if cut else run["calls"][-1]["list"]
    if cut:
        own = run["limits"][str(limit)]
        calls = run["calls"][:cut] + [own["call"]]
        rounds = run["rounds"][:cut]
    else:
        own = {"answer": run["answer"], "termination": run["termination"]}
        calls, rounds = run["calls"], run["rounds"]
    accepted = evidence_of(state)
    q0 = next(q for q in state if q["id"] == "Q0")
    groups = case["groups"]
    chunks = [run["units"][u]["chunks"] for u in seq]
    n = completion(chunks, groups)
    n_acc = completion([run["units"][u]["chunks"] for u in seq if u in accepted], groups)
    tokens = sum(run["units"][u]["tokens"] for u in seq)
    answered = q0["status"] == "answered"
    out: dict[str, Any] = {
        "cut": cut, "seq": seq, "accepted": accepted, "state": state, "answered": answered,
        "termination": own["termination"], "answer": own["answer"], "tokens": tokens,
        "n": n, "round": [None if x is None else run["units"][seq[x - 1]]["call"] for x in n],
        "seq_cov": [x is not None for x in n], "acc_cov": [x is not None for x in n_acc],
        "calls": calls, "rounds": rounds,
        "plan": sum(c["kind"] == "plan" for c in calls), "answer_calls": sum(c["kind"] == "answer" for c in calls),
        "llm_seconds": sum(c["seconds"] for c in calls), "cost": sum(c["cost_usd"] or 0 for c in calls),
        "prompt_tokens": [c["prompt_tokens"] for c in calls],
        "retrievals": sum(r[0] for r in rounds), "retrieval_seconds": sum(r[1] for r in rounds),
        "outputs": outputs(calls[:cut] if cut else calls),
    }
    if not case["answerable"]:
        out["result"] = "none_answered" if answered else "correct_none"
        return out
    missing = [g["id"] for g, ok in zip(groups, out["acc_cov"]) if not ok]
    out["missing"] = missing
    out["rejected"] = [g["id"] for g, s, a in zip(groups, out["seq_cov"], out["acc_cov"]) if s and not a]
    out["result"] = ("complete" if answered and not missing else "misjudged" if answered else "unanswered")
    out["vs"] = {}
    for key, base in baselines.items():
        prefix, used = 0, 0
        for _, t in base["u"]:
            if used + t > tokens:
                break
            used += t
            prefix += 1
        cov = [x is not None and x <= prefix for x in base["n"]]
        out["vs"][key] = {"verdict": verdict(completion_vector(base["n"]), completion_vector(n)),
                          "base_n": base["n"], "prefix": prefix, "base_cov": cov}
    out["modes"] = mode_facts(run, seq, accepted, groups, n)
    return out


def mode_label(source: list) -> str:
    q, _, mode, _ = source
    return ("first_q0" if q == "Q0" else "first_derived") if mode == "first" else mode


def mode_facts(run, seq, accepted, groups, n) -> dict[str, dict[str, Any]]:
    """Per mode: units it handed first, the evidence units among them, those accepted, those continue did not
    hand in the same round, the items it touched and the items whose last piece it handed."""
    out = {m: {"units": 0, "evidence": 0, "accepted": 0, "beyond_continue": 0, "beyond_continue_accepted": 0,
               "items": set(), "completed": set()} for m, _ in MODES}
    by_round_continue = {}
    for u in seq:
        e = run["units"][u]
        if any(s[2] == "continue" for s in e["sources"]):
            by_round_continue.setdefault(e["call"], set()).add(u)
    last = {seq[x - 1] for x in n if x is not None}
    last_of = {}
    for g, x in zip(groups, n):
        if x is not None:
            last_of.setdefault(seq[x - 1], []).append(g["id"])
    for u in seq:
        e = run["units"][u]
        held = [g["id"] for g in groups if holds(e["chunks"], g)]
        for m in {mode_label(s) for s in e["sources"]}:
            f = out[m]
            f["units"] += 1
            if held:
                f["evidence"] += 1
                f["accepted"] += u in accepted
                if m in FALLBACK and m != "continue" and u not in by_round_continue.get(e["call"], set()):
                    f["beyond_continue"] += 1
                    f["beyond_continue_accepted"] += u in accepted
                f["items"].update(held)
            if u in last:
                f["completed"].update(last_of[u])
    return out


def evaluate(data: dict[str, Any]) -> dict[str, Any]:
    """case → generation → limit → facts."""
    top = data["settings"]["max_llm_calls"]
    limits = list(range(2, top + 1))
    out = {}
    for case in data["cases"]:
        out[case["case_id"]] = {}
        for g in data["generations"]:
            run = case["runs"].get(str(g))
            if run is None:
                continue
            bases = {k: v.get(str(g), v.get("0")) for k, v in case["baselines"].items()}
            out[case["case_id"]][g] = {L: at_limit(case, run, L, bases) for L in limits}
    return {"limits": limits, "facts": out}


# ---------------------------------------------------------------- summaries

def spread(values: list[float], fmt: str = "{:.1f}") -> str:
    if not values:
        return "—"
    lo, hi = min(values), max(values)
    if fmt == "{:.1f}" and all(float(v).is_integer() for v in values):
        fmt = "{:.0f}" if float(mean(values)).is_integer() else fmt
        lo, hi = int(lo), int(hi)
    text = fmt.format(mean(values))
    return text if lo == hi else f'{text} <span class="meta">({fmt.format(lo)}–{fmt.format(hi)})</span>'


def per_generation(data, ev, limit, fn, answerable=True) -> list[float]:
    """fn(case, facts) summed over the cases of each generation."""
    vals = []
    for g in data["generations"]:
        total = 0.0
        for case in data["cases"]:
            if case["answerable"] != answerable and answerable is not None:
                continue
            f = ev["facts"][case["case_id"]].get(g)
            if f is not None:
                total += fn(case, f[limit])
        vals.append(total)
    return vals


def summary_table(data, ev, limit) -> str:
    cases = [c for c in data["cases"] if c["answerable"]]
    N = len(cases)
    items = sum(len(c["groups"]) for c in cases)
    pg = lambda fn: per_generation(data, ev, limit, fn)
    rows = [
        ("Q0 判为可回答", pg(lambda c, f: f["answered"]), f"/ {N} 题"),
        ("　其中已接受的证据凑齐全部信息项", pg(lambda c, f: f["result"] == "complete"), f"/ {N} 题"),
        ("　<b class='down'>误判可回答</b>（判为可回答，但已接受的证据有信息项未凑齐）", pg(lambda c, f: f["result"] == "misjudged"), f"/ {N} 题"),
        ("Q0 未回答", pg(lambda c, f: f["result"] == "unanswered"), f"/ {N} 题"),
        ("交给 LLM 的材料完整覆盖（展示序列中全部信息项凑齐）", pg(lambda c, f: all(f["seq_cov"])), f"/ {N} 题"),
        ("已接受的证据完整覆盖", pg(lambda c, f: all(f["acc_cov"])), f"/ {N} 题"),
        ("交给 LLM 的材料中凑齐的信息项", pg(lambda c, f: sum(f["seq_cov"])), f"/ {items} 项"),
        ("已接受的证据中凑齐的信息项", pg(lambda c, f: sum(f["acc_cov"])), f"/ {items} 项"),
        ("证据被拒（交给过 LLM 却未接受而未凑齐）的信息项", pg(lambda c, f: len(f["rejected"])), f"/ {items} 项"),
    ]
    body = "".join(f'<tr><td>{label}</td><td class="num">{spread(v)}</td><td class="meta">{unit}</td></tr>' for label, v, unit in rows)
    tok = [t / N for t in pg(lambda c, f: f["tokens"])]
    body += f'<tr><td>每题交给 LLM 的 token（展示序列中各单元之和）</td><td class="num">{spread(tok, "{:.0f}")}</td><td class="meta">各题之和 ÷ {N}</td></tr>'
    single = ""
    for b in data["baselines"]:
        cc = pg(lambda c, f, k=b["key"]: all(f["vs"][k]["base_cov"]))
        ic = pg(lambda c, f, k=b["key"]: sum(f["vs"][k]["base_cov"]))
        single += (f'<tr><td>{esc(b["label"])}</td><td class="num">{spread(pg(lambda c, f: all(f["seq_cov"])))} / {spread(cc)}</td>'
                   f'<td class="num">{spread(pg(lambda c, f: sum(f["seq_cov"])))} / {spread(ic)}</td></tr>')
    return (f'<h3>结果</h3><table><tr><th>指标</th><th>多轮</th><th></th></tr>{body}</table>'
            f'<h3>与单轮比较：同样的返回 token</h3><p class="meta">单轮按每题多轮交给 LLM 的 token 截取：取排名前面累计 token 不超过它的单元。</p>'
            f'<table><tr><th>单轮基线</th><th>完整覆盖：多轮 / 单轮（题）</th><th>凑齐的信息项：多轮 / 单轮（/ {items}）</th></tr>{single}</table>')


def verdict_table(data, ev, limit) -> str:
    rows = ""
    for b in data["baselines"]:
        k = b["key"]
        counts = {v: per_generation(data, ev, limit, lambda c, f, v=v: f["vs"][k]["verdict"] == v) for v in VERDICT}
        net = [a - b_ for a, b_ in zip(counts["better"], counts["worse"])]
        stable_b = stable_w = flips = 0
        for case in data["cases"]:
            if not case["answerable"]:
                continue
            vs = [ev["facts"][case["case_id"]][g][limit]["vs"][k]["verdict"] for g in data["generations"]
                  if g in ev["facts"][case["case_id"]]]
            stable_b += all(v == "better" for v in vs)
            stable_w += all(v == "worse" for v in vs)
            flips += "better" in vs and "worse" in vs
        rows += (f'<tr><td>{esc(b["label"])}</td>' + "".join(f'<td class="num">{spread(counts[v])}</td>' for v in VERDICT)
                 + f'<td class="num"><b>{spread(net)}</b></td><td class="num">{stable_b}</td><td class="num">{stable_w}</td><td class="num">{flips}</td></tr>')
    return ('<h3>与单轮比较：凑齐向量的支配关系</h3><p class="meta">多轮的名次在展示序列上计算（未凑齐排在最后），单轮在全量排名上计算；'
            '判定为多轮相对单轮。题数为各次生成的平均（括号内为各次生成的范围）；稳定 = 全部生成中都变好（或都变差），翻转 = 有的生成变好、有的变差。</p>'
            '<table><tr><th>单轮基线</th><th>变好</th><th>变差</th><th>各有优势</th><th>不变</th><th>净值</th><th>稳定变好</th><th>稳定变差</th><th>翻转</th></tr>'
            + rows + '</table>')


def cost_table(data, ev, limit) -> str:
    runs = [(c, ev["facts"][c["case_id"]][g][limit]) for c in data["cases"] for g in data["generations"]
            if g in ev["facts"][c["case_id"]]]
    R = len(runs)
    calls = [f["plan"] + f["answer_calls"] for _, f in runs]
    prompt = [t for _, f in runs for t in f["prompt_tokens"]]
    cost = [f["cost"] for _, f in runs]
    secs = [f["llm_seconds"] for _, f in runs]
    rows = [
        ("每次运行的 LLM 调用", f'{mean(calls):.2f}（规划 {mean(f["plan"] for _, f in runs):.2f} + 作答 {mean(f["answer_calls"] for _, f in runs):.2f}）', f"最多 {max(calls)}"),
        ("每次运行的 LLM 耗时", f"{mean(secs):.1f} 秒", f"最长 {max(secs):.1f} 秒"),
        ("每次调用的输入 token", f"{mean(prompt):.0f}", f"最多 {max(prompt)}"),
        ("每次运行的检索", f'{mean(f["retrievals"] for _, f in runs):.1f} 次，{mean(f["retrieval_seconds"] for _, f in runs) * 1000:.0f} ms', ""),
        ("每次运行的 LLM 花费", f"${mean(cost):.4f}", f"最多 ${max(cost):.4f}"),
        ("全部运行的 LLM 花费", f"${sum(cost):.2f}", f"{R} 次运行（{len(data['cases'])} 题 × {len(data['generations'])} 次生成）"),
    ]
    base = ""
    for b in data["baselines"]:
        costs = [v["llm_cost_usd"] for c in data["cases"] for v in c["baselines"].get(b["key"], {}).values()]
        if costs:
            base += f'；{esc(b["label"])}每题 ${mean(costs):.4f}'
    return ('<h3>成本</h3><table><tr><th>项</th><th>平均</th><th></th></tr>'
            + "".join(f'<tr><td>{a}</td><td class="num">{b_}</td><td class="meta">{c}</td></tr>' for a, b_, c in rows)
            + f'</table><p class="meta">平均的分母：运行次数 {R}（输入 token 的分母为全部调用次数 {len(prompt)}）。花费为 OpenRouter 报告的计费金额；'
              f'命中缓存的调用记的是首次调用时的金额，即跑一次的花费。单轮基线的 LLM 花费{base or "：无"}。</p>')


def problem_lists(data, ev, limit) -> str:
    mis, rej = [], []
    for case in data["cases"]:
        for g, by in ev["facts"][case["case_id"]].items():
            f = by[limit]
            names = {x["id"]: x["item"] for x in case["groups"]}
            link = f'<a href="cases/{esc(case["case_id"])}.html">{esc(case["case_id"])}</a>'
            if f["result"] == "misjudged":
                what = "缺 " + "；".join(f'{esc(x)} {esc(names[x])}' for x in f["missing"])
                mis.append(f'<tr><td class="nw">{link}</td><td class="num">{g}</td><td>{what}</td></tr>')
            if case["answerable"] and f["rejected"]:
                rej.append(f'<tr><td class="nw">{link}</td><td class="num">{g}</td><td>{"；".join(esc(x) + " " + esc(names[x]) for x in f["rejected"])}</td>'
                           f'<td class="nw">{"已回答" if f["answered"] else "未回答"}</td></tr>')
    out = '<h3>误判可回答（逐条）</h3>' + (
        '<table><tr><th>题</th><th>生成</th><th>未凑齐的信息项</th></tr>' + "".join(mis) + '</table>' if mis else '<p>无。</p>')
    out += '<h3>证据被拒（逐条）</h3>' + (
        '<table><tr><th>题</th><th>生成</th><th>交给过 LLM、未被接受的信息项</th><th>Q0</th></tr>' + "".join(rej) + '</table>' if rej else '<p>无。</p>')
    return out


def item_cell(case, f, k) -> str:
    """Each item: round and rank it completed in the display sequence; the baseline's rank."""
    parts = []
    for g, n, r, acc, b in zip(case["groups"], f["n"], f["round"], f["acc_cov"], f["vs"][k]["base_n"]):
        mark = "ok" if acc else ("warn" if n is not None else "bad")
        parts.append(f'<span class="badge {mark}" title="{esc(g["item"])}">{esc(g["id"])} '
                     + (f'第 {r} 轮 · {n}' if n is not None else '未凑齐') + f' <span class="meta">单轮 {b if b is not None else "—"}</span></span>')
    return " ".join(parts)


RESULT = {"complete": ("已回答，证据凑齐", "ok"), "misjudged": ("误判可回答", "bad"), "unanswered": ("未回答", "warn"),
          "correct_none": ("未回答（无答案）", "ok"), "none_answered": ("判为可回答（无答案题，看答案是否说明没有）", "warn")}


def case_table(data, ev, limit) -> str:
    k = data["baselines"][0]["key"]
    rows = []
    for case in data["cases"]:
        if not case["answerable"]:
            continue
        cells = ""
        for g in data["generations"]:
            f = ev["facts"][case["case_id"]].get(g, {}).get(limit)
            if f is None:
                cells += "<td>—</td>"
                continue
            label, kind = RESULT[f["result"]]
            v, vk = VERDICT[f["vs"][k]["verdict"]]
            cells += (f'<td><span class="badge {kind}">{label}</span> <span class="badge {vk}">{v}</span>'
                      f'<div style="margin-top:4px">{item_cell(case, f, k)}</div>'
                      f'<div class="meta">{TERMINATION[f["termination"]]} · 调用 {f["plan"] + f["answer_calls"]} · {len(f["seq"])} 单元 / {f["tokens"]} token</div></td>')
        rows.append(f'<tr><td style="min-width:260px"><a href="cases/{esc(case["case_id"])}.html"><b>{esc(case["case_id"])}</b></a>'
                    f'<div>{esc(case["question"])}</div></td>{cells}</tr>')
    head = "".join(f"<th>第 {g} 次生成</th>" for g in data["generations"])
    return ('<p class="meta">每个信息项：绿 = 已接受的证据凑齐；黄 = 交给过 LLM 但已接受的证据未凑齐；红 = 交给 LLM 的材料中未凑齐。'
            '"第 n 轮 · m"：在第 n 轮检索中凑齐，展示序列名次 m；"单轮"为 V3.14 默认配置全量排名中的凑齐名次。变好 / 变差为相对 V3.14 默认配置。</p>'
            f'<div class="scroll"><table><tr><th>题</th>{head}</tr>' + "".join(rows) + '</table></div>')


def mode_table(data, ev, limit) -> str:
    keys = ("units", "evidence", "accepted", "beyond_continue", "beyond_continue_accepted")
    rows = ""
    for m, label in MODES:
        vals = {x: [] for x in keys + ("items", "completed")}
        for g in data["generations"]:
            acc = {x: 0 for x in keys + ("items", "completed")}
            for case in data["cases"]:
                if not case["answerable"] or g not in ev["facts"][case["case_id"]]:
                    continue
                f = ev["facts"][case["case_id"]][g][limit]["modes"][m]
                for x in keys:
                    acc[x] += f[x]
                acc["items"] += len(f["items"])
                acc["completed"] += len(f["completed"])
            for x in acc:
                vals[x].append(acc[x])
        beyond = (f'<td class="num">{spread(vals["beyond_continue"])}</td><td class="num">{spread(vals["beyond_continue_accepted"])}</td>'
                  if m in FALLBACK and m != "continue" else '<td class="meta">—</td><td class="meta">—</td>')
        rows += (f'<tr><td>{esc(label)}</td><td class="num">{spread(vals["units"])}</td><td class="num">{spread(vals["evidence"])}</td>'
                 f'<td class="num">{spread(vals["accepted"])}</td>{beyond}<td class="num">{spread(vals["items"])}</td><td class="num">{spread(vals["completed"])}</td></tr>')
    return ('<p class="meta">按单元第一次交给 LLM 时由哪种方式返回计（同一轮被几种方式返回的单元，各方式都计）；只计可回答题；数值为各次生成之和的平均（括号内为范围）。'
            '证据单元：含某信息项任一可接受 chunk 的单元。"往下取未交出"：同一轮往下取没有返回它，往下取是兜底的基线，只有这部分才算该方式的贡献。'
            '"凑齐信息项"：该信息项在展示序列中凑齐时的最后一块由它交出。</p>'
            '<div class="scroll"><table><tr><th>方式</th><th>交出的单元</th><th>其中证据单元</th><th>其中被接受</th><th>往下取未交出的证据单元</th><th>其中被接受</th>'
            '<th>涉及的信息项</th><th>凑齐信息项</th></tr>' + rows + '</table></div>')


def none_table(data, ev, limit) -> str:
    rows = ""
    for case in data["cases"]:
        if case["answerable"]:
            continue
        for g in data["generations"]:
            f = ev["facts"][case["case_id"]].get(g, {}).get(limit)
            if f is None:
                continue
            label, kind = RESULT[f["result"]]
            rows += (f'<tr><td class="nw"><a href="cases/{esc(case["case_id"])}.html">{esc(case["case_id"])}</a></td><td class="num">{g}</td>'
                     f'<td><span class="badge {kind}">{label}</span></td><td class="nw">{TERMINATION[f["termination"]]}</td>'
                     f'<td class="num">{f["plan"] + f["answer_calls"]}</td><td class="num">${f["cost"]:.4f}</td>'
                     f'<td><pre style="white-space:pre-wrap">{esc(f["answer"])}</pre></td></tr>')
    qs = "".join(f'<li><b>{esc(c["case_id"])}</b>：{esc(c["question"])}</li>' for c in data["cases"] if not c["answerable"])
    return (f'<ul>{qs}</ul><p class="meta">无答案题没有标注证据：Q0 判为可回答不一定是错，答案说明材料中没有即为正确；答案对不对本版不评估，请看答案。</p>'
            f'<table><tr><th>题</th><th>生成</th><th>结果</th><th>终止原因</th><th>LLM 调用</th><th>花费</th><th>答案</th></tr>{rows}</table>')


def withdrawals(data, ev, limit) -> str:
    rows = ""
    for case in data["cases"]:
        for g, by in ev["facts"][case["case_id"]].items():
            f = by[limit]
            texts = {q["id"]: q["text"] for q in f["state"]}
            parents = {q["id"]: q["parent"] for q in f["state"]}
            for n, out in enumerate(f["outputs"], start=1):
                for w in out.get("withdrawals", []) or []:
                    rows += (f'<tr><td class="nw"><a href="cases/{esc(case["case_id"])}.html">{esc(case["case_id"])}</a></td><td class="num">{g}</td>'
                             f'<td class="num">{n}</td><td class="nw">{esc(w["id"])} ← {esc(parents.get(w["id"]) or "")}</td>'
                             f'<td>{esc(texts.get(w["id"], ""))}</td><td>{esc(w.get("reason", ""))}</td></tr>')
                for j in out.get("judgements", []) or []:
                    if j.get("action") == "withdraw":
                        rows += (f'<tr><td class="nw"><a href="cases/{esc(case["case_id"])}.html">{esc(case["case_id"])}</a></td><td class="num">{g}</td>'
                                 f'<td class="num">{n}</td><td class="nw">{esc(j["id"])} ← {esc(parents.get(j["id"]) or "")}</td>'
                                 f'<td>{esc(texts.get(j["id"], ""))}</td><td>{esc(j.get("reason", ""))}</td></tr>')
    return ('<table><tr><th>题</th><th>生成</th><th>第几次调用</th><th>问题 ← 来源</th><th>问题</th><th>撤回理由</th></tr>' + rows + '</table>'
            if rows else '<p>无。</p>')


# ---------------------------------------------------------------- pages

def tabs(name: str, labels: list[str]) -> str:
    return (f'<div class="seg" data-tabs="{name}">' + "".join(
        f'<button type="button" data-v="{i}"{" class=on" if i == 0 else ""}>{esc(l)}</button>' for i, l in enumerate(labels)) + '</div>')


TAB_JS = ('<script>(function(){document.querySelectorAll("[data-tabs]").forEach(function(bar){var n=bar.dataset.tabs;'
          'var bs=bar.querySelectorAll("button");bs.forEach(function(b){b.addEventListener("click",function(){'
          'bs.forEach(function(x){x.classList.toggle("on",x===b)});document.querySelectorAll("[data-panel=\'"+n+"\']").forEach(function(p){'
          'p.classList.toggle("hidden",p.dataset.v!==b.dataset.v)})})})})})();</script>')


def render(data: dict[str, Any], ev: dict[str, Any]) -> str:
    limits = sorted(ev["limits"], reverse=True)
    s = data["settings"]
    panels = ""
    for i, L in enumerate(limits):
        panels += (f'<div data-panel="limit" data-v="{i}"{"" if i == 0 else " class=hidden"}>'
                   f'<div class="card"><h2>结论先看（上限 {L}）</h2>{summary_table(data, ev, L)}{verdict_table(data, ev, L)}{problem_lists(data, ev, L)}{cost_table(data, ev, L)}</div>'
                   f'<div class="card"><h2>逐题</h2>{case_table(data, ev, L)}</div>'
                   f'<div class="card"><h2>按方式的找回</h2>{mode_table(data, ev, L)}</div>'
                   f'<div class="card"><h2>无答案题</h2>{none_table(data, ev, L)}</div>'
                   f'<div class="card"><h2>撤回的问题</h2>{withdrawals(data, ev, L)}</div></div>')
    sched = " | ".join("、".join(g) for g in s["schedule"])
    answerable = sum(c["answerable"] for c in data["cases"])
    body = (f'<h1>V3.15 多轮检索评估：{esc(data["name"])}</h1>'
            f'<div class="sub">{esc(data["config"])}（返回{"整表" if data["table_return_unit"] == "table" else "命中的 chunk"}）· '
            f'{answerable} 道可回答题 + {len(data["cases"]) - answerable} 道无答案题 · 第 {"、".join(map(str, data["generations"]))} 次生成 · '
            f'兜底安排 {esc(sched)} · K = {s["units_per_attempt"]} · 模型 {esc(s["model"])} · 提示词 {esc(s["prompts"]["plan"])} / {esc(s["prompts"]["answer"])} · '
            f'数据集 {esc(data["dataset"]["version"])} · {esc(data["created_at"])}</div>'
            '<div class="card"><p>LLM 调用上限（含作答调用）：</p>' + tabs("limit", [f"上限 {L}" for L in limits]) +
            f'<p class="meta">上限 {limits[0]} 实际运行；更低的上限从同一次运行截取：在第 L 次规划调用之前截止，交给 LLM 的材料与已接受的证据取第 L − 1 次规划调用之后的状态，再为该档作答一次。'
            '"证据凑齐"按已接受的单元是否覆盖标注的信息项判定；答案文字对不对本版不评估。</p></div>'
            + panels + TAB_JS)
    return page(f"多轮检索 {data['name']}", body, root="../../", crumbs=[("报告首页", "../../index.html")])


def judgement_table(out: dict[str, Any]) -> str:
    rows = []
    for j in out.get("judgements", []) or []:
        retry = "".join(f'<div class="meta">{esc(m)}：' + "；".join(f'[{esc(x["language"])}] {esc(x["text"])}' for x in xs) + '</div>'
                        for m, xs in (j.get("retry") or {}).items() if xs)
        v = ('<span class="badge ok">可回答</span>' if j.get("answerable")
             else f'<span class="badge warn">{esc(j.get("gap"))}</span> → <b>{esc(j.get("action"))}</b>')
        rows.append(f'<tr><td class="nw"><b>{esc(j["id"])}</b></td><td class="nw">{v}</td><td class="nw">{esc("、".join(j.get("accept") or []) or "—")}</td>'
                    f'<td>{esc(j.get("note", ""))}{"<div class=meta>理由：" + esc(j["reason"]) + "</div>" if j.get("reason") else ""}{retry}</td></tr>')
    extra = ""
    if out.get("new_questions"):
        extra += "<p><b>新问题</b></p><ul>" + "".join(
            f'<li>{esc(n.get("parent"))} → {esc(n["text"])}' + "".join(f'<div class="meta">[{esc(t["language"])}] {esc(t["text"])}</div>' for t in n.get("translations", []))
            + "</li>" for n in out["new_questions"]) + "</ul>"
    if out.get("withdrawals"):
        extra += "<p><b>撤回</b>：" + "；".join(f'{esc(w["id"])}（{esc(w.get("reason", ""))}）' for w in out["withdrawals"]) + "</p>"
    if out.get("answer"):
        extra += f'<p><b>答案</b></p><pre style="white-space:pre-wrap">{esc(out["answer"])}</pre>'
    table = ('<table><tr><th>问题</th><th>判断</th><th>接受的单元</th><th>说明 / 兜底文本</th></tr>' + "".join(rows) + '</table>') if rows else ""
    return table + extra


def unit_line(u: str, info: dict[str, Any], groups: list[dict[str, Any]]) -> str:
    held = [g["id"] for g in groups if holds(info["chunks"], g)]
    badge = f'<span class="badge ok">证据：{esc("、".join(held))}</span> ' if held else '<span class="badge">非证据</span> '
    src = "；".join(f'{s[0]} {s[2]}（第 {s[1]} 次检索，名次 {s[3]}）' for s in info["sources"])
    return (f'<details><summary>{badge}<b>{esc(("整表 " if info["table"] else "") + u)}</b> <span class="meta">第 {",".join(map(str, info["pages"]))} 页 · '
            f'{info["tokens"]} token · {esc(src)}</span></summary><pre style="white-space:pre-wrap">{esc(info["text"])}</pre></details>')


def case_page(data: dict[str, Any], ev: dict[str, Any], case: dict[str, Any]) -> str:
    gens = [g for g in data["generations"] if str(g) in case["runs"]]
    top = max(ev["limits"])
    panels = ""
    for i, g in enumerate(gens):
        run = case["runs"][str(g)]
        facts = ev["facts"][case["case_id"]][g]
        answers = "".join(
            f'<tr><td class="nw">上限 {L}</td><td><span class="badge {RESULT[facts[L]["result"]][1]}">{RESULT[facts[L]["result"]][0]}</span>'
            f'<div class="meta">{TERMINATION[facts[L]["termination"]]}</div></td><td><pre style="white-space:pre-wrap">{esc(facts[L]["answer"])}</pre></td></tr>'
            for L in sorted(ev["limits"], reverse=True))
        final = run["calls"][-1]["list"]
        flist = "".join(f'<tr><td class="nw">{esc(q["id"])}</td><td class="nw">{esc(q["parent"] or "—")}</td><td style="min-width:320px">{esc(q["text"])}</td>'
                        f'<td class="nw">{esc(q["status"])}{"：" + esc(q["abandon_reason"]) if q["abandon_reason"] else ""}</td>'
                        f'<td class="meta" style="word-break:break-all;min-width:220px">{esc("、".join(q["evidence"]) or "—")}</td></tr>' for q in final)
        calls = ""
        for n, c in enumerate(run["calls"], start=1):
            try:
                out = json.loads(c["output"])
            except ValueError:
                out = {}
            head = (f'第 {n} 次调用（{"规划" if c["kind"] == "plan" else "作答"}）· 输入 {c["prompt_tokens"]} / 输出 {c["completion_tokens"]}（推理 {c["reasoning_tokens"]}）token · '
                    f'{c["seconds"]:.1f} 秒 · ${(c["cost_usd"] or 0):.4f}' + (" · 重试 1 次" if c["retried"] else "")
                    + (" · <span class='down'>降级</span>" if c["degraded"] else ""))
            new = "".join(unit_line(u, run["units"][u], case["groups"]) for u in c["new"])
            body = (f'<h4>本轮新材料（{len(c["new"])}）</h4>{new}' if c["kind"] == "plan" else "")
            body += '<h4>输出</h4>' + (judgement_table(out) if c["kind"] == "plan" else f'<pre style="white-space:pre-wrap">{esc(out.get("answer", ""))}</pre>')
            if c["degraded"]:
                body += '<p class="down">降级，仍未通过：' + esc("；".join(c["degraded"])) + '</p>'
            calls += f'<details class="card"><summary><b>{head}</b></summary>{body}</details>'
        panels += (f'<div data-panel="gen" data-v="{i}"{"" if i == 0 else " class=hidden"}>'
                   f'<div class="card"><h3>各档上限的结果与答案</h3><table><tr><th>上限</th><th>结果</th><th>答案</th></tr>{answers}</table>'
                   f'<h3>最终问题列表（上限 {top}）</h3><table><tr><th>编号</th><th>来源</th><th>问题</th><th>状态</th><th>证据</th></tr>{flist}</table></div>'
                   + calls + '</div>')
    groups = "".join(f'<li><b>{esc(g["id"])}</b> {esc(g["item"])}</li>' for g in case["groups"]) or "<li>本题无答案</li>"
    body = (f'<h1>{esc(case["case_id"])}</h1><div class="sub">{esc(case["question"])}</div>'
            f'<div class="card"><p class="meta">检索范围：{esc("、".join(case["scope"]["documents"]))}；标识编码：{esc("、".join(case["scope"]["identifier_codes"]) or "无")}；'
            f'文档语言：{esc("、".join(case["scope"]["languages"]))}</p><p><b>信息项</b></p><ul>{groups}</ul>'
            + tabs("gen", [f"第 {g} 次生成" for g in gens]) + '</div>' + panels + TAB_JS)
    return page(case["case_id"], body, root="../../../", crumbs=[("报告首页", "../../../index.html"), ("多轮检索评估", "../index.html")])


def folder_of(workspace: Workspace, name: str) -> Path:
    return workspace.root / "reports" / "multi_round" / name


def write_pages(workspace: Workspace, data: dict[str, Any], save: bool = True) -> Path:
    folder = folder_of(workspace, data["name"])
    if save:
        write_atomic(folder / "data" / "runs.json", json.dumps(data, ensure_ascii=False))
    ev = evaluate(data)
    write_assets(workspace.root / "reports")
    (folder / "cases").mkdir(parents=True, exist_ok=True)
    for case in data["cases"]:
        (folder / "cases" / f'{case["case_id"]}.html').write_text(case_page(data, ev, case), encoding="utf-8")
    (folder / "index.html").write_text(render(data, ev), encoding="utf-8")
    return folder


def rerender(workspace: Workspace) -> int:
    root = workspace.root / "reports" / "multi_round"
    count = 0
    for path in sorted(root.glob("*/data/runs.json")) if root.is_dir() else []:
        write_pages(workspace, read_json(path), save=False)
        count += 1
    return count
