"""由 results/ 生成第 2 轮样例审核页 index.html。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from run_samples import MODELS, NO_TEMPERATURE, REAL  # noqa: E402
from synthetic import TABLES as SYNTHETIC, build  # noqa: E402

from rag.ingest.tables.llm_tables import LEAD_MAX_TOKENS, Lengths  # noqa: E402
from rag.reports.html import badge, esc, page, text_block, write_assets  # noqa: E402

HERE = Path(__file__).parent
RESULTS = HERE / "results"
ROOT = "../../../"          # annotation-samples/ -> reports/
KEYS = [(key, title) for key, _, _, title in REAL] + [(key, title) for key, title, *_ in SYNTHETIC]
SHORT = {"po2519-qty": "PO 25-19 数量表", "contract-object": "c0004 合同标的",
         "syn-budget": "人造：西文预算表", "syn-schedule": "人造：英文交货计划", "syn-payment": "人造：中文付款计划"}
FORMS = [("markdown", "LM（Markdown）"), ("labeled", "LL（表头映射）"),
         ("markdown_lead", "LM+（Markdown + 引导句）"), ("labeled_lead", "LL+（表头映射 + 引导句）")]

FINDINGS = [  # 由助手逐项查看结果后写入（2026-10-19）
    ("结论", "15 次结构标注最终全部通过，没有表退回规则。luna、gemini 全部一次通过；deepseek 在两张人造表上"
     "第 1 次把有值的列（Inspection witness、计划日期）标成隐式合并，被校验拦下，重试后改正。"),
    ("结论", "表头：3 个模型在 5 张表上判定的表头行都与规则表头判定一致。PO 25-19 的 4 个分组标题、"
     "c0004 无分节，3 个模型一致且正确。"),
    ("结论", "人造预算表：表头之上的全表标题、两层嵌套分节，3 个模型都判对；Subtotal、COSTO DIRECTO、Nota "
     "都没有被当成节标题（“按含义判断”有效）。"),
    ("问题", "人造预算表的两行节标题（02.01 及其下一行说明“(incluye suministro…)”）只有 gemini 标成两行；"
     "luna、deepseek 只标了第一行，说明行被当成数据行，02.01 被切开时后一块不重复它。"
     "luna 还把 Subtotal 01 划进了子节 01.02（应只属于 01）。两处都只影响重复哪些行，不产生错误数据。"),
    ("结论", "人造交货计划、中文付款计划：隐式合并 8 段 + 8 段，3 个模型（deepseek 为重试后）全部判对；"
     "都没有把“本来就没有值”的空格（Qty、Inspection witness、Remarks、数量）标成同上，也没有标解析出的纵向合并。"),
    ("问题", "中文付款计划：3 个模型都把期次 6（质保金）划进了第二批。造表时我预期它不属于任何一批，"
     "但这一行的归属本身有歧义，算预期不严谨，不判为模型错误。"),
    ("结论", "引导句全部一次通过：19–24 个词（中文 35–41 字），23–38 token（第 1 轮为 30–69 token，9 次超 48）。"
     "都不含位置信息；表前无关的上下文（电话号码、页码行）没有被写进引导句或摘要。"),
    ("问题", "gemini 在人造预算表的引导句里写了表内标题中的编号 SED-114，违反“不照抄单元格的值”，代码校验不出"
     "（数字在表中出现过）。"),
    ("结论", "摘要全部一次通过：131–397 token，都不超上限，都写出了分组标题和标识各行的值。"),
    ("问题", "块数比规则多：PO 25-19 的 LM 9 块（RM 5 块），LL 18 块（RL 12 块）。原因是每块预留 64 token 给引导句、"
     "每块重复分组标题（52 token）与三行表头（180 token），而 LL 每行约 210 token，一块只放得下 1 行。"
     "这是“每块自足”的代价，会使只返回命中 chunk 时每题返回的 token 变多，由全量评估的指标 3 衡量。"),
    ("结论", "花费（5 张表，两次调用，含推理）：luna $0.0046、deepseek $0.0136、gemini $0.0561；"
     "推理 token 2.9k、14.0k、8.4k。"),
]


def load(model: str, key: str) -> dict | None:
    path = RESULTS / model.replace("/", "__") / f"{key}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def spent(call: dict) -> float:
    return sum(a["cost_usd"] or 0 for a in call.get("attempts", []))


def status(call: dict, passed: bool, part: str | None = None) -> str:
    """`part`: "lead" or "annotation" when one call carries both and each is judged on its own."""
    if "error" in call:
        return badge("调用出错", "bad")
    first = call["attempts"][0]["issues"]
    if part is not None:
        first = [x for x in first if x.startswith("lead:") == (part == "lead")]
    once = not first
    if passed:
        return badge("通过" if once else "重试后通过", "ok" if once else "warn")
    return badge("未通过，退回规则", "bad")


def annotation_text(ann: dict | None) -> str:
    if ann is None:
        return "—"
    sections = "；".join(f"{'-'.join(map(str, s['title_rows'][:1] + s['title_rows'][-1:])) if len(s['title_rows']) > 1 else s['title_rows'][0]}"
                        f"→{s['last_row']}" for s in sorted(ann["sections"], key=lambda s: s["title_rows"][0]))
    merges = "；".join(f"c{m['column']} {m['first_row']}→{m['last_row']}" for m in ann["implicit_merges"])
    return (f"表头 {ann['header_rows'] or '无'}<br>节（标题行→末行）：{esc(sections) or '无'}"
            f"<br>隐式合并（列 首行→末行）：{esc(merges) or '无'}")


def attempts_html(call: dict) -> str:
    if "error" in call:
        return f'<div class="meta">{esc(call["error"])}</div>'
    parts = []
    for i, a in enumerate(call["attempts"], 1):
        head = (f"第 {i} 次：输入 {a['prompt_tokens']} · 输出 {a['completion_tokens']}"
                f"（推理 {a['reasoning_tokens']}）token · ${a['cost_usd'] or 0:.5f}")
        issues = "".join(f"<li>{esc(x)}</li>" for x in a["issues"])
        parts.append(f'<div class="meta">{head}</div>' + (f'<ul class="meta">{issues}</ul>' if issues else ""))
        parts.append(f'<details><summary class="meta">第 {i} 次的原始输出与推理</summary>'
                     f'{text_block(a["raw"], 1500)}{text_block(a["reasoning"] or "（未返回推理内容）", 1500)}</details>')
    return "".join(parts)


def chunks_html(chunks: list[dict] | None, checks: list[str] | None = None) -> str:
    if not chunks:
        return '<div class="meta">（无）</div>'
    body = ""
    if checks is not None:
        body += (f'<div>{badge("代码检查通过", "ok")} 每块含表头行；含某节数据行的块含该节全部标题行；无单行超出预算</div>'
                 if not checks else "".join(f'<div>{badge("检查未通过", "bad")} {esc(x)}</div>' for x in checks))
    tokens = [c["tokens"] for c in chunks]
    body += f'<div class="meta">{len(chunks)} 块 · 每块 {min(tokens)}–{max(tokens)} token（含 passage 前缀）</div>'
    body += "".join(f'<div class="meta">chunk {i} · {c["tokens"]} token · 重复上下文 {c["repeated"]} 行</div>'
                    f'{text_block(c["text"])}' for i, c in enumerate(chunks, 1))
    return body


def overview(data: dict) -> str:
    head = "".join(f"<th>{esc(SHORT[key])}</th>" for key, _ in KEYS)
    rows = []
    for m in MODELS:
        cells, total, reasoning = [], 0.0, 0
        for key, _ in KEYS:
            out = data[(m, key)]
            if out is None:
                cells.append("<td>未运行</td>")
                continue
            ann, summ = out["annotation"], out["summary"]
            parts = []
            if "error" in ann:
                parts.append("标注 " + badge("调用出错", "bad"))
            else:
                parts.append("标注 " + status(ann, ann["annotation"] is not None, "annotation"))
                parts.append("引导句 " + status(ann, ann["lead"] is not None, "lead"))
                checks = (out.get("markdown_checks") or []) + (out.get("labeled_checks") or [])
                if ann["annotation"] is not None:
                    parts.append("渲染检查 " + (badge("通过", "ok") if not checks else badge(f"{len(checks)} 项未通过", "bad")))
            parts.append("摘要 " + status(summ, bool(summ.get("summary"))))
            for call in (ann, summ):
                total += spent(call)
                reasoning += sum(a["reasoning_tokens"] or 0 for a in call.get("attempts", []))
            cells.append("<td class=nw>" + "<br>".join(parts) + "</td>")
        note = "<br><span class=meta>不支持 temperature，未传</span>" if m in NO_TEMPERATURE else ""
        rows.append(f"<tr><td class=nw><b>{esc(m)}</b>{note}</td><td class=num>${total:.4f}</td>"
                    f"<td class=num>{reasoning:,}</td>{''.join(cells)}</tr>")
    return (f'<div class="card"><h3>校验结果与花费</h3><div class="scroll" data-grid><table>'
            f'<tr><th>模型</th><th class=num>5 张表合计花费</th><th class=num>推理 token</th>{head}</tr>'
            f'{"".join(rows)}</table></div>'
            f'<p class="meta">花费为 OpenRouter 实际计费，含重试与推理 token。"重试后通过"指第一次未通过校验，'
            f'把未通过的项反馈给模型后第二次通过；"未通过，退回规则"指重试后仍未通过，该表按提案 2.6 退回规则序列化'
            f'（引导句未通过则不加引导句）。渲染检查由代码对 LM、LL 的每个 chunk 检查表头与节标题是否齐全。</p></div>')


def findings() -> str:
    if not FINDINGS:
        return ""
    items = "".join(f"<li>{badge(kind, 'bad' if kind == '问题' else 'ok')} {esc(text)}</li>"
                    for kind, text in FINDINGS)
    return f'<div class="card"><h3>结论与问题</h3><ul>{items}</ul></div>'


def table_html(key: str) -> str:
    """A synthetic table drawn with its merged cells, row numbers as in the grid (r0, r1, ...)."""
    spec = next((t for t in SYNTHETIC if t[0] == key), None)
    if spec is None:
        return ""
    table = build(key, spec[4]())
    starts = {(c.start_row_offset_idx, c.start_col_offset_idx): c for c in table.cells}
    rows = []
    for r in range(table.row_count):
        cells = "".join(
            f'<td{f" rowspan={c.row_span}" if c.row_span > 1 else ""}'
            f'{f" colspan={c.col_span}" if c.col_span > 1 else ""}>{esc(c.text)}</td>'
            for col in range(table.column_count) if (c := starts.get((r, col))))
        rows.append(f'<tr><td class="meta nw">r{r}</td>{cells}</tr>')
    return (f'<div class="meta">表格原样（合并单元格按原样合并；左列为行号，与标注中的行号一致）：</div>'
            f'<div class="scroll" data-grid><table>{"".join(rows)}</table></div>')


def table_panel(key: str, title: str, inp: dict, data: dict) -> str:
    lengths = Lengths(inp["language"], inp["lengths"]["summary_max_tokens"])
    context = "".join(f'<div class="meta">[{esc(n["kind"])}]</div>{text_block(n["text"])}'
                      for n in inp["context"]) or '<div class="meta">(none)</div>'
    expected = (f'<div class="meta">造表时的预期标注：</div>{text_block(inp["expected"])}'
                if inp.get("expected") else "")
    block = [f'<h2>{esc(title)}</h2>',
             f'<div class="card"><h3>输入</h3><div class="meta">文档语言：{esc(inp["language"])} · '
             f'告诉模型：引导句不超过 {esc(lengths.lead_limit)}（代码接受 ≤ {LEAD_MAX_TOKENS} token）、'
             f'摘要不超过 {esc(lengths.summary_limit)}（代码接受 ≤ {lengths.summary_max_tokens} token）</div>'
             f'<div class="meta">上下文（表前两个节点）：</div>{context}{table_html(key)}{expected}'
             f'<details><summary>表格网格（交给 LLM 的 Markdown）</summary>{text_block(inp["grid"])}</details></div>']
    rule_header = inp["rule_header_rows"]
    rows = [f'<tr><td class=nw><b>规则表头判定</b></td><td>表头 {rule_header if rule_header is not None else "未认出"}</td><td></td></tr>']
    for m in MODELS:
        out = data[(m, key)]
        if out is None or "error" in out["annotation"]:
            continue
        ann = out["annotation"]
        rows.append(f"<tr><td class=nw><b>{esc(m)}</b><br>{status(ann, ann['annotation'] is not None, 'annotation')}</td>"
                    f"<td>{annotation_text(ann['annotation'])}</td><td>{attempts_html(ann)}</td></tr>")
    block.append(f'<div class="card"><h3>结构标注</h3><div class="scroll" data-grid><table>'
                 f'<tr><th>来源</th><th style="width:38%">标注（行号从 0 起，同网格的 r 号）</th><th>各次调用</th></tr>'
                 f'{"".join(rows)}</table></div></div>')
    rows = []
    for m in MODELS:
        out = data[(m, key)]
        if out is None or "error" in out["annotation"]:
            continue
        ann = out["annotation"]
        last = json.loads(ann["attempts"][-1]["raw"]).get("lead", "") if ann["attempts"] else ""
        lead = ann["lead"] or last
        size = lengths.size(lead) if lead else ""
        rows.append(f"<tr><td class=nw><b>{esc(m)}</b><br>{status(ann, ann['lead'] is not None, 'lead')}</td>"
                    f"<td>{esc(lead)}<div class=meta>{esc(size)}</div></td></tr>")
    block.append(f'<div class="card"><h3>引导句</h3><div class="scroll" data-grid><table>'
                 f'<tr><th>模型</th><th>引导句（未通过时显示最后一次的输出）</th></tr>{"".join(rows)}</table></div></div>')
    rows = []
    for m in MODELS:
        out = data[(m, key)]
        if out is None:
            continue
        summ = out["summary"]
        if "error" in summ:
            rows.append(f"<tr><td class=nw><b>{esc(m)}</b></td><td>{attempts_html(summ)}</td></tr>")
            continue
        text = summ["summary"] or json.loads(summ["attempts"][-1]["raw"]).get("summary", "")
        size = f'{lengths.size(text)} · {out.get("summary_tokens", "—")} token（含 passage 前缀）'
        rows.append(f"<tr><td class=nw><b>{esc(m)}</b><br>{status(summ, bool(summ['summary']))}</td>"
                    f"<td>{text_block(text)}<div class=meta>{esc(size)}</div>{attempts_html(summ)}</td></tr>")
    block.append(f'<div class="card"><h3>摘要（S）</h3><div class="scroll" data-grid><table>'
                 f'<tr><th>模型</th><th>摘要</th></tr>{"".join(rows)}</table></div></div>')
    block.append('<h3>切出的 chunk</h3>')
    rule = inp["rule_chunks"]
    block.append(f'<div class="card"><details><summary><b>对照：规则序列化</b> · RM {len(rule["markdown"])} 块 · '
                 f'RL {len(rule["labeled"])} 块</summary><h4>RM（markdown_rows_v1）</h4>{chunks_html(rule["markdown"])}'
                 f'<h4>RL（labeled_rows_v1）</h4>{chunks_html(rule["labeled"])}</details></div>')
    for m in MODELS:
        out = data[(m, key)]
        if out is None or "error" in out["annotation"]:
            continue
        fallback = out["fallback"]["annotation"]
        parts = []
        for form, label in FORMS:
            base = form.removesuffix("_lead")
            checks = None if fallback else out.get(base + "_checks")
            note = "（退回规则）" if fallback else ""
            chunks = out.get(form)
            count = f"{len(chunks)} 块" if chunks else "无引导句"
            parts.append(f'<details><summary>{esc(label)}{note} · {count}</summary>'
                         f'{chunks_html(chunks, checks if not form.endswith("_lead") else None)}</details>')
        flag = badge("标注退回规则", "bad") if fallback else ""
        block.append(f'<div class="card"><b>{esc(m)}</b> {flag} · LM {len(out["markdown"])} 块 · '
                     f'LL {len(out["labeled"])} 块{"".join(parts)}</div>')
    return f'<section data-panel="{key}" class="hidden">{"".join(block)}</section>'


def main() -> None:
    inputs = {key: json.loads((RESULTS / "_inputs" / f"{key}.json").read_text(encoding="utf-8"))
              for key, _ in KEYS}
    data = {(m, key): load(m, key) for m in MODELS for key, _ in KEYS}
    tabs = ['<a href="#overview" data-tab="overview">总览</a>'] + [
        f'<a href="#{key}" data-tab="{key}">{esc(SHORT[key])}</a>' for key, _ in KEYS]
    panels = [f'<section data-panel="overview">{findings()}{overview(data)}</section>']
    panels += [table_panel(key, title, inputs[key], data) for key, title in KEYS]
    body = (f'<h1>V3.13 样例审核（第 2 轮）：结构标注、引导句、摘要</h1>'
            f'<div class="sub">3 个模型 × 5 张表（2 张真实表、3 张人造样表），提示词 llm_tables_v3'
            f'（docs/changes/v3.13-prompts.md），JSON Schema 严格模式，原生推理中档，预算 512 token。'
            f'请看：表头、分节、隐式合并是否判对；渲染出的每块是否单独看得懂；引导句与摘要是否准确、'
            f'不含位置信息。第 1 轮（LLM 直接写记录）的结果保留在 ../samples/。</div>'
            f'<div class="tabs">{"".join(tabs)}</div>{"".join(panels)}')
    write_assets(HERE.parents[2])     # repo reports/
    (HERE / "index.html").write_text(page("V3.13 样例审核（第 2 轮）", body, root=ROOT,
                                          crumbs=[("报告首页", ROOT + "index.html")]), encoding="utf-8")


if __name__ == "__main__":
    main()
