"""reports/index.html: every config's index state, its documents' audit pages, and all evaluations."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

from rag.config import ConfigError, list_configs
from rag.eval.metrics import METRICS
from rag.jsonio import read_json, write_atomic
from rag.paths import Workspace
from rag.reports.html import badge, esc, page, pct, write_assets

STATE_BADGES = {"current": ("已入库", "ok"), "new": ("未入库", "warn"), "changed": ("PDF 已修改", "warn"),
                "incomplete": ("索引不完整", "bad"), "missing": ("PDF 已删除", ""),
                "unreadable": ("无可用文本", "bad")}


def _config_section(workspace: Workspace, config) -> str:
    from rag.index.builder import current_status
    from rag.registry import assemble
    try:
        assembly = assemble(config)
    except ConfigError as exc:
        return f'<div class="card">{badge("配置无效", "bad")} {esc(exc)}</div>'
    status = current_status(workspace, assembly)
    state = {"not_built": badge("尚未建立索引", "warn"),
             "config_changed": badge("配置已改变，需 ingest --force", "bad"),
             "ready": badge("可用", "ok")}[status.state]
    rows = []
    for item in status.documents:
        label, kind = STATE_BADGES[item.state]
        entry = item.entry
        links = ""
        if entry and item.state != "new":
            folder = workspace.ingest_reports(config.name) / entry.folder
            base = f"ingest/{quote(config.name)}/{quote(entry.folder)}/"
            links = "　".join(f'<a href="{esc(base + quote(name))}">{esc(text)}</a>'
                             for name, text in (("1-解析.html", "解析"), ("2-表格.html", "表格"),
                                                ("3-分块.html", "分块"))
                             if (folder / name).is_file())
        numbers = (f'<td class="num">{entry.page_count}</td><td class="num">{entry.table_count}</td>'
                   f'<td class="num">{entry.chunk_count}</td>' if entry else '<td></td><td></td><td></td>')
        rows.append(f"<tr><td>{esc(item.relative_path)}</td><td>{badge(label, kind)}</td>"
                    f"{numbers}<td>{links}</td></tr>")
    table = (f'<div class="scroll"><table><tr><th>文档</th><th>状态</th><th class="num">页</th>'
             f'<th class="num">表格</th><th class="num">chunk</th><th>审核页</th></tr>{"".join(rows)}</table></div>'
             if rows else '<div class="meta">documents/ 中没有 PDF</div>')
    return (f'<h2 id="{esc(config.name)}">配置 {esc(config.name)} {state}</h2>'
            f'<div class="sub">{esc(config.description)} · configs/{esc(config.name)}.toml · '
            f'构建指纹 {esc(assembly.fingerprint()[:12])}</div>{table}')


def _eval_section(workspace: Workspace) -> str:
    folder = workspace.eval_reports()
    runs = sorted((p for p in folder.glob("*/result.json")), reverse=True) if folder.is_dir() else []
    if not runs:
        return '<h2>检索评估</h2><div class="meta">还没有评估记录：python -m rag eval --config NAME</div>'
    rows = []
    for path in runs:
        result = read_json(path)
        modes = result["evidence_modes"]
        note = badge(f"{modes['auto']} 组自动判定", "warn") if modes["auto"] else ""
        rows.append(f'<tr><td><a href="eval/{esc(quote(path.parent.name))}/index.html">{esc(path.parent.name)}</a> {note}</td>'
                    f'<td>{esc(result["config"])}</td><td class="num">{result["top_k"]}</td>'
                    + "".join(f'<td class="num">{pct(result["metrics"][key]["value"])}</td>'
                              for key, _, _ in METRICS) + "</tr>")
    head = "".join(f'<th class="num">{esc(label)}</th>' for _, label, _ in METRICS)
    return (f'<h2>检索评估</h2><div class="scroll"><table><tr><th>运行</th><th>配置</th><th class="num">K</th>'
            f'{head}</tr>{"".join(rows)}</table></div>')


def write_index_page(workspace: Workspace) -> None:
    configs = list_configs(workspace.root)
    sections = "".join(_config_section(workspace, config) for config in configs)
    body = (f'<h1>Minimal RAG 报告</h1><div class="sub">生成于 {datetime.now():%Y-%m-%d %H:%M} · '
            f'文档目录 documents/ · 评估数据 eval/ground-truth/</div>'
            f'{_eval_section(workspace)}{sections}')
    write_assets(workspace.reports)
    write_atomic(workspace.reports / "index.html", page("Minimal RAG 报告", body, root=""))
