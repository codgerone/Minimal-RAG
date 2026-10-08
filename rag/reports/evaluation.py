"""Evaluation report: verdict first, failures easy to find, expected vs retrieved side by side."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rag.eval.metrics import METRICS
from rag.jsonio import write_atomic
from rag.paths import Workspace
from rag.reports.html import badge, esc, filters, page, pct, text_block, write_assets

MODE_LABELS = {"coordinate": ("坐标判定", ""), "fallback": ("文字兜底", "warn"),
               "unmapped": ("找不到对应 chunk", "bad"),
               # runs before V3.4 (mapping file + text-overlap judging)
               "confirmed": ("已确认映射", ""), "auto": ("自动判定", "warn")}
PLAIN_MODES = {"coordinate", "confirmed"}   # modes that need no checking, shown without a badge


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


def _excerpt_html(x: dict[str, Any], case: dict[str, Any]) -> str:
    source = "" if x["document_name"] == case["document_name"] else f' · {esc(x["document_name"])}'
    header = " " + badge("表头") if x.get("table_header") else ""
    return (f'<div class="meta">{esc(x["excerpt_id"])}{source} · 第 {",".join(map(str, x["pages"]))} 页{header}</div>'
            f'{text_block(x["text"], 300)}')


def _legacy_group_html(group: dict[str, Any]) -> str:
    """Runs before dataset 3.0.0: excerpts sit directly in the group and must all be retrieved."""
    label, kind = MODE_LABELS[group["mode"]]
    state = (badge(f"第 {group['covered_at_rank']} 位时已覆盖", "ok") if group["covered_at_rank"]
             else badge("未覆盖", "bad"))
    mode = badge(label, kind) if group["mode"] not in PLAIN_MODES else ""
    excerpts = "".join(f'<div class="meta">{esc(x["document_name"])} · 第 {",".join(map(str, x["pages"]))} 页</div>'
                       f'{text_block(x["text"], 400)}' for x in group["excerpts"])
    sets = "；".join(" + ".join(s) for s in group["acceptable_sets"]) or "无"
    return (f'<div class="item"><b>证据 {esc(group["group_id"])}</b> {state} {mode}{excerpts}'
            f'<details><summary>可接受的 chunk 组合</summary><div class="meta">{esc(sets)}</div>'
            f'</details></div>')


def _group_html(group: dict[str, Any], case: dict[str, Any], top_k: int) -> str:
    if "schemes" not in group:
        return _legacy_group_html(group)
    excerpts = {x["excerpt_id"]: x for x in case["excerpts"]}
    retrieved = {h["chunk_id"] for h in case["hits"]}
    state = (badge(f"第 {group['covered_at_rank']} 位时已覆盖", "ok") if group["covered_at_rank"]
             else badge("未覆盖", "bad"))
    label, kind = MODE_LABELS[group["mode"]]
    mode = badge(label, kind) if group["mode"] not in PLAIN_MODES else ""
    fewest = min((len(s) for s in group["acceptable_sets"]), default=0)
    beyond = badge(f"至少需 {fewest} 个 chunk，K={top_k} 时不可能覆盖", "bad") if fewest > top_k else ""
    schemes = []
    for n, scheme in enumerate(group["schemes"]):
        got = any(set(s) <= retrieved for s in scheme["acceptable_sets"])
        flags = [x for x in scheme["excerpt_ids"] if excerpts[x]["mode"] not in PLAIN_MODES]
        mark = badge("已检索到", "ok") if got else badge("未检索到")
        auto = "".join(badge(f"{x} {MODE_LABELS[excerpts[x]['mode']][0]}", MODE_LABELS[excerpts[x]["mode"]][1])
                       for x in flags)
        sets = "；".join(" + ".join(s) for s in scheme["acceptable_sets"]) or "无"
        schemes.append(
            (f'<div class="meta" style="margin:4px 0 0 4px">或</div>' if n else "")
            + f'<div style="border-left:3px solid var(--line);padding-left:10px;margin:4px 0">'
              f'<div><b>方案 {esc(scheme["scheme_id"])}</b> = {esc(" + ".join(scheme["excerpt_ids"]))} '
              f'{badge("须全部检索到") if len(scheme["excerpt_ids"]) > 1 else ""} {mark} {auto}</div>'
              f'{"".join(_excerpt_html(excerpts[x], case) for x in scheme["excerpt_ids"])}'
              f'<details><summary>可接受的 chunk 组合</summary><div class="meta">{esc(sets)}</div></details></div>')
    note = f'<div class="meta">{esc(group["note"])}</div>' if group.get("note") else ""
    relation = badge(f'{len(group["schemes"])} 套方案，任一即可') if len(group["schemes"]) > 1 else ""
    return (f'<div class="item"><b>证据组 {esc(group["group_id"])}</b> 信息项：{esc(group["information_item"])} '
            f'{state} {relation} {mode} {beyond}{note}{"".join(schemes)}</div>')


SCOPE_REASONS = {"unidentified": "问题中未识别出已登记的标识编码", "no_catalog": "未配置文档标识表",
                 "disabled": "文档过滤已关闭"}


def _scope_html(case: dict[str, Any]) -> str:
    scope = case.get("scope")
    if not scope:
        return ""
    if scope["kind"] == "identified":
        verdict = (badge("✓ 识别正确", "ok") if scope["identified_correctly"]
                   else badge("✗ 与证据文档不一致", "bad"))
        target = f'按标识编码 {esc("、".join(scope["codes"]))} → {esc("、".join(scope["document_names"]))}'
    else:
        verdict = badge("未限定", "warn" if scope["kind"] == "unidentified" else "")
        target = f'全库（{esc(SCOPE_REASONS.get(scope["kind"], scope["kind"]))}）'
    return f'<div class="meta">检索范围：{target} {verdict}</div>'


def filter_state(result: dict[str, Any]) -> str:
    """开启 / 关闭; runs from before the filter existed searched the whole index."""
    summary = result.get("document_scope")
    return "开启" if summary and summary["enabled"] else "关闭"


def _scope_tile(result: dict[str, Any]) -> str:
    summary = result.get("document_scope")
    if not summary:
        return ""
    if not summary["enabled"]:
        return '<div class="tile"><div class="k">文档识别</div><div class="v">关闭</div><div class="d same">未按标识编码过滤</div></div>'
    ratio = summary["identified_correctly"]
    value = ratio["numerator"] / ratio["denominator"] if ratio["denominator"] else None
    note = (f'{ratio["numerator"]}/{ratio["denominator"]} 题' if summary["catalog"]
            else "未配置文档标识表")
    return (f'<div class="tile"><div class="k">文档识别准确率</div><div class="v">{pct(value)}</div>'
            f'<div class="d same">{note}</div></div>')


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
        for key, label, better_up in METRICS) + _scope_tile(result)
    cases = result["cases"]
    statuses = [_case_status(c) for c in cases]
    modes = result["evidence_modes"]
    notices = []
    if "fallback" in modes:
        if modes["fallback"]:
            notices.append(f'{modes["fallback"]} 段 excerpt 落在没有细坐标的区域，按文字兜底判定。'
                           f'请抽查标有「文字兜底」的题目。')
    elif modes.get("auto") or modes.get("unmapped"):
        unit = "段 excerpt" if result["cases"] and "excerpts" in result["cases"][0] else "个证据组"
        notices.append(f'{modes["auto"]} {unit}由自动判定、{modes["unmapped"]} {unit}找不到对应 chunk'
                       f'（V3.4 之前的旧判定方式）。')
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
    has_auto = [any(g["mode"] not in PLAIN_MODES for g in c["groups"]) for c in cases]
    scope_off = [bool(c.get("scope")) and c["answerable"] and c["scope"]["kind"] != "disabled"
                 and not c["scope"]["identified_correctly"] for c in cases]
    bar = filters([("all", "全部", len(cases)),
                   ("miss", "未命中", sum(s[0] == "miss" for s in statuses)),
                   ("partial", "部分覆盖", sum(s[0] == "partial" for s in statuses)),
                   ("complete", "完整覆盖", sum(s[0] == "complete" for s in statuses)),
                   ("cross", "有跨文档结果", sum(has_cross)),
                   ("auto", "含文字兜底" if "fallback" in modes else "含自动判定", sum(has_auto))]
                  + ([("scope", "文档识别有误", sum(scope_off))] if any(scope_off) else []))
    blocks = []
    current_doc = None
    for case, (tag, label, kind), cross, auto, off in zip(cases, statuses, has_cross, has_auto, scope_off):
        if case["document_id"] != current_doc:
            current_doc = case["document_id"]
            blocks.append(f'<h2 id="doc-{esc(current_doc)}">{esc(case["document_name"])}</h2>')
        tags = " ".join(["all", tag] + (["cross"] if cross else []) + (["auto"] if auto else [])
                        + (["scope"] if off else []))
        cross_count = sum(h["cross_document"] for h in case["hits"])
        cross_note = (f' <span class="meta">· {cross_count}/{len(case["hits"])} 条结果来自其他文档</span>'
                      if cross_count else "")
        blocks.append(
            f'<div class="card" data-tags="{tags}"><h3>{badge(label, kind)} {esc(case["case_id"])}{cross_note}</h3>'
            f'<div>{esc(case["question"])}</div>{_scope_html(case)}'
            f'<details><summary>参考答案</summary><div class="meta">{esc(case["reference_answer"])}</div></details>'
            f'<div class="cols"><div><h3>标准证据</h3>{"".join(_group_html(g, case, result["top_k"]) for g in case["groups"])}</div>'
            f'<div><h3>实际检索 Top-{result["top_k"]}</h3>'
            f'{"".join(_hit_html(h, case["groups"]) for h in case["hits"]) or "<div class=meta>无结果</div>"}'
            f'</div></div></div>')
    settings = result["build_settings"]
    body = (f'<h1>检索评估 · {esc(result["config"])} · K={result["top_k"]}</h1>'
            f'<div class="sub">{esc(result["created_at"])} · {result["dataset"]["answerable"]} 道可回答问题'
            f'（数据集 {esc(result["dataset"]["version"])}）· 文档过滤{filter_state(result)}'
            f' · 构建指纹 {esc(result["fingerprint"][:12])}</div>'
            f'<div class="tiles">{tiles}</div>{notice_html}'
            f'<h2>按文档</h2>{doc_table}'
            f'<details><summary>本次使用的构建配置</summary><pre>{esc(_pretty(settings))}</pre></details>'
            f'<h2>逐题明细</h2>{bar}{"".join(blocks)}'
            f'<div class="meta">指标定义见 docs/rules/evaluation.md；原始数据见同目录 result.json。</div>')
    return page(f"检索评估 · {result['config']} · K={result['top_k']}", body,
                root="../../", crumbs=[("报告首页", "../../index.html")])


def _pretty(value: Any) -> str:
    import json
    return json.dumps(value, ensure_ascii=False, indent=2)


def write_eval_report(workspace: Workspace, folder: Path, result: dict[str, Any],
                      previous: dict[str, Any] | None) -> None:
    write_assets(workspace.reports)
    write_atomic(folder / "index.html", render(result, previous))


def rerender_all(workspace: Workspace) -> int:
    """Re-render every saved evaluation page from its result.json (after report style changes)."""
    from rag.jsonio import read_json
    folder = workspace.eval_reports()
    count = 0
    for path in sorted(folder.glob("*/result.json")) if folder.is_dir() else []:
        result = read_json(path)
        write_eval_report(workspace, path.parent, result, latest_before(workspace, result))
        count += 1
    return count


def latest_before(workspace: Workspace, result: dict[str, Any]) -> dict[str, Any] | None:
    """Latest earlier run with the same config, K and dataset version, for the 'change since last run' line."""
    from rag.jsonio import read_json
    folder = workspace.eval_reports()
    runs = [read_json(p) for p in folder.glob("*/result.json")] if folder.is_dir() else []
    earlier = [r for r in runs if r["config"] == result["config"] and r["top_k"] == result["top_k"]
               and r["dataset"]["version"] == result["dataset"]["version"]
               and r["created_at"] < result["created_at"]]
    return max(earlier, key=lambda r: r["created_at"]) if earlier else None
