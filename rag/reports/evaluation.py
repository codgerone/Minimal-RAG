"""Evaluation report: verdict first, failures easy to find, expected vs retrieved side by side."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rag.eval.metrics import METRICS
from rag.jsonio import write_atomic
from rag.paths import Workspace
from rag.reports.html import badge, esc, filters, page, pct, text_block

MODE_LABELS = {"confirmed": ("已确认映射", ""), "auto": ("自动判定", "warn"),
               "unmapped": ("找不到对应 chunk", "bad")}


def _delta(key: str, now: float | None, before: float | None, better_up: bool) -> str:
    if now is None or before is None:
        return '<div class="d same">无上次结果</div>'
    diff = now - before
    if abs(diff) < 1e-9:
        return '<div class="d same">与上次相同</div>'
    good = (diff > 0) == better_up
    return f'<div class="d {"up" if good else "down"}">{"+" if diff > 0 else "−"}{abs(diff):.1%} 较上次</div>'


def _case_status(case: dict[str, Any]) -> tuple[str, str, str]:
    """(tag, label, badge kind)."""
    if not case["answerable"]:
        return "unanswerable", "不可回答题", ""
    if case["complete"]:
        return "complete", "✓ 全部证据已检索到", "ok"
    if case["hit"]:
        return "partial", "◐ 部分证据检索到", "warn"
    return "miss", "✗ 未检索到证据", "bad"


def _group_html(group: dict[str, Any]) -> str:
    label, kind = MODE_LABELS[group["mode"]]
    state = (badge(f"第 {group['covered_at_rank']} 位时已覆盖", "ok") if group["covered_at_rank"]
             else badge("未覆盖", "bad"))
    mode = badge(label, kind) if group["mode"] != "confirmed" else ""
    excerpts = "".join(f'<div class="meta">{esc(x["document_name"])} · 第 {",".join(map(str, x["pages"]))} 页</div>'
                       f'{text_block(x["text"], 400)}' for x in group["excerpts"])
    sets = "；".join(" + ".join(s) for s in group["acceptable_sets"]) or "无"
    return (f'<div class="item"><b>证据 {esc(group["group_id"])}</b> {state} {mode}{excerpts}'
            f'<details><summary>可接受的 chunk 组合</summary><div class="meta">{esc(sets)}</div>'
            f'</details></div>')


def _hit_html(hit: dict[str, Any], groups: list[dict[str, Any]]) -> str:
    matched = [g["group_id"] for g in groups if any(hit["chunk_id"] in s for s in g["acceptable_sets"])]
    mark = (badge("✓ 证据 " + "、".join(matched), "ok") if hit["relevant"]
            else badge("跨文档", "bad") if hit["cross_document"] else badge("非证据"))
    return (f'<div class="item"><b>#{hit["rank"]}</b> {mark} '
            f'<span class="meta">{esc(hit["document_name"])} · 第 {",".join(map(str, hit["pages"]))} 页'
            f' · 相似度 {hit["similarity"]:.3f}</span>{text_block(hit["text"], 280)}'
            f'<details><summary>{esc(hit["chunk_id"])}</summary><div class="meta">距离 {hit["distance"]:.6f}'
            f'，类型 {esc(hit["kind"])}</div></details></div>')


def render(result: dict[str, Any], previous: dict[str, Any] | None) -> str:
    metrics = result["metrics"]
    tiles = "".join(
        f'<div class="tile"><div class="k">{esc(label)}</div><div class="v">{pct(metrics[key]["value"])}</div>'
        + _delta(key, metrics[key]["value"],
                 previous["metrics"][key]["value"] if previous else None, better_up) + "</div>"
        for key, label, better_up in METRICS)
    cases = result["cases"]
    statuses = [_case_status(c) for c in cases]
    modes = result["evidence_modes"]
    notices = []
    if modes["auto"] or modes["unmapped"]:
        notices.append(f'{modes["auto"]} 个证据组由自动判定、{modes["unmapped"]} 个找不到对应 chunk'
                       f'（分块变化后没有已确认的映射）。请核对标有「自动判定」的题目；确认无误后运行 '
                       f'<code>python -m rag eval --config {esc(result["config"])} --confirm-auto</code> 保存。')
    if result["dataset"]["pdf_changed_since_labelling"]:
        notices.append("部分 PDF 在标注之后被修改过，标注证据可能已过时。")
    notice_html = "".join(f'<div class="card">⚠ {n}</div>' for n in notices)
    doc_rows = "".join(
        f'<tr><td><a href="#doc-{esc(doc_id)}">{esc(info["document_name"])}</a></td>'
        f'<td class="num">{info["questions"]}</td>'
        + "".join(f'<td class="num">{pct(info["metrics"][key]["value"])}</td>' for key, _, _ in METRICS)
        + "</tr>" for doc_id, info in result["documents"].items())
    doc_table = (f'<div class="scroll"><table><tr><th>文档</th><th class="num">题数</th>'
                 + "".join(f'<th class="num">{esc(label)}</th>' for _, label, _ in METRICS)
                 + f"</tr>{doc_rows}</table></div>")
    has_cross = [any(h["cross_document"] for h in c["hits"]) for c in cases]
    has_auto = [any(g["mode"] != "confirmed" for g in c["groups"]) for c in cases]
    bar = filters([("all", "全部", len(cases)),
                   ("miss", "未命中", sum(s[0] == "miss" for s in statuses)),
                   ("partial", "部分覆盖", sum(s[0] == "partial" for s in statuses)),
                   ("complete", "完整覆盖", sum(s[0] == "complete" for s in statuses)),
                   ("cross", "有跨文档结果", sum(has_cross)),
                   ("auto", "含自动判定", sum(has_auto))])
    blocks = []
    current_doc = None
    for case, (tag, label, kind), cross, auto in zip(cases, statuses, has_cross, has_auto):
        if case["document_id"] != current_doc:
            current_doc = case["document_id"]
            blocks.append(f'<h2 id="doc-{esc(current_doc)}">{esc(case["document_name"])}</h2>')
        tags = " ".join(["all", tag] + (["cross"] if cross else []) + (["auto"] if auto else []))
        cross_count = sum(h["cross_document"] for h in case["hits"])
        cross_note = (f' <span class="meta">· {cross_count}/{len(case["hits"])} 条结果来自其他文档</span>'
                      if cross_count else "")
        blocks.append(
            f'<div class="card" data-tags="{tags}"><h3>{badge(label, kind)} {esc(case["case_id"])}{cross_note}</h3>'
            f'<div>{esc(case["question"])}</div>'
            f'<details><summary>参考答案</summary><div class="meta">{esc(case["reference_answer"])}</div></details>'
            f'<div class="cols"><div><h3>标准证据</h3>{"".join(_group_html(g) for g in case["groups"])}</div>'
            f'<div><h3>实际检索 Top-{result["top_k"]}</h3>'
            f'{"".join(_hit_html(h, case["groups"]) for h in case["hits"]) or "<div class=meta>无结果</div>"}'
            f'</div></div></div>')
    settings = result["build_settings"]
    body = (f'<h1>检索评估 · {esc(result["config"])} · K={result["top_k"]}</h1>'
            f'<div class="sub">{esc(result["created_at"])} · {result["dataset"]["answerable"]} 道可回答问题'
            f'（数据集 {esc(result["dataset"]["version"])}）· 构建指纹 {esc(result["fingerprint"][:12])}</div>'
            f'<div class="tiles">{tiles}</div>{notice_html}'
            f'<h2>按文档</h2>{doc_table}'
            f'<details><summary>本次使用的构建配置</summary><pre>{esc(_pretty(settings))}</pre></details>'
            f'<h2>逐题明细</h2>{bar}{"".join(blocks)}'
            f'<div class="meta">指标定义见 docs/rules/evaluation.md；原始数据见同目录 result.json。</div>')
    return page(f"检索评估 · {result['config']} · K={result['top_k']}", body,
                crumbs=[("报告首页", "../../index.html")], scripts=True)


def _pretty(value: Any) -> str:
    import json
    return json.dumps(value, ensure_ascii=False, indent=2)


def write_eval_report(workspace: Workspace, folder: Path, result: dict[str, Any],
                      previous: dict[str, Any] | None) -> None:
    write_atomic(folder / "index.html", render(result, previous))


def rerender_all(workspace: Workspace) -> int:
    """Re-render every saved evaluation page from its result.json (after report style changes)."""
    from rag.jsonio import read_json
    folder = workspace.eval_reports()
    count = 0
    for path in sorted(folder.glob("*/result.json")) if folder.is_dir() else []:
        result = read_json(path)
        write_eval_report(workspace, path.parent, result, _latest_before(workspace, result))
        count += 1
    return count


def _latest_before(workspace: Workspace, result: dict[str, Any]) -> dict[str, Any] | None:
    from rag.jsonio import read_json
    runs = [read_json(p) for p in workspace.eval_reports().glob(f"*_{result['config']}_k{result['top_k']}*/result.json")]
    earlier = [r for r in runs if r["created_at"] < result["created_at"]]
    return max(earlier, key=lambda r: r["created_at"]) if earlier else None
