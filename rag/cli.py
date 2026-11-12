"""Command line: python -m rag <command> [--config NAME] ..."""

from __future__ import annotations

import argparse
import json
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


def _budgets(value: str) -> list[int]:
    try:
        return sorted({_positive(part) for part in value.split(",")})
    except ValueError:
        raise argparse.ArgumentTypeError("写成逗号分隔的正整数，如 3,6,9,12")


def _retriever_option(sub: argparse.ArgumentParser) -> None:
    from rag.config import TABLE_RETURN_UNITS
    from rag.registry import RETRIEVERS
    sub.add_argument("--retriever", choices=RETRIEVERS,
                     help="本次改用这个检索器（默认取配置文件 [retriever] use；bm25、hybrid 需要配置 [lexical]）")
    sub.add_argument("--table-return-unit", choices=TABLE_RETURN_UNITS,
                     help="命中表格 chunk 时返回该 chunk 还是整张表（默认取配置文件 [retriever] table_return_unit）")
    sub.add_argument("--bm25-weight", type=_weight,
                     help="混合检索中 BM25 排名的权重 α（默认取配置文件 [retriever] bm25_weight，未写为 1）")


def _generations(value: str) -> list[int]:
    try:
        numbers = sorted({int(part) for part in value.split(",")})
    except ValueError:
        raise argparse.ArgumentTypeError("写成逗号分隔的非负整数，如 0,1,2,3")
    if any(n < 0 for n in numbers):
        raise argparse.ArgumentTypeError("写成逗号分隔的非负整数，如 0,1,2,3")
    return numbers


def _query_mode_option(sub: argparse.ArgumentParser) -> None:
    from rag.config import QUERY_MODES
    sub.add_argument("--query-mode", choices=QUERY_MODES,
                     help="本次改用这一路查询：retrieval 原问题检索一次；multi_round 多轮检索（LLM 规划，设置见 [multi_round]）。"
                          "默认取配置文件 [query] mode，未写为 retrieval")


def _weight(value: str) -> float:
    number = float(value)
    if number < 0:
        raise argparse.ArgumentTypeError("必须为非负数")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m rag", description="本地 PDF 知识库 RAG")
    commands = parser.add_subparsers(dest="command", required=True)

    def command(name: str, help_text: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help_text, description=help_text)
        if name not in ("config", "compare", "query-compare"):
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
        _retriever_option(sub)
        if name == "ask":
            sub.add_argument("--debug", action="store_true", help="显示检索命中和发给 LLM 的完整消息（多轮检索时显示问题列表）")
            _query_mode_option(sub)
    chat = command("chat", "多次提问（每轮独立检索，不保留对话记忆）")
    chat.add_argument("--top-k", type=_positive)
    _retriever_option(chat)
    _query_mode_option(chat)
    evaluate = command("eval", "用 eval/ground-truth 的标注问题评估检索效果，生成报告")
    budget = evaluate.add_mutually_exclusive_group()
    budget.add_argument("--top-k", type=_positive, help="默认取配置文件 [eval] top_k")
    budget.add_argument("--budgets", type=_budgets, metavar="B1,B2,...",
                        help="几档预算（返回单元数），每题检索一次，每档各出一份评估，如 3,6,9,12")
    _retriever_option(evaluate)
    _query_mode_option(evaluate)
    evaluate.add_argument("--name", help="多轮检索评估页的名称（reports/multi_round/NAME/），默认为配置名；"
                                         "每题按 [multi_round] 的上限运行，更低的上限从同一次运行截取")
    evaluate.add_argument("--cases", metavar="ID,ID,...", help="多轮检索评估只跑这些题")
    evaluate.add_argument("--generations", type=_generations, default=None, metavar="0,1,2,3",
                          help="多轮检索评估的独立生成编号（默认 0,1,2,3）")
    anchors = command("anchors", "在 PDF 上定位每段 excerpt，生成审核文件 tmp/anchor-review/")
    anchors.add_argument("--since", metavar="GIT_REF", help="审核页并排显示相对这个 git 版本改动过的 excerpt")
    anchors.add_argument("--write", action="store_true", help="把定位结果写入 eval/ground-truth（审核确认后再用）")
    command("table-structure", "把 eval/table-structure 的表格结构答案画在已入库的表格上，生成核对页")
    config = command("config", "查看装配配置")
    config.add_argument("action", choices=["list", "show"])
    config.add_argument("name", nargs="?")
    compare = command("compare", "把几次评估并排对比，生成 reports/compare/NAME/；每组第一次评估是基线")
    compare.add_argument("runs", nargs="+", metavar="RUN",
                         help="reports/eval/ 下的评估目录名；可写成 目录名=显示名（如 =V3.9），显示在该列设置之前")
    compare.add_argument("--name", required=True, help="对比页名称（目录名）")
    compare.add_argument("--no-table-evidence", action="store_true",
                         help="不显示表格证据标签页（不以表格处理为对象的对比，如查询侧的改动）")
    compare.add_argument("--table-spec", metavar="FILE",
                         help="表格证据对照的说明文件（JSON：pairs 对照、excluded 排除的证据组、notes 结论），存入 runs.json")
    query = command("query-compare", "13 种组合 × 查询侧模块配置逐题评估，生成查询侧对比页 reports/query/NAME/")
    query.add_argument("--name", required=True, help="对比页名称（目录名）")
    command("report", "按已入库的中间结果与已保存的评估结果，重新生成入库审核页、评估报告、对比页和总入口 reports/index.html")
    return parser


def _out(text: str = "") -> None:
    print(text, flush=True)


def _assembly(workspace: Workspace, name: str | None, retriever: str | None = None,
              table_return_unit: str | None = None, bm25_weight: float | None = None,
              query_mode: str | None = None):
    """`retriever`, `table_return_unit`, `bm25_weight` and `query_mode` replace the config's query settings for this command."""
    from dataclasses import replace
    from rag.registry import assemble
    config = resolve_config(workspace.root, name)
    if retriever is not None:
        config = replace(config, retriever=replace(config.retriever, use=retriever))
    if table_return_unit is not None:
        config = replace(config, table_return_unit=table_return_unit)
    if bm25_weight is not None:
        config = replace(config, bm25_weight=bm25_weight)
    if query_mode is not None:
        config = replace(config, query_mode=query_mode)
    return assemble(config)


def _query_assembly(workspace: Workspace, args: argparse.Namespace):
    return _assembly(workspace, args.config, args.retriever, args.table_return_unit, args.bm25_weight,
                     getattr(args, "query_mode", None))


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
    from rag.reports.index_page import index_summary
    _out(f"配置 {assembly.name}：{headline}")
    _out(f"  {index_summary(assembly)}")
    for item in status.documents:
        chunks = f"{item.entry.chunk_count} chunks" if item.entry and item.state != "new" else ""
        indexes = ""
        if item.state == "incomplete":
            missing = [name for name, ok in (("向量", item.vectors_complete), ("倒排", item.lexical_complete),
                                             ("整表", item.tables_complete)) if ok is False]
            indexes = f"  （{'、'.join(missing)}索引不完整）"
        _out(f"  [{STATE_LABELS[item.state]}] {item.relative_path}  {chunks}{indexes}")
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
        built = {item.relative_path for item in outcomes if item.action in ("added", "updated", "rebuilt")}
        if built:
            _out(f"  各步骤耗时（{len(built)} 份文档合计）：{_step_times(workspace, assembly.name, built)}")
    write_index_page(workspace)
    _out("审核报告：reports/index.html")
    return 1 if failed else 0


def _step_times(workspace: Workspace, config_name: str, built: set[str]) -> str:
    from rag.index.manifest import Manifest
    manifest = Manifest.load(workspace.index_dir(config_name) / "manifest.json")
    totals: dict[str, float] = {}
    for entry in (manifest.documents.values() if manifest else ()):
        if entry.relative_path in built:
            for name, seconds in entry.timings.items():
                totals[name] = totals.get(name, 0.0) + seconds
    return "，".join(f"{name} {seconds:.1f}s" for name, seconds in totals.items())


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
        return self.scope(question).document_ids

    def scope(self, question: str):
        from rag.query.scope import describe, resolve_scope
        scope = resolve_scope(question, self.catalog, self.enabled, self.user_document)
        _out(describe(scope, self.names))
        return scope


def _searcher(workspace: Workspace, assembly):
    from rag.index.builder import open_lexical, open_store, open_tables
    from rag.query.retriever import build_retriever
    from rag.query.units import UnitSearch
    store = open_store(workspace, assembly.name)
    return UnitSearch(build_retriever(assembly, store, open_lexical(workspace, assembly)), store,
                      open_tables(workspace, assembly.name), assembly.config.table_return_unit)


def _hit_scores(hit, retriever: str) -> str:
    parts = []
    if hit.semantic_rank is not None:
        parts.append(f"向量第 {hit.semantic_rank} 名（相似度 {hit.similarity:.4f}）")
    if hit.bm25 is not None:
        parts.append(f"BM25 第 {hit.lexical_rank} 名（{hit.bm25:.3f}）")
    if retriever == "hybrid":
        if hit.bm25 is None:
            parts.append("BM25 无匹配词")
        parts.append(f"融合得分 {hit.score:.5f}")
    return " · ".join(parts)


def _show_units(units, retriever: str) -> None:
    if not units:
        _out("没有检索到内容。")
    for rank, unit in enumerate(units, start=1):
        chunk = unit.hit.chunk
        source = (f"整表 {unit.table.table_id}（命中 {chunk.chunk_id}，共 {len(unit.chunk_ids)} 块）"
                  if unit.table else chunk.chunk_id)
        _out(f"#{rank} {_hit_scores(unit.hit, retriever)} · {chunk.document_name} · "
             f"第 {','.join(map(str, unit.pages))} 页 · {source}")
        _out(unit.text)
        _out()


def _search(workspace: Workspace, args: argparse.Namespace) -> int:
    assembly = _query_assembly(workspace, args)
    _require_queryable(workspace, assembly)
    documents = _Scoper(workspace, assembly, args.document)(args.question)
    units = _searcher(workspace, assembly).search(
        args.question, args.top_k or assembly.config.top_k, documents)
    _show_units(units, assembly.config.retriever.use)
    return 0


def _answer(workspace: Workspace, assembly, searcher, question: str, top_k: int,
            scoper: _Scoper, debug: bool) -> None:
    from rag.query.llm import LLMError
    from rag.query.prompt import build_messages
    units = searcher.search(question, top_k, scoper(question))
    messages = build_messages(question, units)
    if debug:
        _show_units(units, assembly.config.retriever.use)
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


class _MultiRound:
    """The multi-round query path of an assembly (settings: [multi_round])."""

    def __init__(self, workspace: Workspace, assembly, document: str | None = None):
        from rag.ingest.tables.llm_tables import LLMCache
        from rag.query.llm import OpenRouterLLM
        from rag.query.multi_round.state import SCHEDULE_A, Schedule
        from rag.query.planning import document_languages
        self.settings = assembly.config.multi_round
        self.scoper = _Scoper(workspace, assembly, document)
        self.languages_of = document_languages(workspace.index_dir(assembly.name))
        self.searcher = _searcher(workspace, assembly)
        self.llm = OpenRouterLLM(model=self.settings.model, temperature=0, env_override=False)
        self.cache = LLMCache(workspace.root / ".rag" / "llm-cache")
        self.schedule = Schedule(self.settings.fallback_schedule) if self.settings.fallback_schedule else SCHEDULE_A

    def ask(self, question: str, debug: bool = False) -> None:
        from rag.query.multi_round.planner import Caller
        from rag.query.multi_round.runner import run
        scope = self.scoper.scope(question)
        languages = sorted({self.languages_of[d] for d in (scope.document_ids or self.languages_of)})
        names = self.scoper.names
        caller = Caller(self.llm, self.cache, scope={"documents": [names[d] for d in (scope.document_ids or names)],
                                                     "identifier_codes": list(scope.codes)})
        record = run(question, self.searcher, scope.document_ids, scope.codes, languages, caller,
                     self.settings.max_llm_calls, self.settings.units_per_attempt, self.schedule)
        if debug:
            for q in record.questions:
                _out(f"{q.id}（{q.parent_id or '原问题'}）[{q.status.value}{'：' + q.abandon_reason if q.abandon_reason else ''}] {q.text}"
                     f"\n    证据：{'、'.join(q.evidence) or '无'}；{q.note}")
        seconds = sum(c.seconds for c in record.calls)
        _out(f"[多轮检索：{len(record.calls)} 次 LLM 调用，{seconds:.1f} 秒，终止：{record.termination}，"
             f"交给 LLM 的单元 {len(record.table.order)} 个]")
        _out(record.answer)


def _ask(workspace: Workspace, args: argparse.Namespace) -> int:
    assembly = _query_assembly(workspace, args)
    _require_queryable(workspace, assembly)
    if assembly.config.query_mode == "multi_round":
        _MultiRound(workspace, assembly, args.document).ask(args.question, args.debug)
        return 0
    _answer(workspace, assembly, _searcher(workspace, assembly), args.question,
            args.top_k or assembly.config.top_k, _Scoper(workspace, assembly, args.document), args.debug)
    return 0


def _chat(workspace: Workspace, args: argparse.Namespace) -> int:
    from rag.query.llm import LLMError
    assembly = _query_assembly(workspace, args)
    _require_queryable(workspace, assembly)
    multi = assembly.config.query_mode == "multi_round"
    if multi:
        path = _MultiRound(workspace, assembly)
        _out(f"配置 {assembly.name}，多轮检索，模型 {path.settings.model}，LLM 调用上限 {path.settings.max_llm_calls}。"
             "输入问题，空行或 exit 退出。")
    else:
        searcher = _searcher(workspace, assembly)
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
            if multi:
                path.ask(question)
            else:
                _answer(workspace, assembly, searcher, question,
                        args.top_k or assembly.config.top_k, scoper, False)
        except LLMError as exc:
            _out(f"回答失败：{exc}")
    return 0


def _eval(workspace: Workspace, args: argparse.Namespace) -> int:
    from rag.eval.runner import run_evaluation
    from rag.reports.index_page import write_index_page
    assembly = _query_assembly(workspace, args)
    if assembly.config.query_mode == "multi_round":
        return _eval_multi_round(workspace, assembly, args)
    if args.name or args.cases or args.generations is not None:
        raise ConfigError("--name、--cases、--generations 只用于多轮检索评估"
                          "（[query] mode = \"multi_round\" 或 --query-mode multi_round）")
    budgets = args.budgets or [args.top_k or assembly.config.eval_top_k]
    folders = run_evaluation(workspace, assembly, budgets)
    for folder in folders:
        _eval_summary(workspace, assembly, folder)
    write_index_page(workspace)
    for folder in folders:
        _out(f"报告：{(folder / 'index.html').relative_to(workspace.root).as_posix()}")
    return 0


def _eval_multi_round(workspace: Workspace, assembly, args: argparse.Namespace) -> int:
    from rag.eval.multi_round import GENERATIONS, collect
    from rag.reports.multi_round import write_pages
    _require_queryable(workspace, assembly)
    cases = [c.strip() for c in args.cases.split(",") if c.strip()] if args.cases else None
    generations = args.generations if args.generations is not None else list(GENERATIONS)

    def progress(case_id, generation, facts):
        _out(f"  {case_id} 第 {generation} 次生成：{facts['termination']}，{len(facts['calls'])} 次调用")

    data = collect(workspace, args.name or assembly.name, assembly, cases, generations, progress=progress)
    folder = write_pages(workspace, data)
    _out(f"多轮检索评估：{(folder / 'index.html').relative_to(workspace.root).as_posix()}")
    return 0


def _eval_summary(workspace: Workspace, assembly, folder) -> None:
    from rag.jsonio import read_json
    result = read_json(folder / "result.json")
    metrics = result["metrics"]
    modes = result["evidence_modes"]
    returned = "整表" if assembly.config.table_return_unit == "table" else "命中的 chunk"
    _out(f"配置 {assembly.name} · K={result['top_k']} · 命中表格时返回{returned} · {result['dataset']['answerable']} 道可回答问题")
    for key, label in (("hit_rate", "命中率"), ("complete_coverage", "完整覆盖率"),
                       ("group_recall", "证据组召回"), ("mrr", "MRR"),
                       ("chunk_precision", "Chunk 精确率"), ("cross_document", "跨文档污染")):
        _out(f"  {label}：{metrics[key]['value']:.2%}")
    tokens = result["returned_tokens"]
    _out(f"  每题返回 token：{tokens['value']:.0f}（最多 {tokens['max']}）")
    cost = result["query_cost"]
    _out(f"  每题检索：{cost['retrievals']['value']:.1f} 次，{cost['retrieval_seconds']['value'] * 1000:.0f} ms")
    scope = result["document_scope"]
    if scope["enabled"] and scope["catalog"]:
        ratio = scope["identified_correctly"]
        _out(f"  文档识别准确率：{ratio['numerator']}/{ratio['denominator']}")
    else:
        _out("  文档过滤：" + ("已关闭" if not scope["enabled"] else "未配置文档标识表，全库检索"))
    if modes["fallback"]:
        _out(f"证据映射：{modes['coordinate']} 段 excerpt 按坐标判定，{modes['fallback']} 段文字兜底，请在报告中抽查")


def _table_structure(workspace: Workspace, args: argparse.Namespace) -> int:
    from rag.reports.table_structure import write_answers_page
    config = resolve_config(workspace.root, args.config)
    _out(f"核对页：{write_answers_page(workspace, config.name)}")
    return 0


def _anchors(workspace: Workspace, args: argparse.Namespace) -> int:
    import json
    import subprocess
    from rag.eval.anchors import locate_all, write_anchors
    from rag.eval.dataset import load_dataset
    from rag.eval.runner import EvalError
    from rag.ingest.sources import discover
    from rag.registry import ocr_step
    from rag.reports.anchor_review import write_anchor_review
    dataset = load_dataset(workspace.ground_truth)
    sources = {s.document_id: s for s in discover(workspace.documents)}
    for doc, labelled in dataset.document_hashes.items():
        if doc not in sources:
            raise EvalError(f"标注涉及的 PDF 不在 documents/ 中：{doc}")
        if sources[doc].file_hash != labelled:
            raise EvalError(f"PDF 在标注之后被修改过，需重新标注：{sources[doc].relative_path}")
    # Documents with scanned pages are read from their OCR text-layer PDF, as every other tool does.
    step = ocr_step(resolve_config(workspace.root, args.config))
    scanned: dict[str, frozenset[int]] = {}
    for doc in dataset.document_hashes:
        if step is not None:
            sources[doc], pages = step.prepare(sources[doc], _out)
            scanned[doc] = frozenset(p.page_number for p in pages.pages if p.kind == "scanned")
    pdf_paths = {d: s.pdf_path for d, s in sources.items()}
    pairs = [(c.case_id, x) for c in dataset.cases for x in c.excerpts]
    located = locate_all([x for _, x in pairs], pdf_paths, scanned)
    items = [(case_id, item) for (case_id, _), item in zip(pairs, located)]
    previous: dict[tuple[str, str], str | None] = {}
    if args.since:
        for path in sorted(workspace.ground_truth.glob("*--*.json")):
            relative = path.relative_to(workspace.root).as_posix()
            shown = subprocess.run(["git", "show", f"{args.since}:{relative}"], cwd=workspace.root,
                                   capture_output=True)
            old = json.loads(shown.stdout.decode("utf-8")) if shown.returncode == 0 else {"cases": []}
            texts = {(c["case_id"], x["excerpt_id"]): x["text"] for c in old["cases"] for x in c["excerpts"]}
            for case_id, item in items:
                key = (case_id, item.excerpt.excerpt_id)
                if item.excerpt.document_id in path.stem and texts.get(key) != item.excerpt.text:
                    previous[key] = texts.get(key)
    folder = workspace.root / "tmp" / "anchor-review"
    page_path = write_anchor_review(folder, items, pdf_paths,
                                    {c.case_id: c.question for c in dataset.cases}, previous)
    counts = {s: sum(1 for _, i in items if i.status == s) for s in ("located", "ambiguous", "missing")}
    approximate = sum(1 for _, i in items if i.approximate)
    _out(f"{len(items)} 段 excerpt：唯一定位 {counts['located']}，多处出现按位置选定 {counts['ambiguous']}，"
         f"有片段找不到 {counts['missing']}；其中 {approximate} 段含扫描页上的近似匹配")
    _out(f"审核页：{page_path.relative_to(workspace.root).as_posix()}")
    if args.write:
        if counts["missing"]:
            raise EvalError("有 excerpt 存在找不到的片段，不能写入；请先修正这些 excerpt")
        written = write_anchors(workspace.ground_truth, {(c, i.excerpt.excerpt_id): i for c, i in items})
        _out(f"已把 {written} 段 excerpt 的定位写入 eval/ground-truth")
    return 0


def _config(workspace: Workspace, args: argparse.Namespace) -> int:
    if args.action == "list":
        for config in list_configs(workspace.root):
            _out(f"{config.name:<28} {config.description}")
        extra = list_configs(workspace.root, extra=True)[len(list_configs(workspace.root)):]
        if extra:
            _out("\nconfigs/extra/（不在 --all-configs 中，可用 --config 名称装配）：")
            for config in extra:
                _out(f"{config.name:<28} {config.description}")
        return 0
    from rag.registry import assemble
    config = resolve_config(workspace.root, args.name)
    assembly = assemble(config)
    _out(config.path.read_text(encoding="utf-8").rstrip())
    _out(f"\n# 校验通过 · 构建指纹 {assembly.fingerprint()[:12]}")
    return 0


def _compare(workspace: Workspace, args: argparse.Namespace) -> int:
    from rag.reports.compare import write_comparison
    from rag.reports.index_page import write_index_page
    spec = json.loads(Path(args.table_spec).read_text(encoding="utf-8")) if args.table_spec else None
    folder = write_comparison(workspace, args.name, args.runs, spec, not args.no_table_evidence)
    write_index_page(workspace)
    _out(f"对比页：{(folder / 'index.html').relative_to(workspace.root).as_posix()}")
    return 0


def _report(workspace: Workspace, args: argparse.Namespace) -> int:
    from rag.reports.compare import rerender_comparisons
    from rag.reports.evaluation import rerender_all
    from rag.reports.index_page import write_index_page
    from rag.reports.ingest import rerender_ingest_reports
    documents = rerender_ingest_reports(workspace)
    count = rerender_all(workspace)
    comparisons = rerender_comparisons(workspace)
    from rag.reports.query_compare import rerender_query_pages
    queries = rerender_query_pages(workspace)
    from rag.reports.multi_round import rerender as rerender_multi_round
    multi = rerender_multi_round(workspace)
    write_index_page(workspace)
    _out(f"已重新生成 {documents} 份文档的入库审核页、{count} 份评估报告、{comparisons} 份对比页、"
         f"{queries} 份查询侧对比页、{multi} 份多轮检索评估页和 reports/index.html")
    return 0


def _query_compare(workspace: Workspace, args: argparse.Namespace) -> int:
    from rag.eval.query_compare import (COMBOS, PERTURBATION_GENERATIONS, PERTURBATION_MAPPING, collect,
                                        collect_perturbation)
    from rag.reports.index_page import write_index_page
    from rag.reports.query_compare import write_query_page
    _out(f"{len(COMBOS)} 种组合：{'、'.join(c.label for c in COMBOS)}")
    data = collect(workspace, args.name)
    folder = write_query_page(workspace, data)
    if all((workspace.root / PERTURBATION_MAPPING.format(generation=g)).is_file() for g in PERTURBATION_GENERATIONS):
        _out(f"扰动测试：第 {'、'.join(map(str, PERTURBATION_GENERATIONS))} 次生成")
        del data["process"]   # written above; the second write keeps the process files
        data["perturbation"] = collect_perturbation(workspace)
        folder = write_query_page(workspace, data)
    write_index_page(workspace)
    _out(f"查询侧对比页：{(folder / 'index.html').relative_to(workspace.root).as_posix()}")
    return 0


HANDLERS = {"status": _status, "ingest": _ingest, "chunks": _chunks, "search": _search,
            "ask": _ask, "chat": _chat, "eval": _eval, "anchors": _anchors, "table-structure": _table_structure, "config": _config,
            "compare": _compare, "query-compare": _query_compare, "report": _report}


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
    from rag.reports.compare import CompareError
    try:
        return HANDLERS[args.command](workspace, args)
    except (ConfigError, SourceError, IngestError, EvalError, CompareError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except LLMError as exc:
        print(f"LLM 调用失败：{exc}", file=sys.stderr)
        return 4
