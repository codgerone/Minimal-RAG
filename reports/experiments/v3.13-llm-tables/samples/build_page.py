"""由 results/ 生成样例审核页 index.html。单价取自 OpenRouter 模型列表（首次生成时的报价，存入 results/_prices.json）。"""
import json
import urllib.request
from pathlib import Path

from rag.reports.html import badge, esc, page, text_block, write_assets
from run_samples import MODELS, TABLES

HERE = Path(__file__).parent
RESULTS = HERE / "results"
ROOT = "../../../"          # samples/ -> reports/
KINDS = {"records": "记录", "lead": "引导句", "summary": "摘要"}


def prices() -> dict:
    path = RESULTS / "_prices.json"
    if not path.exists():
        data = json.load(urllib.request.urlopen("https://openrouter.ai/api/v1/models"))["data"]
        found = {m["id"]: {"prompt": float(m["pricing"]["prompt"]) * 1e6,
                           "completion": float(m["pricing"]["completion"]) * 1e6}
                 for m in data if m["id"] in MODELS}
        path.write_text(json.dumps(found, indent=2), encoding="utf-8")
    return json.loads(path.read_text(encoding="utf-8"))


def cost(attempts: list, price: dict) -> float:
    total = 0.0
    for a in attempts:
        if a["cost_usd"] is not None:
            total += a["cost_usd"]
        elif a["prompt_tokens"] is not None:
            total += (a["prompt_tokens"] * price["prompt"]
                      + (a["completion_tokens"] or 0) * price["completion"]) / 1e6
    return total


def status(call: dict) -> str:
    if "error" in call:
        return badge("调用出错", "bad")
    ok = not call["attempts"][-1]["issues"]
    once = len(call["attempts"]) == 1
    return badge(("通过" if once else "重试后通过") if ok else "未通过",
                 "ok" if ok and once else "warn" if ok else "bad")


def issues_html(call: dict) -> str:
    if "error" in call:
        return f'<div class="meta">{esc(call["error"])}</div>'
    parts = []
    for i, a in enumerate(call["attempts"], 1):
        if a["issues"]:
            items = "".join(f"<li>{esc(x)}</li>" for x in a["issues"][:30])
            more = f"<li>…共 {len(a['issues'])} 项</li>" if len(a["issues"]) > 30 else ""
            parts.append(f'<div class="meta">第 {i} 次未通过的项：</div><ul class="meta">{items}{more}</ul>')
    return "".join(parts)


def load(model: str, key: str) -> dict | None:
    path = RESULTS / model.replace("/", "__") / f"{key}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def overview(data: dict, price: dict) -> str:
    head = "".join(f"<th>{esc(title)}</th>" for _, _, _, title in TABLES)
    rows = []
    for m in MODELS:
        p = price.get(m, {"prompt": 0, "completion": 0})
        cells, spent = [], 0.0
        for key, *_ in TABLES:
            out = data[(m, key)]
            if out is None:
                cells.append("<td>未运行</td>")
                continue
            parts = []
            for kind, label in KINDS.items():
                parts.append(f"{label} {status(out[kind])}")
                if "error" not in out[kind]:
                    spent += cost(out[kind]["attempts"], p)
            cells.append("<td class=nw>" + "<br>".join(parts) + "</td>")
        rows.append(f"<tr><td class=nw><b>{esc(m)}</b></td>"
                    f"<td class=num>{p['prompt']:.2f} / {p['completion']:.2f}</td>"
                    f"<td class=num>${spent:.4f}</td>{''.join(cells)}</tr>")
    return (f'<div class="card"><h3>模型、单价与校验结果</h3><div class="scroll" data-grid><table>'
            f'<tr><th>模型</th><th class=num>输入 / 输出单价<br>（美元 / 百万 token）</th>'
            f'<th class=num>4 张表合计花费</th>{head}</tr>{"".join(rows)}</table></div>'
            f'<p class="meta">花费为 OpenRouter 实际计费（含重试）；未返回计费时按单价 × token 估算。'
            f'"重试后通过"指第一次未通过校验，把未通过的项反馈给模型后第二次通过。</p></div>')


def table_panel(key: str, title: str, inp: dict, data: dict) -> str:
    ctx = inp["context"]
    lim = inp["limits"]
    rule = "".join(f'<div class="meta">chunk {i}</div>{text_block(t)}'
                   for i, t in enumerate(inp["rule_chunks"], 1))
    block = [f'<h2>{esc(title)}</h2>',
             f'<div class="card"><h3>输入</h3><div class="meta">文档语言：{esc(inp["language"])} · '
             f'预算 {inp["max_tokens"]} token；单条记录上限 {lim["record_max_tokens"]}、'
             f'引导句上限 {lim["lead_max_tokens"]}、摘要上限 {lim["summary_max_tokens"]}（按 multilingual-e5 计）</div>'
             f'<div class="meta">上下文 · 章节标题：</div>{text_block(ctx["heading"] or "(none)")}'
             f'<div class="meta">上下文 · 表前正文：</div>{text_block(ctx["before"] or "(none)")}'
             f'<details><summary>表格网格（交给 LLM 的 Markdown）</summary>{text_block(inp["grid"])}</details>'
             f'<details><summary>对照：规则序列化（labeled_rows_v1）切出的 {len(inp["rule_chunks"])} 个 chunk</summary>'
             f'{rule}</details></div>']
    rows = []
    for m in MODELS:
        out = data[(m, key)]
        if out is None:
            continue
        lead, summ = out["lead"], out["summary"]
        rows.append(f"<tr><td class=nw><b>{esc(m)}</b></td>"
                    f"<td>{status(lead)} {esc(lead.get('text', ''))}{issues_html(lead)}</td>"
                    f"<td>{status(summ)} {esc(summ.get('text', ''))}{issues_html(summ)}</td></tr>")
    block.append(f'<div class="card"><h3>引导句与摘要</h3><div class="scroll" data-grid><table>'
                 f'<tr><th>模型</th><th style="width:30%">引导句</th><th>摘要</th></tr>{"".join(rows)}'
                 f'</table></div></div>')
    block.append('<h3>记录与切出的 chunk（引导句通过时写在每块开头）</h3>')
    for m in MODELS:
        out = data[(m, key)]
        if out is None:
            continue
        rec = out["records"]
        chunks = out.get("chunks_with_lead") or out.get("chunks") or []
        segments = rec.get("records", []) if "error" not in rec else []
        body = issues_html(rec)
        body += "".join(f'<div class="meta">chunk {i} · {c["tokens"]} token · {len(c["records"])} 条记录</div>'
                        f'{text_block(c["text"])}' for i, c in enumerate(chunks, 1))
        if not chunks and "error" not in rec:
            body += f'<div class="meta">最后一次原始输出：</div>{text_block(rec["attempts"][-1]["raw"], 3000)}'
        block.append(f'<div class="card"><details><summary><b>{esc(m)}</b> · 记录 {status(rec)} · '
                     f'{len(segments)} 段 {sum(len(s) for s in segments)} 条 → {len(chunks)} 个 chunk'
                     f'</summary>{body}</details></div>')
    return f'<section data-panel="{key}" class="hidden">{"".join(block)}</section>'


FINDINGS = [  # 由助手逐项查看结果后写入（2026-10-17）
    ("问题", "有 4 个模型把上下文写进了每条记录：deepseek-v4.1-flash、gpt-6-luna 在每条前写"
     "“6. Quantity per electrical company:”，gemini-3.8-flash 写“Category:”或“CRONOGRAMA DE ENTREGA |”，"
     "opus-5.5 在交货计划表写“Cronograma de entrega.”。这等于自带了引导句，会污染“不加引导句”的对照组；"
     "建议在序列化提示词第 4 条补一句“不要把上下文的文字写进记录”。"),
    ("问题", "上下文有噪声：PO 25-19 数量表的“表前正文”是 Docling 误判为正文的页眉电话号码"
     "（Teléfonos: +51(1) 224-4609），haiku-5.5 把它写进了摘要。"),
    ("问题", "引导句经常超出 48 token：96 次调用中 8 次重试是因为引导句超长，haiku-5.5 在 c0006 上重试后仍超长（53）。"
     "给模型的“约多少词”按 0.6 词/token 换算，对西班牙文偏松（实测约 0.65 词/token 仍超）；建议改为 0.5。"),
    ("结论", "PO 25-19 数量表：8 个模型都把分组标题（含型号，如 MODEL HXE12ES）写进了它所管的每一行，"
     "29 条记录对应 29 个数据行，没有模型把分组标题当成合计。合并单元格的语义问题（C2）在这张表上解决了。"),
    ("结论", "LLM 的记录比规则序列化更长（每条约 130–170 token），PO 25-19 数量表切成 9–14 个 chunk，"
     "与规则序列化的 12 个相当：LLM 序列化带来的是“每块自足”，而不是块数减少。"
     "gemini-4-argon 在每个值前重复完整的多级表头，最冗长（14 块）。"),
    ("结论", "gemini-3.8-flash 没有合并被拆开的词（REQUERIMI ENTO、ENTREG A），其他模型都合并了。"),
    ("结论", "引导句：8 个模型都能区分 c0004（第三条 合同标的）与 c0006（第四条 技术规格）。"),
    ("结论", "摘要：8 个模型都在 313–469 token 之间，都列全了 13 家公司和 4 个分组标题。"),
]


def findings() -> str:
    items = "".join(f"<li>{badge(kind, 'bad' if kind == '问题' else 'ok')} {esc(text)}</li>"
                    for kind, text in FINDINGS)
    return f'<div class="card"><h3>结论与问题</h3><ul>{items}</ul></div>'


def main() -> None:
    price = prices()
    inputs = {key: json.loads((RESULTS / "_inputs" / f"{key}.json").read_text(encoding="utf-8"))
              for key, *_ in TABLES}
    data = {(m, key): load(m, key) for m in MODELS for key, *_ in TABLES}
    tabs = ['<a href="#overview" data-tab="overview">总览</a>'] + [
        f'<a href="#{key}" data-tab="{key}">{esc(title)}</a>' for key, _, _, title in TABLES]
    panels = [f'<section data-panel="overview">{findings()}{overview(data, price)}</section>']
    panels += [table_panel(key, title, inputs[key], data) for key, _, _, title in TABLES]
    body = (f'<h1>V3.13 样例审核：LLM 序列化、引导句、摘要</h1>'
            f'<div class="sub">8 个模型 × 4 张表，提示词 llm_tables_v1（docs/changes/v3.13-prompts.md）。'
            f'请看：记录是否自足、分组标题与合计是否判对、数值是否原样；引导句能否区分 c0004 与 c0006；'
            f'摘要是否写出标识各行的值。</div><div class="tabs">{"".join(tabs)}</div>{"".join(panels)}')
    write_assets(HERE.parents[2])     # repo reports/
    (HERE / "index.html").write_text(page("V3.13 样例审核", body, root=ROOT,
                                          crumbs=[("报告首页", ROOT + "index.html")]), encoding="utf-8")


if __name__ == "__main__":
    main()
