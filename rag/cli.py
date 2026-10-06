"""Command line: python -m rag <command> [--config NAME] ..."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path
from typing import Sequence

from rag.config import ConfigError, list_configs, resolve_config
from rag.paths import Workspace


def _positive(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("必须为正整数")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m rag", description="本地 PDF 知识库 RAG")
    commands = parser.add_subparsers(dest="command", required=True)

    def command(name: str, help_text: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help_text, description=help_text)
        if name != "config":
            sub.add_argument("--config", metavar="NAME",
                             help="装配配置名（configs/NAME.toml），默认 structured 或环境变量 RAG_CONFIG")
        return sub

    command("status", "查看索引状态：哪些文档已入库、需要更新或清理")
    ingest = command("ingest", "把 documents/ 中的 PDF 入库（默认只处理新增和修改的）")
    ingest.add_argument("--file", help="只处理这个文档（相对路径或唯一文件名）")
    ingest.add_argument("--force", action="store_true", help="强制重建；不带 --file 时全量重建")
    ingest.add_argument("--prune", action="store_true", help="同时清理已从 documents/ 删除的文档")
    ingest.add_argument("--all-configs", action="store_true", help="对 configs/ 下所有配置依次入库")
    chunks = command("chunks", "浏览已入库的 chunk")
    chunks.add_argument("--document", help="只看这个文档")
    chunks.add_argument("--page", type=_positive, help="只看这一页")
    chunks.add_argument("--offset", type=int, default=0)
    chunks.add_argument("--limit", type=_positive, default=20)
    for name, text in (("search", "语义检索，显示命中的 chunk"), ("ask", "检索后让 LLM 依据证据回答")):
        sub = command(name, text)
        sub.add_argument("question")
        sub.add_argument("--top-k", type=_positive)
        sub.add_argument("--document", help="只在这个文档内检索")
        if name == "ask":
            sub.add_argument("--debug", action="store_true", help="显示检索命中和发给 LLM 的完整消息")
    chat = command("chat", "多次提问（每轮独立检索，不保留对话记忆）")
    chat.add_argument("--top-k", type=_positive)
    evaluate = command("eval", "用 eval/ground-truth 的标注问题评估检索效果，生成报告")
    evaluate.add_argument("--top-k", type=_positive, help="默认取配置文件 [eval] top_k")
    evaluate.add_argument("--confirm-auto", action="store_true",
                          help="把本次自动判定的证据映射写入 eval/mappings.json（先看报告再用）")
    config = command("config", "查看装配配置")
    config.add_argument("action", choices=["list", "show"])
    config.add_argument("name", nargs="?")
    command("report", "按已保存的结果重新生成评估报告和总入口 reports/index.html")
    return parser


def _out(text: str = "") -> None:
    print(text, flush=True)


def _assembly(workspace: Workspace, name: str | None):
    from rag.registry import assemble
    return assemble(resolve_config(workspace.root, name))


def _status(workspace: Workspace, args: argparse.Namespace) -> int:
    from rag.index.builder import current_status
    from rag.index.status import STATE_LABELS
    from rag.ingest.sources import discover
    from rag.query.scope import CATALOG_FILE, load_catalog
    assembly = _assembly(workspace, args.config)
    sources = discover(workspace.documents)
    status = current_status(workspace, assembly, sources)
    headline = {"not_built": "尚未建立索引", "config_changed": "配置已改变，索引过期（需 ingest --force）",
                "ready": "可用"}[status.state]
    _out(f"配置 {assembly.name}：{headline}")
    for item in status.documents:
        chunks = f"{item.entry.chunk_count} chunks" if item.entry and item.state != "new" else ""
        _out(f"  [{STATE_LABELS[item.state]}] {item.relative_path}  {chunks}")
    todo = [d for d in status.documents if d.needs_ingest]
    if todo:
        _out(f"→ {len(todo)} 个文档需要处理：python -m rag ingest --config {assembly.name}")
    if status.count("missing"):
        _out(f"→ 清理已删除文档：python -m rag ingest --config {assembly.name} --prune")
    catalog = load_catalog(workspace.documents, sources)
    if catalog is None:
        _out(f"文档标识表：未配置（documents/{CATALOG_FILE}），检索不会按标识编码限定范围")
        return 0
    switch = "" if assembly.config.document_filter else "（本配置已关闭文档过滤）"
    _out(f"文档标识表：已登记 {len(catalog.entries)} / {len(sources)} 份 PDF{switch}")
    for path in catalog.unregistered:
        _out(f"  [未登记标识编码] {path}")
    for path in catalog.missing_files:
        _out(f"  [文件不在 documents/ 中，检索时忽略] {path}")
    return 0


def _ingest(workspace: Workspace, args: argparse.Namespace) -> int:
    from rag.index.builder import ingest
    from rag.reports.index_page import write_index_page
    names = [c.name for c in list_configs(workspace.root)] if args.all_configs else [args.config]
    if args.all_configs and args.config:
        raise ConfigError("--all-configs 与 --config 不能同时使用")
    labels = {"added": "新增", "updated": "更新", "rebuilt": "重建", "skipped": "无变化",
              "failed": "失败", "pruned": "已清理", "unreadable": "无可用文本，跳过"}
    failed = False
    for name in names:
        assembly = _assembly(workspace, name)
        _out(f"== 配置 {assembly.name} ==")
        outcomes = ingest(workspace, assembly, file=args.file, force=args.force, prune=args.prune,
                          progress=lambda message: _out(f"  {message}"))
        for item in outcomes:
            extra = f"（{item.chunk_count} chunks）" if item.chunk_count else ""
            error = f"：{item.error}" if item.error else ""
            _out(f"  [{labels[item.action]}] {item.relative_path}{extra}{error}")
            failed |= item.action == "failed"
    write_index_page(workspace)
    _out("审核报告：reports/index.html")
    return 1 if failed else 0


def _require_queryable(workspace: Workspace, assembly) -> None:
    from rag.index.builder import current_status
    status = current_status(workspace, assembly)
    if not status.queryable:
        hint = ("ingest --force" if status.state == "config_changed" else "ingest")
        raise ConfigError(f"配置 {assembly.name} 的索引不可用，请先运行：python -m rag {hint} --config {assembly.name}")
    stale = [d.relative_path for d in status.documents if d.state != "current"]
    if stale:
        _out(f"提示：{len(stale)} 个文档的索引不是最新的（运行 status 查看），结果可能不完整。")


def _document_id(workspace: Workspace, selector: str | None) -> str | None:
    if selector is None:
        return None
    from rag.ingest.sources import discover, select
    return select(selector, discover(workspace.documents)).document_id


def _chunks(workspace: Workspace, args: argparse.Namespace) -> int:
    from rag.index.builder import open_store
    assembly = _assembly(workspace, args.config)
    _require_queryable(workspace, assembly)
    chunks = open_store(workspace, assembly.name).list_chunks(_document_id(workspace, args.document))
    if args.page:
        chunks = [c for c in chunks if args.page in c.pages]
    shown = chunks[args.offset:args.offset + args.limit]
    for chunk in shown:
        _out(f"── {chunk.chunk_id} · {chunk.document_name} · 第 {','.join(map(str, chunk.pages))} 页 · {chunk.kind}")
        _out(chunk.text)
    _out(f"（显示 {len(shown)} / 共 {len(chunks)} 个）")
    return 0


class _Scoper:
    """Resolves each question's search scope from the document catalog and prints it."""

    def __init__(self, workspace: Workspace, assembly, user_selector: str | None = None):
        from rag.ingest.sources import discover, select
        from rag.query.scope import load_catalog
        sources = discover(workspace.documents)
        self.names = {s.document_id: s.relative_path for s in sources}
        self.user_document = select(user_selector, sources).document_id if user_selector else None
        self.enabled = assembly.config.document_filter
        self.catalog = load_catalog(workspace.documents, sources) if self.enabled else None

    def __call__(self, question: str):
        from rag.query.scope import describe, resolve_scope
        scope = resolve_scope(question, self.catalog, self.enabled, self.user_document)
        _out(describe(scope, self.names))
        return scope.document_ids


def _retriever(workspace: Workspace, assembly):
    from rag.index.builder import open_store
    from rag.query.retriever import SemanticRetriever
    return SemanticRetriever(assembly.embedder, open_store(workspace, assembly.name))


def _show_hits(hits) -> None:
    if not hits:
        _out("没有检索到内容。")
    for rank, hit in enumerate(hits, start=1):
        chunk = hit.chunk
        _out(f"#{rank} 相似度 {1 - hit.distance:.4f} · {chunk.document_name} · "
             f"第 {','.join(map(str, chunk.pages))} 页 · {chunk.chunk_id}")
        _out(chunk.text)
        _out()


def _search(workspace: Workspace, args: argparse.Namespace) -> int:
    assembly = _assembly(workspace, args.config)
    _require_queryable(workspace, assembly)
    documents = _Scoper(workspace, assembly, args.document)(args.question)
    hits = _retriever(workspace, assembly).retrieve(
        args.question, args.top_k or assembly.config.top_k, documents)
    _show_hits(hits)
    return 0


def _answer(workspace: Workspace, assembly, retriever, question: str, top_k: int,
            scoper: _Scoper, debug: bool) -> None:
    from rag.query.llm import LLMError
    from rag.query.prompt import build_messages
    hits = retriever.retrieve(question, top_k, scoper(question))
    messages = build_messages(question, hits)
    if debug:
        _show_hits(hits)
        for message in messages:
            _out(f"[{message['role']}]\n{message['content']}\n")
    llm = assembly.llm()
    for attempt in range(2):
        try:
            _out(llm.complete(messages))
            return
        except LLMError as exc:
            retry = exc.code in {"missing_key", "auth_failed"} and attempt == 0 and sys.stdin.isatty()
            if not retry:
                raise
            _out("OpenRouter API Key 缺失或无效。可在 .env 中设置 OPENROUTER_API_KEY。")
            key = getpass.getpass("本次会话使用的 API Key（输入不显示，回车放弃）：").strip()
            if not key:
                raise
            llm.api_key = key


def _ask(workspace: Workspace, args: argparse.Namespace) -> int:
    assembly = _assembly(workspace, args.config)
    _require_queryable(workspace, assembly)
    _answer(workspace, assembly, _retriever(workspace, assembly), args.question,
            args.top_k or assembly.config.top_k, _Scoper(workspace, assembly, args.document), args.debug)
    return 0


def _chat(workspace: Workspace, args: argparse.Namespace) -> int:
    from rag.query.llm import LLMError
    assembly = _assembly(workspace, args.config)
    _require_queryable(workspace, assembly)
    retriever = _retriever(workspace, assembly)
    scoper = _Scoper(workspace, assembly)
    _out(f"配置 {assembly.name}，模型 {assembly.llm().model}。输入问题，空行或 exit 退出。")
    while True:
        try:
            question = input("\n问题> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question or question.lower() in {"exit", "quit"}:
            break
        try:
            _answer(workspace, assembly, retriever, question,
                    args.top_k or assembly.config.top_k, scoper, False)
        except LLMError as exc:
            _out(f"回答失败：{exc}")
    return 0


def _eval(workspace: Workspace, args: argparse.Namespace) -> int:
    from rag.eval.runner import run_evaluation
    from rag.jsonio import read_json
    from rag.reports.index_page import write_index_page
    assembly = _assembly(workspace, args.config)
    top_k = args.top_k or assembly.config.eval_top_k
    folder = run_evaluation(workspace, assembly, top_k, confirm_auto=args.confirm_auto)
    result = read_json(folder / "result.json")
    metrics = result["metrics"]
    modes = result["evidence_modes"]
    _out(f"配置 {assembly.name} · K={top_k} · {result['dataset']['answerable']} 道可回答问题")
    for key, label in (("hit_rate", "命中率"), ("complete_coverage", "完整覆盖率"),
                       ("group_recall", "证据组召回"), ("mrr", "MRR"),
                       ("chunk_precision", "Chunk 精确率"), ("cross_document", "跨文档污染")):
        _out(f"  {label}：{metrics[key]['value']:.2%}")
    if modes["auto"] or modes["unmapped"]:
        note = "（已写入 eval/mappings.json）" if args.confirm_auto else "，请在报告中核对"
        _out(f"证据映射：{modes['confirmed']} 组已确认，{modes['auto']} 组自动判定，"
             f"{modes['unmapped']} 组无法匹配{note}")
    write_index_page(workspace)
    _out(f"报告：{(folder / 'index.html').relative_to(workspace.root).as_posix()}")
    return 0


def _config(workspace: Workspace, args: argparse.Namespace) -> int:
    if args.action == "list":
        for config in list_configs(workspace.root):
            _out(f"{config.name:<14} {config.description}")
        return 0
    from rag.registry import assemble
    config = resolve_config(workspace.root, args.name)
    assembly = assemble(config)
    _out(config.path.read_text(encoding="utf-8").rstrip())
    _out(f"\n# 校验通过 · 构建指纹 {assembly.fingerprint()[:12]}")
    return 0


def _report(workspace: Workspace, args: argparse.Namespace) -> int:
    from rag.reports.evaluation import rerender_all
    from rag.reports.index_page import write_index_page
    count = rerender_all(workspace)
    write_index_page(workspace)
    _out(f"已重新生成 {count} 份评估报告和 reports/index.html")
    return 0


HANDLERS = {"status": _status, "ingest": _ingest, "chunks": _chunks, "search": _search,
            "ask": _ask, "chat": _chat, "eval": _eval, "config": _config, "report": _report}


def main(argv: Sequence[str] | None = None, *, workspace_root: Path | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    root = (workspace_root or Path.cwd()).resolve()
    try:
        from dotenv import load_dotenv
        load_dotenv(root / ".env")
    except ImportError:
        pass
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    args = build_parser().parse_args(argv)
    workspace = Workspace(root)
    from rag.eval.runner import EvalError
    from rag.index.builder import IngestError
    from rag.ingest.sources import SourceError
    from rag.query.llm import LLMError
    try:
        return HANDLERS[args.command](workspace, args)
    except (ConfigError, SourceError, IngestError, EvalError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except LLMError as exc:
        print(f"LLM 调用失败：{exc}", file=sys.stderr)
        return 4
