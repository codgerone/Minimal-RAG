"""Comparison page: several evaluation runs side by side, changes against a baseline run.

Runs with the same build settings apart from the rules version, the embedder, the BM25 analyzer and
the table processing (and the same K) share a tab, whatever their retriever and table return unit; the first run given in each tab is its
baseline. When the tabs differ only in K, they are titled by K. A run may be given a display name (`<run>=<name>`, e.g. `V3.9`), shown before its settings. A question improves when its status moves up
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
from rag.reports.evaluation import unit_chunks
from rag.reports.table_compare import TableSpec, table_tabs
from rag.reports.html import badge, esc, filters, hit_scores, page, pct, text_block, write_assets

LEVELS = {"miss": (0, "✗ 未命中", "bad"), "partial": (1, "◐ 部分", "warn"), "complete": (2, "✓ 完整", "ok")}
RECURRING_MIN = 3   # an irrelevant chunk listed when it is in the Top-K of at least this many questions
# Table processing settings: runs that differ only in them are compared in one tab.
TABLE_SETTINGS = ("table_structure", "table_formatter", "table_index_text", "table_llm")


class CompareError(RuntimeError):
    pass


def _status(case: dict[str, Any]) -> str:
    return "complete" if case["complete"] else "partial" if case["hit"] else "miss"


def _without_identity(component: dict[str, Any] | None) -> dict[str, Any] | None:
    if component is None:
        return None
    return {k: v for k, v in component.items() if k not in ("identity", "rules", "libraries")}


def _group_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Parser, table extractors, chunker and OCR settings without rules or library versions, in one
    form for results recorded per build step (V4.1.1 on) and for the earlier flat records."""
    if "parse" in settings:
        extraction = settings.get("table_extraction") or {}
        return {"parser": _without_identity(settings["parse"]),
                "table_extractors": [_without_identity(e) for e in extraction.get("extractors", [])],
                "chunker": _without_identity(settings["chunk"]),
                **({"ocr": _without_identity(settings["ocr"])} if "ocr" in settings else {})}
    return {k: v for k, v in settings.items()
            if k not in ("rules_version", "embedder", "embedder_identity", "lexical", "ocr_identity",
                         *TABLE_SETTINGS)}


def _embedder_model(settings: dict[str, Any]) -> str:
    identity = settings["embed"]["identity"] if "embed" in settings else settings.get("embedder_identity", {})
    return str(identity.get("model", "?")).split("/")[-1]


def _group_key(result: dict[str, Any]) -> str:
    import json
    return json.dumps([_group_settings(result["build_settings"]), result["top_k"]],
                      sort_keys=True, ensure_ascii=False)


def _model(result: dict[str, Any]) -> str:
    """Display name when given; rules version and embedder model name; the retriever and BM25
    analyzer settings when not plain semantic; 整表 when whole tables are returned; the dataset
    variant (e.g. `+translated`) when there is one."""
    parts = [_embedder_model(result["build_settings"])]
    if "rules_version" in result["build_settings"]:
        parts.insert(0, "规则 " + str(result["build_settings"]["rules_version"]))
    if result.get("display_name"):
        parts.insert(0, str(result["display_name"]))
    retriever = result.get("retriever", "semantic")
    if retriever != "semantic":
        lexical = result["build_settings"].get("lexical") or {}
        parts.append(retriever + "（" + ("去停用词" if lexical.get("stopwords") else "保留停用词")
                     + ("、词干化" if lexical.get("stemming") else "") + "）")
    if result.get("table_return_unit") == "table":
        parts.append("返回整表")
    variant = result["dataset"]["version"].partition("+")[2]
    return " · ".join(parts + ([variant] if variant else []))


def parse_run(spec: str) -> tuple[str, str | None]:
    """`<run>` or `<run>=<display name>`; evaluation directory names never contain `=`."""
    name, _, display = spec.partition("=")
    return Path(name.strip()).name, display.strip() or None


def load_runs(workspace: Workspace, specs: list[str]) -> list[tuple[str, dict[str, Any]]]:
    runs = []
    for spec in specs:
        name, display = parse_run(spec)
        path = workspace.eval_reports() / name / "result.json"
        if not path.is_file():
            raise CompareError(f"找不到评估结果：{path.relative_to(workspace.root).as_posix()}")
        result = read_json(path)
        if display:
            result["display_name"] = display   # in memory only; result.json is not changed
        runs.append((name, result))
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


def _coverage_moves(base: dict[str, Any], other: dict[str, Any]) -> tuple[tuple[int, int], tuple[int, int]]:
    """((gained, base incomplete), (lost, base complete)) over answerable questions."""
    before = {c["case_id"]: c["complete"] for c in base["cases"] if c["answerable"]}
    after = {c["case_id"]: c["complete"] for c in other["cases"] if c["case_id"] in before}
    incomplete = [cid for cid, done in before.items() if not done]
    complete = [cid for cid, done in before.items() if done]
    return ((sum(after[c] for c in incomplete), len(incomplete)),
            (sum(not after[c] for c in complete), len(complete)))


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
        gained, lost = _coverage_moves(base, result)
        faces = ("基线" if index == 0 else
                 f'<span class="up">{gained[0]}</span> / {gained[1]}'
                 f'<div class="meta">失去 <span class="down">{lost[0]}</span> / {lost[1]}</div>')
        counts = (f'{result["metrics"]["hit_rate"]["numerator"]:.0f} / '
                  f'{result["metrics"]["complete_coverage"]["numerator"]:.0f}')
        tokens = result.get("returned_tokens")
        token_cell = f'{tokens["value"]:.0f}<div class="meta">最多 {tokens["max"]}</div>' if tokens else "—"
        rows.append(f'<tr><td><b>{esc(_model(result))}</b><div class="meta">{_run_link(name)} · 数据集 '
                    f'{esc(result["dataset"]["version"])}</div></td><td class="num">{counts}</td>{cells}'
                    f'<td class="num">{token_cell}</td><td class="num">{faces}</td><td class="num">{moved}</td></tr>')
    head = "".join(f'<th class="num">{esc(label)}</th>' for _, label, _ in METRICS)
    return (f'<div class="scroll"><table><tr><th>设置 / 运行</th><th class="num">命中 / 完整覆盖（题）</th>'
            f'{head}<th class="num">每题返回 token</th>'
            f'<th class="num" title="改善面：基线未完整覆盖的题中，本运行完整覆盖的题数；回归面：基线完整覆盖的题中，本运行未完整覆盖的题数">'
            f'改善面 / 回归面</th><th class="num">较基线 变好 / 变差</th></tr>'
            f'{"".join(rows)}</table></div>'
            f'<div class="meta">改善面：基线未完整覆盖的 N 题中，本运行完整覆盖 x 题，记为 x / N；'
            f'回归面：基线完整覆盖的 M 题中，本运行未完整覆盖 y 题，记为"失去 y / M"。'
            f'变好 / 变差按 未命中 &lt; 部分覆盖 &lt; 完整覆盖 的状态比较。</div>')


def _by_type(runs: list[tuple[str, dict[str, Any]]]) -> str:
    """Per document type, the summary metrics of every run and its changes against the baseline,
    counted over that type's answerable questions; runs before V4.1 do not record document types."""
    if not all(r.get("document_types") for _, r in runs):
        return '<div class="meta">有运行未记录文档类型（V4.1 之前的评估）。</div>'
    base = runs[0][1]
    type_of = {doc: info.get("document_type") for doc, info in base["documents"].items()}
    sections = []
    for kind, base_info in base["document_types"].items():
        cases = {c["case_id"] for c in base["cases"] if c["answerable"] and type_of.get(c["document_id"]) == kind}
        rows = []
        for index, (name, result) in enumerate(runs):
            info = result["document_types"].get(kind)
            if info is None:
                continue
            changes = {cid: v for cid, v in _changes(base, result).items() if cid in cases}
            cells = "".join(
                f'<td class="num">{pct(info["metrics"][key]["value"])}'
                f'{"" if index == 0 else _delta(info["metrics"][key]["value"], base_info["metrics"][key]["value"], up)}</td>'
                for key, _, up in METRICS)
            moved = ("基线" if index == 0 else
                     f'<span class="up">↑{sum(v > 0 for v in changes.values())}</span> / '
                     f'<span class="down">↓{sum(v < 0 for v in changes.values())}</span>')
            counts = (f'{info["metrics"]["hit_rate"]["numerator"]:.0f} / '
                      f'{info["metrics"]["complete_coverage"]["numerator"]:.0f}')
            tokens = (info.get("returned_tokens") or {}).get("value")
            rows.append(f'<tr><td><b>{esc(_model(result))}</b></td><td class="num">{counts}</td>{cells}'
                        f'<td class="num">{"—" if tokens is None else f"{tokens:.0f}"}</td>'
                        f'<td class="num">{moved}</td></tr>')
        head = "".join(f'<th class="num">{esc(label)}</th>' for _, label, _ in METRICS)
        sections.append(
            f'<h3>{esc(base_info["label"])}<span class="meta"> · {base_info["documents"]} 份文档 · '
            f'{base_info["answerable"]} 道可回答题</span></h3>'
            f'<div class="scroll"><table><tr><th>设置 / 运行</th><th class="num">命中 / 完整覆盖（题）</th>{head}'
            f'<th class="num">每题返回 token</th><th class="num">较基线 变好 / 变差</th></tr>{"".join(rows)}</table></div>')
    return ("".join(sections) + '<div class="meta">每题按它所属文档的类型归类；类型由入库时的页面判定得出。'
            '变好 / 变差只计该类型的题。</div>')


def _required_k(runs: list[tuple[str, dict[str, Any]]], tab: str) -> str:
    """Evidence groups whose required K (leading results the full ranking needs to cover them)
    differs between runs; runs before V3.12 do not record it."""
    if not all(any("required_k" in g for c in r["cases"] for g in c.get("groups", ())) for _, r in runs):
        return '<div class="meta">有运行未记录所需 K（V3.12 之前的评估）。</div>'
    by_run = [{c["case_id"]: c for c in result["cases"]} for _, result in runs]
    rows, same = [], 0
    for case in runs[0][1]["cases"]:
        if not case["answerable"]:
            continue
        for index, group in enumerate(case["groups"]):
            values = [run[case["case_id"]]["groups"][index].get("required_k") for run in by_run]
            if len(set(values)) == 1:
                same += 1
                continue
            base = values[0]
            cells = []
            for i, value in enumerate(values):
                text = "无法覆盖" if value is None else str(value)
                better = base is None or (value is not None and value < base)
                cls = "" if i == 0 or value == base else "up" if better else "down"
                cells.append(f'<td class="num"><span class="{cls}">{text}</span></td>')
            rows.append(f'<tr><td><a href="#{esc(tab)}-{esc(case["case_id"])}">{esc(case["case_id"])}</a>&nbsp;'
                        f'{esc(group["group_id"])}<div class="meta">{esc(group["information_item"])}</div></td>'
                        f'{"".join(cells)}</tr>')
    head = "".join(f'<th class="num">{esc(_model(result))}</th>' for _, result in runs)
    note = f'<div class="meta">其余 {same} 个证据组在各运行中所需 K 相同。</div>'
    if not rows:
        return note
    return f'<div class="scroll"><table><tr><th style="min-width:240px">题目 · 证据组</th>{head}</tr>{"".join(rows)}</table></div>{note}'


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


def _hit(hit: dict[str, Any], retriever: str) -> str:
    state = badge("相关", "ok") if hit["relevant"] else badge("不相关")
    table = badge(f"整表 · {len(unit_chunks(hit))} 块") if hit.get("table_id") else ""
    ids = f'整表 {hit["table_id"]}，排名来自 {hit["chunk_id"]}' if hit.get("table_id") else hit["chunk_id"]
    return (f'<div class="item"><b>#{hit["rank"]}</b> {state} {table} <span class="meta">{esc(hit_scores(hit, retriever))}'
            f' · 第 {",".join(map(str, hit["pages"]))} 页</span>{text_block(hit["text"], 160)}'
            f'<details><summary>ID</summary><div class="meta">{esc(ids)}</div></details></div>')


def _cards(runs, by_run, tags_of: dict[str, str], tab: str) -> str:
    cards = []
    for cid, tags in tags_of.items():
        case = by_run[0][cid]
        columns = "".join(f'<div><h3>{esc(_model(result))}</h3>{"".join(_hit(h, result.get("retriever", "semantic")) for h in by_run[i][cid]["hits"])}</div>'
                          for i, (_, result) in enumerate(runs))
        questions = dict.fromkeys(run[cid]["question"] for run in by_run)
        cards.append(f'<div class="card" id="{esc(tab)}-{esc(cid)}" data-tags="{tags}"><h3>{esc(cid)}</h3>'
                     + "".join(f'<div>{esc(q)}</div>' for q in questions)
                     + f'<details><summary>各运行的 Top-{runs[0][1]["top_k"]}</summary>'
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
                    key = hit.get("table_id") or hit["chunk_id"]
                    counts[key] += 1
                    texts[key] = hit
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


def render(name: str, runs: list[tuple[str, dict[str, Any]]], spec: TableSpec | None = None,
           table_evidence: bool = True) -> str:
    tabs, panels = [], []
    for key, title, panel in table_tabs(runs, spec or TableSpec()) if table_evidence else ():
        tabs.append(f'<a href="#{key}" data-tab="{key}">{esc(title)}</a>')
        panels.append(f'<div data-panel="{key}">{panel}</div>')
    groups: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for run in runs:
        groups.setdefault(_group_key(run[1]), []).append(run)
    used: set[str] = set()
    # groups that differ only in K (budgets of the same runs) are titled by K
    keyed = [(members[0][1]["config"], members[0][1]["top_k"]) for members in groups.values()]
    by_k = len({c for c, _ in keyed}) == 1 and len({k for _, k in keyed}) == len(keyed) > 1
    for members in groups.values():
        tab = config = members[0][1]["config"]
        title = tab
        if by_k:
            tab, title = f"k{members[0][1]['top_k']}", f"K={members[0][1]['top_k']}"
        while tab in used:   # same config in two groups (e.g. different chunk budgets)
            tab = title = f"{config}_{len(used) + 1}"
        used.add(tab)
        tabs.append(f'<a href="#{esc(tab)}" data-tab="{esc(tab)}">{esc(title)}</a>')
        matrix, cards, tags_of = _matrix(members, tab)
        top_k = members[0][1]["top_k"]
        bar = filters([("all", "全部", len(tags_of)),
                       (f"{tab}-better", "有运行变好", sum(f"{tab}-better" in t.split() for t in tags_of.values())),
                       (f"{tab}-worse", "有运行变差", sum(f"{tab}-worse" in t.split() for t in tags_of.values()))])
        panels.append(
            f'<div data-panel="{esc(tab)}"><h2>指标（K={top_k}，基线 {esc(_model(members[0][1]))}）</h2>'
            f'{_summary(members)}<h2>按文档类型</h2>{_by_type(members)}<h2>逐证据组所需 K 的变化</h2>'
            f'<div class="meta">所需 K：按全量排名，该证据组全部进入前几个结果；整表算一个结果。</div>'
            f'{_required_k(members, tab)}'
            f'<h2>逐题状态</h2>{bar}{matrix}'
            f'<h2>反复进入 Top-{top_k} 的不相关结果（≥{RECURRING_MIN} 题）</h2>{_recurring(members)}'
            f'<h2>逐题 Top-{top_k}</h2>{cards}</div>')
    body = (f'<h1>评估对比 · {esc(name)}</h1>'
            f'<div class="sub">{len(runs)} 次评估 · 每个标签页的第一列是基线；状态按 未命中 &lt; 部分覆盖 &lt; 完整覆盖 '
            f'比较，↑ 变好、↓ 变差。未命中：没有一个证据组被完整检索到；部分覆盖：有证据组被完整检索到但不是全部；'
            f'首个相关：排名最靠前的、属于某个证据组的 chunk</div><div class="tabs">{"".join(tabs)}</div>{"".join(panels)}'
            f'<div class="meta">指标定义见 docs/rules/evaluation.md；原始数据见各评估目录的 result.json。</div>')
    return page(f"评估对比 · {name}", body, root="../../", crumbs=[("报告首页", "../../index.html")])


def compare_dir(workspace: Workspace) -> Path:
    return workspace.reports / "compare"


def write_comparison(workspace: Workspace, name: str, run_specs: list[str],
                     table_spec: dict[str, Any] | None = None, table_evidence: bool = True) -> Path:
    """`table_spec`: contrasts, excluded table evidence groups and notes (rag/reports/table_compare.py);
    kept in runs.json so that `report` renders the page again."""
    runs = load_runs(workspace, run_specs)
    folder = compare_dir(workspace) / name
    record: dict[str, Any] = {"name": name, "runs": [spec.strip() for spec in run_specs]}
    if table_spec:
        record["table_spec"] = table_spec
    if not table_evidence:
        record["table_evidence"] = False
    write_json(folder / "runs.json", record)
    write_assets(workspace.reports)
    write_atomic(folder / "index.html", render(name, runs, TableSpec.from_json(table_spec), table_evidence))
    return folder


def rerender_comparisons(workspace: Workspace) -> int:
    count = 0
    for path in sorted(compare_dir(workspace).glob("*/runs.json")) if compare_dir(workspace).is_dir() else []:
        spec = read_json(path)
        write_comparison(workspace, spec["name"], spec["runs"], spec.get("table_spec"),
                         spec.get("table_evidence", True))
        count += 1
    return count
