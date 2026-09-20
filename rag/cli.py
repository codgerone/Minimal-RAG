"""Command-line interface for Minimal RAG."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import replace
from time import perf_counter

from rag.config import SelectedPipelineSettings, Settings, load_settings, select_pipeline
from rag.bootstrap import create_runtime
from rag.document_registry import discover_documents, resolve_document_selector
from rag.embeddings import E5Embedder
from rag.errors import ConfigurationError, IndexNotReadyError, LlmServiceError, PdfParseError, RagError
from rag.evaluator import Evaluator, load_evaluation_cases, run_formal_evaluation
from rag.indexer import Indexer
from rag.pipeline_indexer import RuntimeIndexer
from rag.llm import OpenRouterClient
from rag.manifest import load_manifest, validate_index
from rag.pipeline_manifest import load_pipeline_manifest
from rag.models import DocumentState, DocumentStatus, EvaluationResult, IngestSummary
from rag.pdf_parser import parse_pdf
from rag.pipeline import AnswerResult, RAGPipeline
from rag.retriever import Retriever
from rag.vector_store import ChromaVectorStore
from rag.cli_readiness import (
    ConsoleTerminal,
    IndexInspection,
    ReadinessResult,
    display_status,
    ensure_index_ready,
    ensure_llm_credentials,
    inspect_index_for_cli,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="uv run python -m rag",
        usage=(
            "uv run python -m rag [-h] "
            "{documents,ingest,browse,chunks,search,ask,chat,eval} ..."
        ),
        description="针对本地 PDF 知识库的 Minimal RAG。",
        epilog="""命令总览:
  documents
    查看 documents/ 目录中的 PDF，以及它们的索引状态。

  ingest [--all | --file PDF] [--force] [--prune]
    建立或增量更新向量索引。
    --all       处理 documents/ 下全部 PDF（包括子目录）。
    --file PDF  仅处理 documents/ 内指定的 PDF（支持相对路径或唯一文件名）。
    --force     强制重建索引；即使文件未变化也重新解析、分块和生成 Embedding。
    --prune     清理 documents/ 中已删除文件遗留的向量索引。
    兼容行为：不传 --all/--file 时仍处理全部 PDF。
    注意：--all 与 --file 互斥；--file 与 --prune 不能同时使用。

  chunks --document PDF [--page PAGE]
    查看某个已索引 PDF 的文本块。
    --document PDF  指定文档（必填）。
    --page PAGE     仅查看指定页（正整数）。

  browse [--document PDF] [--offset N] [--limit N] [--full-metadata]
    以纵向列表查看 Chroma 中实际保存的 ID、chunk 文本和 metadata。
    默认隐藏体积很大的来源明细字段；--full-metadata 显示完整 metadata。
    始终不读取或显示 embedding 向量。

  search QUESTION [--top-k N] [--document PDF]
    只执行向量检索，不调用 LLM。
    --top-k N       返回前 N 个最相关文本块（正整数）。
    --document PDF  将检索范围限制到指定文档。

  ask QUESTION [--debug]
    检索知识库并调用 LLM 生成一次回答。
    --debug  额外显示检索结果及发送给 LLM 的最终消息。

  chat
    启动连续问答循环：启动后在 > 提示符中输入问题并按回车。
    当前版本不保留跨轮对话历史，每个问题都会独立检索和回答。
    /help       显示 chat 内可用的交互命令。
    /exit       退出问答循环。
    /quit       退出问答循环（与 /exit 相同）。
    /debug on   开启调试输出：显示检索命中详情与发送给 LLM 的消息。
    /debug off  关闭调试输出，恢复简洁回答模式。

  eval [--top-k N] [--live]
    使用已批准的 ground truth 与当前链路测试集运行正式检索评估并生成 JSON、HTML 和汇总报告。
    --top-k N  指定返回片段数；省略时使用 TOP_K。
    --live  额外调用 LLM，执行端到端冒烟评估。

Pipeline:
  上述八个命令均支持 --pipeline {v1,v2}，默认使用 v2。
  两条链路的 collection、manifest、健康检查和恢复事务彼此隔离。

常用示例:
  uv run python -m rag ingest --pipeline v1 --all
  uv run python -m rag ingest --pipeline v2 --all
  uv run python -m rag documents --pipeline v1
  uv run python -m rag documents --pipeline v2
  uv run python -m rag browse --pipeline v2 --limit 10
  uv run python -m rag chunks --pipeline v2 --document "order.pdf"
  uv run python -m rag search --pipeline v2 "付款条件是什么？"

提示：可在任意命令后追加 -h 或 --help，查看该命令的完整参数说明。
例如：uv run python -m rag ingest --help""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )
    parser.add_argument(
        "-h", "--help", action="help", help="显示当前命令的帮助信息后退出。"
    )
    subparsers = parser.add_subparsers(
        dest="command", required=True, help=argparse.SUPPRESS, prog="uv run python -m rag"
    )
    def add_pipeline_argument(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument(
            "--pipeline", choices=("v1", "v2"), default="v2",
            help="选择处理链路（默认：v2）",
        )

    documents = subparsers.add_parser("documents", help=argparse.SUPPRESS)
    add_pipeline_argument(documents)

    ingest = subparsers.add_parser("ingest", help=argparse.SUPPRESS)
    add_pipeline_argument(ingest)
    ingest_scope = ingest.add_mutually_exclusive_group()
    ingest_scope.add_argument(
        "--all", action="store_true",
        help="处理 documents/ 下全部 PDF（默认行为，显式写出便于脚本阅读）",
    )
    ingest_scope.add_argument("--file", help="仅处理 documents/ 内的指定 PDF")
    ingest.add_argument("--force", action="store_true", help="强制重建")
    ingest.add_argument(
        "--prune", action="store_true", help="删除已移除文档的向量索引"
    )

    chunks = subparsers.add_parser("chunks", help=argparse.SUPPRESS)
    add_pipeline_argument(chunks)
    chunks.add_argument("--document", required=True, help="相对路径或唯一文件名")
    chunks.add_argument("--page", type=int, help="只显示指定页码")

    browse = subparsers.add_parser("browse", help=argparse.SUPPRESS)
    add_pipeline_argument(browse)
    browse.add_argument("--document", help="仅显示指定文档的 records")
    browse.add_argument("--offset", type=int, default=0, help="跳过前 N 条（默认：0）")
    browse.add_argument("--limit", type=int, default=20, help="最多显示 N 条（默认：20）")
    browse.add_argument(
        "--full-metadata", action="store_true",
        help="显示完整 metadata，包括 sources_json 和 node_ids_json",
    )

    search = subparsers.add_parser("search", help=argparse.SUPPRESS)
    add_pipeline_argument(search)
    search.add_argument("question", help="检索问题")
    search.add_argument("--top-k", type=int, dest="top_k")
    search.add_argument("--document", help="限制到指定文档")

    ask = subparsers.add_parser("ask", help=argparse.SUPPRESS)
    add_pipeline_argument(ask)
    ask.add_argument("question", help="问题")
    ask.add_argument("--debug", action="store_true")
    chat = subparsers.add_parser("chat", help=argparse.SUPPRESS)
    add_pipeline_argument(chat)

    evaluate = subparsers.add_parser("eval", help=argparse.SUPPRESS)
    add_pipeline_argument(evaluate)
    evaluate.add_argument("--top-k", type=int, dest="top_k", help="正式检索评估的返回片段数")
    evaluate.add_argument("--live", action="store_true", help="调用 LLM 做冒烟测试")
    return parser


def _validate_cli_args(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    if args.command == "ingest" and args.file and args.prune:
        parser.error("--file 不能与 --prune 同时使用")
    if args.command == "chunks" and args.page is not None and args.page <= 0:
        parser.error("--page 必须是正整数")
    if args.command == "search" and args.top_k is not None and args.top_k <= 0:
        parser.error("--top-k 必须是正整数")
    if args.command == "eval" and args.top_k is not None and args.top_k <= 0:
        parser.error("--top-k 必须是正整数")
    if args.command == "browse" and args.offset < 0:
        parser.error("--offset 不能小于 0")
    if args.command == "browse" and args.limit <= 0:
        parser.error("--limit 必须是正整数")


def _store(settings: Settings) -> ChromaVectorStore:
    return ChromaVectorStore(settings.db_path, settings.collection_name)


def _retriever(
    settings: Settings,
    embedder: E5Embedder | None = None,
    store: ChromaVectorStore | None = None,
) -> Retriever:
    return Retriever(
        settings,
        embedder or E5Embedder(
            settings.embedding_model, settings.embedding_model_revision
        ),
        store or _store(settings),
    )


def _pipeline(
    settings: Settings,
    embedder: E5Embedder | None = None,
    store: ChromaVectorStore | None = None,
) -> RAGPipeline:
    return RAGPipeline(
        _retriever(settings, embedder, store),
        OpenRouterClient(settings),
    )


def _command_documents(settings: Settings, inspection: IndexInspection | None = None) -> int:
    inspection = inspection or inspect_index_for_cli(settings, _store(settings))
    health = inspection.health
    if health.usable:
        print("知识库索引状态：可用")
    else:
        print(f"知识库索引状态：不可用，原因：发现 {len(health.issues)} 项需要处理的问题。")
    print("文档名称 | 索引状态 | 索引状态说明 | 页数 | Chunks | 索引时间")
    for status in health.document_statuses:
        label, explanation = display_status(status)
        print(
            f"{status.relative_path.replace('|', '／')} | {label} | {explanation.replace('|', '／')} | "
            f"{status.page_count if status.page_count is not None else '-'} | "
            f"{status.chunk_count if status.chunk_count is not None else '-'} | "
            f"{status.indexed_at or '-'}"
        )
    return 0


def _print_ingest_summary(summary: IngestSummary, settings: Settings) -> None:
    for result in summary.results:
        suffix = f"：{result.detail}" if result.detail else ""
        print(f"{result.state} | {result.relative_path}{suffix}")
    print(f"扫描PDF数：{summary.scanned}")
    print(f"新增：{summary.added}")
    print(f"更新：{summary.updated}")
    print(f"跳过：{summary.skipped}")
    print(f"失败：{summary.failed}")
    print(f"缺失：{summary.missing}")
    print(f"清理：{summary.pruned}")
    print(f"Collection记录总数：{summary.collection_count}")
    print(f"Embedding模型：{settings.embedding_model}")
    print(f"索引目录：{settings.db_path}")


def _command_ingest(settings: Settings, args: argparse.Namespace) -> int:
    started_at = perf_counter()
    if isinstance(settings, SelectedPipelineSettings):
        indexer = RuntimeIndexer(
            settings, create_runtime(settings),
            E5Embedder(settings.embedding_model, settings.embedding_model_revision), _store(settings),
        )
    else:
        indexer = Indexer(
            settings, E5Embedder(settings.embedding_model, settings.embedding_model_revision), _store(settings),
        )
    if args.file:
        summary = indexer.ingest_one(args.file, force=args.force)
    else:
        summary = indexer.ingest_all(force=args.force, prune=args.prune)
    _print_ingest_summary(summary, settings)
    print(f"耗时：{perf_counter() - started_at:.2f} 秒")
    return 3 if summary.failed else 0


def _command_chunks(settings: Settings, args: argparse.Namespace) -> int:
    documents = discover_documents(settings.documents_dir)
    source = resolve_document_selector(args.document, documents)
    chunks = _store(settings).list_chunks(source.document_id, args.page)
    if not chunks:
        raise RagError(
            f"没有找到文档 {source.relative_path} 的已索引 chunk。",
            "请先执行 python -m rag ingest。",
        )
    for chunk in chunks:
        print(f"Document：{getattr(chunk, 'document_name', source.document_name)}")
        print(f"Chunk ID：{chunk.chunk_id}")
        pages = getattr(chunk, "page_numbers", (getattr(chunk, "page_number", None),))
        print(f"页码：{', '.join(str(page) for page in pages if page is not None) or '不可用'}")
        print(f"Chunk Index：{chunk.chunk_index}")
        print(f"字符数：{len(chunk.text)}")
        print(f"文本：\n{chunk.text}\n")
    return 0


def _command_browse(settings: Settings, args: argparse.Namespace) -> int:
    document_id = None
    if args.document:
        source = resolve_document_selector(
            args.document, discover_documents(settings.documents_dir)
        )
        document_id = source.document_id
    records = _store(settings).list_records(document_id)
    selected = records[args.offset : args.offset + args.limit]
    print(f"Collection：{settings.collection_name}")
    print(f"记录总数：{len(records)}；显示范围：{args.offset + 1 if selected else 0}-"
          f"{args.offset + len(selected)}")
    for position, record in enumerate(selected, start=args.offset + 1):
        print(f"\n[{position}/{len(records)}]")
        print(f"ID：{record.record_id}")
        print("Chunk：")
        print(record.document)
        metadata = dict(record.metadata)
        hidden = []
        if not args.full_metadata:
            for key in ("sources_json", "node_ids_json"):
                if key in metadata:
                    hidden.append(key)
                    metadata.pop(key)
        print("Metadata：")
        print(json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True))
        if hidden:
            print(f"已隐藏：{', '.join(hidden)}（使用 --full-metadata 查看）")
    if not selected:
        print("没有符合当前范围的记录。")
    return 0


def _print_hits(hits) -> None:
    for rank, hit in enumerate(hits, start=1):
        print(f"排名：{rank}")
        print(f"Document：{hit.document_name}")
        print(f"相对路径：{hit.relative_path}")
        print(f"Chunk ID：{hit.chunk_id}")
        pages = getattr(hit, "page_numbers", (getattr(hit, "page_number", None),))
        print(f"页码：{', '.join(str(page) for page in pages if page is not None) or '不可用'}")
        print(f"Distance：{hit.distance:.6f}")
        print(f"Similarity：{hit.similarity:.6f}")
        print(f"文本：\n{hit.text}\n")


def _command_search(settings: Settings, args: argparse.Namespace) -> int:
    hits = _retriever(settings).search(
        args.question,
        top_k=args.top_k,
        document_selector=args.document,
    )
    _print_hits(hits)
    return 0


def _print_answer(result: AnswerResult, debug: bool) -> None:
    print(result.answer)
    print("\n实际检索来源：")
    for hit in result.hits:
        pages = getattr(hit, "page_numbers", (getattr(hit, "page_number", None),))
        page_label = "、".join(str(page) for page in pages if page is not None) or "页码不可用"
        print(
            f"- [{hit.document_name}，{page_label}，{hit.chunk_id}]"
        )
    if debug:
        print("\n--- Debug: Retrieval Hits ---")
        _print_hits(result.hits)
        print("--- Debug: Final Messages ---")
        for message in result.messages:
            print(f"[{message['role']}]\n{message['content']}\n")


def _command_ask(settings: Settings, args: argparse.Namespace) -> int:
    _print_answer(_pipeline(settings).ask(args.question), args.debug)
    return 0


def _command_chat(settings: Settings) -> int:
    store = _store(settings)
    embedder = E5Embedder(
        settings.embedding_model, settings.embedding_model_revision
    )
    pipeline = _pipeline(settings, embedder, store)
    manifest = (load_pipeline_manifest(settings.manifest_path, create_runtime(settings))
                if isinstance(settings, SelectedPipelineSettings) else load_manifest(settings.manifest_path))
    print(f"已索引文档数：{len(manifest.documents) if manifest else 0}")
    print(f"Collection：{settings.collection_name}")
    print(f"Embedding模型：{settings.embedding_model}")
    print(f"LLM：{settings.openrouter_model}")
    print("输入 /help 查看命令，输入 /exit 退出。")
    debug = False
    while True:
        try:
            question = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            return 0
        if not question:
            continue
        if question.casefold() in {"/exit", "/quit"}:
            return 0
        if question.casefold() == "/help":
            print("/help、/exit、/quit、/debug on、/debug off")
            continue
        if question.casefold() == "/debug on":
            debug = True
            print("Debug 已开启。")
            continue
        if question.casefold() == "/debug off":
            debug = False
            print("Debug 已关闭。")
            continue
        try:
            _print_answer(pipeline.ask(question), debug)
        except RagError as exc:
            print(f"错误：{exc.user_message()}", file=sys.stderr)


def _print_evaluation(results: list[EvaluationResult]) -> None:
    passed = total = 0
    for result in results:
        if result.passed is None:
            state = "INFO"
        else:
            state = "PASS" if result.passed else "FAIL"
            total += 1
            passed += int(result.passed)
        print(f"{state} | {result.case_id} | {result.question}")
        print(f"  {result.detail}")
    if total:
        print(f"总体通过率：{passed}/{total} ({passed / total:.1%})")


def _command_eval(settings: Settings, live: bool, top_k: int | None) -> int:
    retriever = _retriever(settings)
    if live:
        cases = load_evaluation_cases(settings.project_root / "eval" / "questions.json")
        results = Evaluator(retriever, _pipeline(settings)).evaluate_live(cases)
        _print_evaluation(results)
        return 0 if all(result.passed is not False for result in results) else 3
    output = run_formal_evaluation(settings, retriever, _store(settings), top_k or settings.top_k)
    print(f"正式检索评估已完成：{output.relative_to(settings.project_root)}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _validate_cli_args(parser, args)
    try:
        settings = select_pipeline(load_settings(require_api_key=False), args.pipeline)
        print(f"Pipeline: {settings.identity.pipeline_id}")
        if args.command == "documents":
            terminal = ConsoleTerminal()
            result, settings, inspection = ensure_index_ready(settings, _store(settings), terminal)
            return _command_documents(settings, inspection)
        if args.command == "ingest":
            return _command_ingest(settings, args)
        terminal = ConsoleTerminal()
        result, settings, _ = ensure_index_ready(settings, _store(settings), terminal)
        if result is not ReadinessResult.READY:
            raise IndexNotReadyError("索引尚未就绪，未执行原始命令。")
        if args.command in {"ask", "chat"} or (args.command == "eval" and args.live):
            result, settings = ensure_llm_credentials(settings, terminal)
            if result is not ReadinessResult.READY:
                raise IndexNotReadyError("未获得 LLM 凭据，未执行原始命令。")
        if args.command == "chunks":
            return _command_chunks(settings, args)
        if args.command == "browse":
            return _command_browse(settings, args)
        if args.command == "search":
            return _command_search(settings, args)
        if args.command == "ask":
            return _command_ask(settings, args)
        if args.command == "chat":
            return _command_chat(settings)
        if args.command == "eval":
            return _command_eval(settings, args.live, args.top_k)
        parser.error(f"未知命令：{args.command}")
    except RagError as exc:
        print(f"错误：{exc.user_message()}", file=sys.stderr)
        return exc.exit_code
    except Exception:
        print("错误：发生未预期的内部错误。", file=sys.stderr)
        return 1
    return 1

