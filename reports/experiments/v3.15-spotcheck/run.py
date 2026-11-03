"""V3.15 step 3 spot check: six questions run by multi-round retrieval on RM-T (structured, whole tables),
generation 0, limit 5 calls, arrangement A, K = 3; every call's input and output, and the prompt templates,
on one review page.

Writes runs.json and index.html next to this script.
Run: python reports/experiments/v3.15-spotcheck/run.py   (add --page to rebuild the page from runs.json)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

from rag.reports.html import esc, page, write_assets  # noqa: E402

CASES = [("PO-25-19-Q009", "多跳：先找出是哪家公司，再找它订购的产品"),
         ("CONTRACT-105-Q002", "复合：5 个信息项，其中合同总金额在合同中没有直接写出"),
         ("PO-25-15-Q001", "表格：第四批次的日期与金额（V3.14 中翻译在这题上变差）"),
         ("PO-25-19-Q008", "无答案：订单中没有这款电表的质保期"),
         ("CONTRACT-105-Q014", "需要线索：先找到担保期从哪里起算，再找那个地方的地址"),
         ("CONTRACT-105-Q012", "需要依据：答案由交货期限、验收与付款时限计算得出")]
COMBO = ("structured", "table")


def collect() -> dict:
    from rag.cli import _assembly
    from rag.eval.judge import judge_case
    from rag.eval.metrics import completion_ranks
    from rag.eval.query_compare import MemoSearch, make_planner
    from rag.eval.runner import prepare
    from rag.paths import Workspace
    from rag.query.multi_round.planner import Caller
    from rag.query.multi_round.runner import run
    from rag.query.planning import document_languages

    ws = Workspace(ROOT)
    ctx = prepare(ws, _assembly(ws, COMBO[0], "hybrid", COMBO[1], 1.0), cache_retrievals=True)
    ctx.warm_up()
    languages_of = document_languages(ws.index_dir(COMBO[0]))
    planner = make_planner(ws)   # the planning model and the LLM cache of V3.14
    cases = {c.case_id: c for c in ctx.dataset.cases}
    from rag.ingest.sources import discover
    names = {d.document_id: d.relative_path for d in discover(ws.documents)}
    out = []
    for case_id, why in CASES:
        case = cases[case_id]
        scope = ctx.scope(case.question)
        languages = sorted({languages_of[d] for d in (scope.document_ids or languages_of)})
        record = run(case.question, MemoSearch(ctx.searcher), scope.document_ids, scope.codes, languages,
                     Caller(planner.llm, planner.cache, scope={"documents": [names[d] for d in (scope.document_ids or names)],
                                                               "identifier_codes": list(scope.codes)}), max_calls=5, k=3)
        items = {}
        if case.answerable:
            evidence = judge_case(case, ctx.chunks)
            for g in case.groups:
                for s in g.schemes:
                    for x in s.excerpt_ids:
                        items.setdefault(x, []).append(g.group_id)
            held = {u: sorted({gid for x, e in evidence.excerpts.items() if any(set(record.table[u].unit.chunk_ids) & c
                                                                            for c in e.acceptable_sets) for gid in items.get(x, [])})
                    for u in record.table.order}
            accepted = [u for u in record.table.order if any(u in q.evidence for q in record.questions)]
            covered_accepted = completion_ranks([record.table[u].unit.chunk_ids for u in accepted], evidence.groups)
            covered_handed = completion_ranks([record.table[u].unit.chunk_ids for u in record.table.order], evidence.groups)
            groups = [{"id": g.group_id, "item": g.information_item, "handed": g.group_id in covered_handed,
                       "accepted": g.group_id in covered_accepted} for g in case.groups]
        else:
            held, groups = {u: [] for u in record.table.order}, []
        out.append({
            "case": case_id, "why": why, "question": case.question, "answerable": case.answerable,
            "scope": {"documents": list(scope.document_ids or []), "codes": list(scope.codes), "languages": languages},
            "termination": record.termination, "answer": record.answer, "groups": groups,
            "q0": {"status": record.questions["Q0"].status.value, "abandon_reason": record.questions["Q0"].abandon_reason},
            "rounds": record.rounds,
            "units": {u: {"text": e.unit.text, "document": e.unit.hit.chunk.document_name, "pages": list(e.unit.pages),
                          "call": e.call, "table": e.unit.table is not None, "sources": e.sources, "items": held[u]}
                      for u, e in record.table.entries.items()},
            "order": record.table.order,
            "calls": [{"kind": c.kind, "messages": c.messages, "texts": c.texts, "prompt_tokens": c.prompt_tokens,
                       "completion_tokens": c.completion_tokens, "reasoning_tokens": c.reasoning_tokens, "seconds": c.seconds, "cost_usd": c.cost_usd,
                       "degraded": c.degraded, "cached": c.cached, "new": new, "list": lst}
                      for c, new, lst in zip(record.calls, record.new_units, record.lists)]})
        print(case_id, record.termination, len(record.calls), flush=True)
    return {"runs": out}


# ---------------------------------------------------------------- page

def unit_line(u: str, info: dict, extra: str) -> str:
    items = info["items"]
    badge = (f'<span class="badge ok">证据：{esc("、".join(items))}</span> ' if items else '<span class="badge">非证据</span> ')
    head = f'{badge}<b>{esc(u if not info["table"] else "整表 " + u)}</b> <span class="meta">{esc(info["document"][:40])} · 第 {",".join(map(str, info["pages"]))} 页 · {extra}</span>'
    return f'<details><summary>{head}</summary><pre style="white-space:pre-wrap">{esc(info["text"])}</pre></details>'


def judgement_table(out: dict) -> str:
    rows = []
    for j in out.get("judgements", []):
        retry = "".join(f'<div class="meta">{esc(m)}：' + "；".join(f'[{esc(x["language"])}] {esc(x["text"])}' for x in xs) + '</div>'
                        for m, xs in j["retry"].items() if xs)
        verdict = '<span class="badge ok">可回答</span>' if j["answerable"] else f'<span class="badge warn">{esc(j["gap"])}</span> → <b>{esc(j["action"])}</b>'
        rows.append(f'<tr><td class="nw"><b>{esc(j["id"])}</b></td><td class="nw">{verdict}</td><td class="nw">{esc("、".join(j["accept"]) or "—")}</td>'
                    f'<td>{esc(j["note"])}{"<div class=meta>理由：" + esc(j["reason"]) + "</div>" if j["reason"] else ""}{retry}</td></tr>')
    extra = ""
    if out.get("new_questions"):
        extra += "<p><b>新问题</b></p><ul>" + "".join(
            f'<li>{esc(n["parent"])} → {esc(n["text"])}' + "".join(f'<div class="meta">[{esc(t["language"])}] {esc(t["text"])}</div>' for t in n["translations"]) + "</li>"
            for n in out["new_questions"]) + "</ul>"
    if out.get("withdrawals"):
        extra += "<p><b>撤回</b>：" + "；".join(f'{esc(w["id"])}（{esc(w["reason"])}）' for w in out["withdrawals"]) + "</p>"
    if out.get("answer"):
        extra += f'<p><b>答案</b></p><pre style="white-space:pre-wrap">{esc(out["answer"])}</pre>'
    return ('<table><tr><th>问题</th><th>判断</th><th>接受的单元</th><th>说明 / 兜底文本</th></tr>' + "".join(rows) + '</table>' + extra)


def call_block(n: int, call: dict, run: dict) -> str:
    payload = json.loads(call["messages"][1]["content"])
    head = (f'第 {n} 次调用（{"规划" if call["kind"] == "plan" else "作答"}）· 输入 {call["prompt_tokens"]} token · 输出 {call["completion_tokens"]} token · '
            f'{call["seconds"]:.1f} 秒' + (" · 重试 1 次" if len(call["texts"]) > 1 else "") + (" · <span class='down'>降级</span>" if call["degraded"] else ""))
    qs = "".join(f'<tr><td class="nw">{esc(q["id"])}</td><td class="nw">{esc(q.get("parent") or "—")}</td><td>{esc(q["text"])}</td>'
                 f'<td class="nw">{esc(q["status"])}</td><td class="meta">{esc(q.get("note", ""))}</td></tr>' for q in payload["questions"])
    body = f'<h4>输入：问题列表</h4><table><tr><th>编号</th><th>来源</th><th>问题</th><th>状态</th><th>此前的说明</th></tr>{qs}</table>'
    if payload.get("to_judge"):
        body += f'<p class="meta">需判断：{esc("、".join(payload["to_judge"]))}</p>'
    ev = payload.get("evidence", [])
    body += f'<h4>输入：已接受的证据（{len(ev)}）</h4>' + "".join(
        unit_line(e["id"], run["units"][e["id"]], "接受者 " + "、".join(e.get("accepted_by", []))) for e in ev)
    if "new_material" in payload:
        body += f'<h4>输入：本轮新材料（{len(payload["new_material"])}）</h4>' + "".join(
            unit_line(m["id"], run["units"][m["id"]], "；".join(m["returned_by"])) for m in payload["new_material"])
    if len(call["texts"]) > 1:
        body += f'<h4>第一次输出未通过的检查</h4><pre style="white-space:pre-wrap">{esc(call["messages"][-1]["content"])}</pre>'
    try:
        out = json.loads(call["texts"][-1])
    except ValueError:
        out = {}
    body += '<h4>输出</h4>' + (judgement_table(out) if call["kind"] == "plan" else f'<pre style="white-space:pre-wrap">{esc(out.get("answer", ""))}</pre>')
    if call["degraded"]:
        body += '<p class="down">降级，仍未通过：' + esc("；".join(call["degraded"])) + '</p>'
    body += f'<details><summary>原始输入输出（JSON）</summary><pre style="white-space:pre-wrap">{esc(call["messages"][1]["content"])}</pre>' + "".join(
        f'<pre style="white-space:pre-wrap">{esc(t)}</pre>' for t in call["texts"]) + '</details>'
    return f'<details class="card" open><summary><b>{head}</b></summary>{body}</details>'


TERMINATION = {"answered": "Q0 已回答", "limit": "达到调用上限", "nothing_pending": "没有待检索的问题"}


def result(r: dict) -> str:
    """Answered or not, and whether the accepted evidence covers the annotated items."""
    answered = r["q0"]["status"] == "answered"
    groups = r["groups"]
    missing = [g["id"] for g in groups if not g["accepted"]]
    if not r["answerable"]:
        return ('<span class="badge ok">未回答：本题无答案，正确</span>' if not answered
                else '<span class="badge bad">已回答：本题无答案，误判可回答</span>')
    if answered:
        return (f'<span class="badge ok">已回答，证据凑齐 {len(groups)}/{len(groups)}</span>' if not missing else
                f'<span class="badge bad">已回答，证据未凑齐：缺 {esc("、".join(missing))}（误判可回答）</span>')
    seen = [g for g in missing if next(x for x in groups if x["id"] == g)["handed"]]
    return (f'<span class="badge warn">未回答（漏答）：缺 {esc("、".join(missing))}</span>'
            + (f'<div class="meta">其中 {esc("、".join(seen))} 的证据交给过 LLM 但未被接受</div>' if seen else ""))


def llm_calls(r: dict) -> str:
    plan = sum(c["kind"] == "plan" for c in r["calls"])
    answer = len(r["calls"]) - plan
    return f'规划 {plan}' + (f' + 作答 {answer}' if answer else "")


def timing(r: dict) -> str:
    rounds = "、".join(f'{n} 次 {s * 1000:.0f} ms' for n, s in r["rounds"])
    calls = "".join(f'<div style="white-space:nowrap">第 {i} 次{"规划" if c["kind"] == "plan" else "作答"}：{c["seconds"]:.1f} 秒 · {c["prompt_tokens"]} / {c["completion_tokens"]}（{c["reasoning_tokens"]}）</div>'
                        for i, c in enumerate(r["calls"], start=1))
    total = sum(c["seconds"] for c in r["calls"]) + sum(s for _, s in r["rounds"])
    return (f'<b>合计 {total:.1f} 秒</b><div class="meta" style="white-space:nowrap">检索：{rounds}</div>'
            f'<div class="meta">{calls}</div>')


def render(data: dict) -> str:
    from rag.query.multi_round.planner import ANSWER_SYSTEM, PLAN_SYSTEM, RETRY_TEMPLATE, PROMPT_VERSIONS, plan_schema
    rows = []
    for r in data["runs"]:
        q0 = esc(r["q0"]["status"]) + ("：" + esc(r["q0"]["abandon_reason"]) if r["q0"]["abandon_reason"] else "")
        rows.append(f'<tr><td style="min-width:300px"><b>{esc(r["case"])}</b> <span class="meta">{esc(r["why"])}</span>'
                    f'<div>{esc(r["question"])}</div></td>'
                    f'<td style="min-width:150px">{result(r)}</td>'
                    f'<td class="nw">{TERMINATION[r["termination"]]}<div class="meta">Q0：{q0}</div></td>'
                    f'<td class="num">{len(r["calls"])}<div class="meta" style="white-space:nowrap">{llm_calls(r)}</div></td>'
                    f'<td>{timing(r)}</td><td class="num">{len(r["order"])}</td></tr>')
    summary = ('<div class="scroll"><table><tr><th>题</th><th>结果</th><th>终止原因<div class="meta">Q0 最终状态</div></th>'
               '<th>LLM 调用</th><th>耗时与 token<div class="meta">输入 / 输出（其中推理）</div></th><th>交给 LLM 的单元</th></tr>' + "".join(rows) + '</table></div>'
               '<p class="meta">结果按已接受的单元是否覆盖标注的证据判定（信息项 e1、e2…）；答案文字本身对不对本版不评估。'
               '耗时：检索为各轮检索的次数与时间（命中缓存时记录首次检索的时间）；LLM 为各次调用的时间，token 为输入 / 输出（其中推理）。</p>')
    prompts = ('<div class="card"><h2>提示词模板（原文）</h2>'
               f'<p class="meta">规划 {PROMPT_VERSIONS["plan"]}、作答 {PROMPT_VERSIONS["answer"]}；{{languages}} 由代码填入检索范围内的文档语言；问题列表中的 retrieval_method_left、retry_texts、open_sub_questions 由代码给出。'
               '每次调用独立，不带对话历史；用户消息是 JSON（检索范围、问题列表、已接受的证据、本轮新材料、需判断的问题）。</p>'
               f'<h3>规划</h3><pre style="white-space:pre-wrap">{esc(PLAN_SYSTEM)}</pre>'
               f'<h3>作答（调用次数到上限或没有待检索的问题时）</h3><pre style="white-space:pre-wrap">{esc(ANSWER_SYSTEM)}</pre>'
               f'<h3>校验不通过时的重试</h3><pre style="white-space:pre-wrap">{esc(RETRY_TEMPLATE)}</pre>'
               f'<details><summary>规划输出的 JSON schema（以西班牙文为文档语言时）</summary><pre style="white-space:pre-wrap">{esc(json.dumps(plan_schema(["spanish"]), ensure_ascii=False, indent=1))}</pre></details></div>')
    tabs = '<div class="seg" data-rtabs>' + "".join(
        f'<button type="button" data-v="{i}"{" class=on" if i == 0 else ""}>{esc(r["case"])}</button>' for i, r in enumerate(data["runs"])) + '</div>'
    panels = ""
    for i, r in enumerate(data["runs"]):
        final = r["calls"][-1]["list"]
        flist = "".join(f'<tr><td class="nw">{esc(q["id"])}</td><td class="nw">{esc(q["parent"] or "—")}</td><td>{esc(q["text"])}</td>'
                        f'<td class="nw">{esc(q["status"])}{"：" + esc(q["abandon_reason"]) if q["abandon_reason"] else ""}</td>'
                        f'<td class="nw">{esc("、".join(q["evidence"]) or "—")}</td></tr>' for q in final)
        panels += (f'<div data-rpanel="{i}"{"" if i == 0 else " class=hidden"}><div class="card"><h3>{esc(r["case"])}：{esc(r["question"])}</h3>'
                   f'<p class="meta">检索范围的文档语言：{esc("、".join(r["scope"]["languages"]))}；标识编码：{esc("、".join(r["scope"]["codes"]) or "无")}；终止：{esc(r["termination"])}</p>'
                   f'<h4>最终答案</h4><pre style="white-space:pre-wrap">{esc(r["answer"])}</pre>'
                   f'<h4>最终问题列表</h4><table><tr><th>编号</th><th>来源</th><th>问题</th><th>状态</th><th>证据</th></tr>{flist}</table></div>'
                   + "".join(call_block(n, c, r) for n, c in enumerate(r["calls"], start=1)) + '</div>')
    body = (f'<h1>V3.15 抽查：6 道题的逐次调用</h1><div class="sub">RM-T（structured，返回整表）· 第 0 次生成 · 上限 5 次调用 · 安排 A · K = 3</div>'
            '<div class="card"><h2>结论先看</h2>' + summary +
            '<p class="meta">请重点看：每次判断是否可回答、接受的单元是否正确（绿色"证据"标记是标注的证据单元，供核对）、缺口与动作是否合理、派生的问题是否独立成立、答案是否只依据材料。</p></div>'
            + prompts + '<div class="card">' + tabs + '</div>' + panels
            + '<script>(function(){var bs=document.querySelectorAll("[data-rtabs] button");bs.forEach(function(b){b.addEventListener("click",function(){'
              'bs.forEach(function(x){x.classList.toggle("on",x===b)});document.querySelectorAll("[data-rpanel]").forEach(function(p){'
              'p.classList.toggle("hidden",p.dataset.rpanel!==b.dataset.v)})})})})();</script>')
    return page("V3.15 抽查", body, root="../../", crumbs=[("报告首页", "../../index.html")])


def main() -> None:
    path = HERE / "runs.json"
    if "--page" in sys.argv:
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = collect()
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    write_assets(ROOT / "reports")
    (HERE / "index.html").write_text(render(data), encoding="utf-8")
    print(HERE / "index.html")


if __name__ == "__main__":
    main()
