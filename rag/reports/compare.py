"""Comparison page: several evaluation runs side by side, changes against a baseline run.

Runs with the same build settings apart from the embedder (and the same K) share a tab; the
first run given in each tab is its baseline. A question improves when its status moves up
(未命中 < 部分覆盖 < 完整覆盖) against the baseline and regresses when it moves down.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import quote

from rag.eval.metrics import METRICS
from rag.jsonio import read_json, write_atomic, write_json
from rag.paths import Workspace
from rag.reports.html import badge, esc, filters, page, pct, text_block, write_assets

LEVELS = {"miss": (0, "✗ 未命中", "bad"), "partial": (1, "◐ 部分", "warn"), "complete": (2, "✓ 完整", "ok")}
RECURRING_MIN = 3   # an irrelevant chunk listed when it is in the Top-K of at least this many questions


class CompareError(RuntimeError):
    pass


def _status(case: dict[str, Any]) -> str:
    return "complete" if case["complete"] else "partial" if case["hit"] else "miss"


def _group_key(result: dict[str, Any]) -> str:
    settings = {k: v for k, v in result["build_settings"].items() if k not in ("embedder", "embedder_identity")}
    import json
    return json.dumps([settings, result["top_k"]], sort_keys=True, ensure_ascii=False)


def _model(result: dict[str, Any]) -> str:
    """Embedder model name, plus the dataset variant (e.g. `+translated`) when there is one."""
    model = str(result["build_settings"].get("embedder_identity", {}).get("model", "?")).split("/")[-1]
    variant = result["dataset"]["version"].partition("+")[2]
    return f"{model} · {variant}" if variant else model


def load_runs(workspace: Workspace, names: list[str]) -> list[tuple[str, dict[str, Any]]]:
    runs = []
    for name in names:
        path = workspace.eval_reports() / Path(name).name / "result.json"
        if not path.is_file():
            raise CompareError(f"找不到评估结果：{path.relative_to(workspace.root).as_posix()}")
        runs.append((path.parent.name, read_json(path)))
    first = {c["case_id"] for c in runs[0][1]["cases"]}
    for name, result in runs[1:]:
        if {c["case_id"] for c in result["cases"]} != first:
            raise CompareError(f"{name} 的题目与 {runs[0][0]} 不同，不能对比")
    return runs


def _changes(base: dict[str, Any], other: dict[str, Any]) -> dict[str, int]:
    """case_id → +1 improved / -1 regressed / 0 same status, answerable questions only."""
    before = {c["case_id"]: c for c in base["cases"] if c["answerable"]}
    result = {}
    for case in other["cases"]:
        if case["case_id"] in before:
            diff = LEVELS[_status(case)][0] - LEVELS[_status(before[case["case_id"]])][0]
            result[case["case_id"]] = (diff > 0) - (diff < 0)
    return result


def _delta(now: float | None, before: float | None, better_up: bool) -> str:
    if now is None or before is None or abs(now - before) < 1e-9:
        return ""
    good = (now > before) == better_up
    return f' <span class="{"up" if good else "down"}">{"+" if now > before else "−"}{abs(now - before):.1%}</span>'


def _run_link(name: str) -> str:
    return f'<a href="../../eval/{esc(quote(name))}/index.html">{esc(name)}</a>'


def _summary(runs: list[tuple[str, dict[str, Any]]]) -> str:
    base = runs[0][1]
    rows = []
    for index, (name, result) in enumerate(runs):
        changes = _changes(base, result)
        cells = "".join(
            f'<td class="num">{pct(result["metrics"][key]["value"])}'
            f'{"" if index == 0 else _delta(result["metrics"][key]["value"], base["metrics"][key]["value"], up)}</td>'
            for key, _, up in METRICS)
        moved = ("基线" if index == 0 else
                 f'<span class="up">↑{sum(v > 0 for v in changes.values())}</span> / '
                 f'<span class="down">↓{sum(v < 0 for v in changes.values())}</span>')
        counts = (f'{result["metrics"]["hit_rate"]["numerator"]:.0f} / '
                  f'{result["metrics"]["complete_coverage"]["numerator"]:.0f}')
        rows.append(f'<tr><td><b>{esc(_model(result))}</b><div class="meta">{_run_link(name)} · 数据集 '
                    f'{esc(result["dataset"]["version"])}</div></td><td class="num">{counts}</td>{cells}'
                    f'<td class="num">{moved}</td></tr>')
    head = "".join(f'<th class="num">{esc(label)}</th>' for _, label, _ in METRICS)
    return (f'<div class="scroll"><table><tr><th>编码器 / 运行</th><th class="num">命中 / 完整覆盖（题）</th>'
            f'{head}<th class="num">较基线 变好 / 变差</th></tr>{"".join(rows)}</table></div>')


def _matrix(runs: list[tuple[str, dict[str, Any]]], tab: str) -> tuple[str, str, dict[str, str]]:
    base = runs[0][1]
    changes = [_changes(base, result) for _, result in runs]
    by_run = [{c["case_id"]: c for c in result["cases"]} for _, result in runs]
    tags_of: dict[str, str] = {}
    rows, current_doc = [], None
    for case in base["cases"]:
        if not case["answerable"]:
            continue
        cid = case["case_id"]
        moves = [ch.get(cid, 0) for ch in changes[1:]]
        tags = ["all"] + (["better"] if any(m > 0 for m in moves) else []) + (["worse"] if any(m < 0 for m in moves) else [])
        tags_of[cid] = " ".join(f"{tab}-{t}" if t != "all" else t for t in tags)
        if case["document_id"] != current_doc:
            current_doc = case["document_id"]
            rows.append(f'<tr><td colspan="{len(runs) + 1}"><b>{esc(case["document_name"])}</b></td></tr>')
        cells = []
        for index, run in enumerate(by_run):
            other = run[cid]
            _, label, kind = LEVELS[_status(other)]
            rank = other["first_relevant_rank"]
            move = changes[index].get(cid, 0) if index else 0
            mark = {1: ' <span class="up">↑</span>', -1: ' <span class="down">↓</span>'}.get(move, "")
            cells.append(f'<td>{badge(label, kind)} <span class="meta">首个相关 '
                         f'{"#" + str(rank) if rank else "—"}</span>{mark}</td>')
        rows.append(f'<tr data-tags="{tags_of[cid]}"><td><a href="#{esc(tab)}-{esc(cid)}">{esc(cid)}</a></td>'
                    f'{"".join(cells)}</tr>')
    head = "".join(f'<th>{esc(_model(result))}{"（基线）" if i == 0 else ""}</th>'
                   for i, (_, result) in enumerate(runs))
    table = (f'<div class="scroll" data-grid><table><tr><th>题目</th>{head}</tr>{"".join(rows)}</table></div>')
    return table, _cards(runs, by_run, tags_of, tab), tags_of


def _hit(hit: dict[str, Any]) -> str:
    state = badge("相关", "ok") if hit["relevant"] else badge("不相关")
    return (f'<div class="item"><b>#{hit["rank"]}</b> {state} <span class="meta">相似度 {hit["similarity"]:.4f}'
            f' · 第 {",".join(map(str, hit["pages"]))} 页</span>{text_block(hit["text"], 160)}'
            f'<details><summary>chunk ID</summary><div class="meta">{esc(hit["chunk_id"])}</div></details></div>')


def _cards(runs, by_run, tags_of: dict[str, str], tab: str) -> str:
    cards = []
    for cid, tags in tags_of.items():
        case = by_run[0][cid]
        columns = "".join(f'<div><h3>{esc(_model(result))}</h3>{"".join(_hit(h) for h in by_run[i][cid]["hits"])}</div>'
                          for i, (_, result) in enumerate(runs))
        questions = dict.fromkeys(run[cid]["question"] for run in by_run)
        cards.append(f'<div class="card" id="{esc(tab)}-{esc(cid)}" data-tags="{tags}"><h3>{esc(cid)}</h3>'
                     + "".join(f'<div>{esc(q)}</div>' for q in questions)
                     + f'<details><summary>各编码器的 Top-{runs[0][1]["top_k"]}</summary>'
                     f'<div class="grid">{columns}</div></details></div>')
    return "".join(cards)


def _recurring(runs: list[tuple[str, dict[str, Any]]]) -> str:
    blocks = []
    for name, result in runs:
        counts: Counter[str] = Counter()
        texts: dict[str, dict[str, Any]] = {}
        for case in result["cases"]:
            for hit in case["hits"]:
                if not hit["relevant"]:
                    counts[hit["chunk_id"]] += 1
                    texts[hit["chunk_id"]] = hit
        items = [(cid, n) for cid, n in counts.most_common() if n >= RECURRING_MIN]
        body = "".join(f'<div class="item"><b>{n} 题</b> <span class="meta">{esc(texts[cid]["document_name"])} · '
                       f'第 {",".join(map(str, texts[cid]["pages"]))} 页</span>'
                       f'{text_block(texts[cid]["text"], 160)}'
                       f'<details><summary>chunk ID</summary><div class="meta">{esc(cid)}</div></details></div>'
                       for cid, n in items)
        blocks.append(f'<div class="card"><h3>{esc(_model(result))} '
                      f'<span class="meta">共 {sum(n for _, n in items)} 次</span></h3>'
                      f'{body or "<div class=meta>无</div>"}</div>')
    return f'<div class="grid">{"".join(blocks)}</div>'


def render(name: str, runs: list[tuple[str, dict[str, Any]]]) -> str:
    groups: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for run in runs:
        groups.setdefault(_group_key(run[1]), []).append(run)
    tabs, panels = [], []
    for members in groups.values():
        tab = members[0][1]["config"]
        tabs.append(f'<a href="#{esc(tab)}" data-tab="{esc(tab)}">{esc(tab)}</a>')
        matrix, cards, tags_of = _matrix(members, tab)
        top_k = members[0][1]["top_k"]
        bar = filters([("all", "全部", len(tags_of)),
                       (f"{tab}-better", "有运行变好", sum(f"{tab}-better" in t.split() for t in tags_of.values())),
                       (f"{tab}-worse", "有运行变差", sum(f"{tab}-worse" in t.split() for t in tags_of.values()))])
        panels.append(
            f'<div data-panel="{esc(tab)}"><h2>指标（K={top_k}，基线 {esc(_model(members[0][1]))}）</h2>'
            f'{_summary(members)}<h2>逐题状态</h2>{bar}{matrix}'
            f'<h2>反复进入 Top-{top_k} 的不相关 chunk（≥{RECURRING_MIN} 题）</h2>{_recurring(members)}'
            f'<h2>逐题 Top-{top_k}</h2>{cards}</div>')
    body = (f'<h1>评估对比 · {esc(name)}</h1>'
            f'<div class="sub">{len(runs)} 次评估 · 每个标签页的第一列是基线；状态按 未命中 &lt; 部分覆盖 &lt; 完整覆盖 '
            f'比较，↑ 变好、↓ 变差。未命中：没有一个证据组被完整检索到；部分覆盖：有证据组被完整检索到但不是全部；'
            f'首个相关：排名最靠前的、属于某个证据组的 chunk</div><div class="tabs">{"".join(tabs)}</div>{"".join(panels)}'
            f'<div class="meta">指标定义见 docs/rules/evaluation.md；原始数据见各评估目录的 result.json。</div>')
    return page(f"评估对比 · {name}", body, root="../../", crumbs=[("报告首页", "../../index.html")])


def compare_dir(workspace: Workspace) -> Path:
    return workspace.reports / "compare"


def write_comparison(workspace: Workspace, name: str, run_names: list[str]) -> Path:
    runs = load_runs(workspace, run_names)
    folder = compare_dir(workspace) / name
    write_json(folder / "runs.json", {"name": name, "runs": [n for n, _ in runs]})
    write_assets(workspace.reports)
    write_atomic(folder / "index.html", render(name, runs))
    return folder


def rerender_comparisons(workspace: Workspace) -> int:
    count = 0
    for path in sorted(compare_dir(workspace).glob("*/runs.json")) if compare_dir(workspace).is_dir() else []:
        spec = read_json(path)
        write_comparison(workspace, spec["name"], spec["runs"])
        count += 1
    return count
