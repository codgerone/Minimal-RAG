"""Command-line interface for Minimal RAG."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import replace
from time import perf_counter

from rag.config import Settings, load_settings
from rag.document_registry import discover_documents, resolve_document_selector
from rag.embeddings import E5Embedder
from rag.errors import LlmServiceError, PdfParseError, RagError
from rag.evaluator import Evaluator, load_evaluation_cases
from rag.indexer import Indexer
from rag.llm import OpenRouterClient
from rag.manifest import load_manifest, validate_index
from rag.models import DocumentState, DocumentStatus, EvaluationResult, IngestSummary
from rag.pdf_parser import parse_pdf
from rag.pipeline import AnswerResult, RAGPipeline
from rag.retriever import Retriever
from rag.vector_store import ChromaVectorStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="uv run python -m rag",
        usage=(
            "uv run python -m rag [-h] "
            "{documents,ingest,chunks,search,ask,chat,eval} ..."
        ),
        description="针对本地 PDF 知识库的 Minimal RAG。",
        epilog="""命令总览:
  documents
    查看 documents/ 目录中的 PDF，以及它们的索引状态。

  ingest [--file PDF] [--force] [--prune]
    建立或增量更新向量索引。
    --file PDF  仅处理 documents/ 内指定的 PDF（支持相对路径或唯一文件名）。
    --force     强制重建索引；即使文件未变化也重新解析、分块和生成 Embedding。
    --prune     清理 documents/ 中已删除文件遗留的向量索引。
    注意：--file 与 --prune 不能同时使用。

  chunks --document PDF [--page PAGE]
    查看某个已索引 PDF 的文本块。
    --document PDF  指定文档（必填）。
    --page PAGE     仅查看指定页（正整数）。

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

  eval [--live]
    运行最小评估集，默认只评估检索结果。
    --live  额外调用 LLM，执行端到端冒烟评估。

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
    subparsers.add_parser("documents", help=argparse.SUPPRESS)

    ingest = subparsers.add_parser("ingest", help=argparse.SUPPRESS)
    ingest.add_argument("--file", help="仅处理 documents/ 内的指定 PDF")
    ingest.add_argument("--force", action="store_true", help="强制重建")
    ingest.add_argument(
        "--prune", action="store_true", help="删除已移除文档的向量索引"
    )

    chunks = subparsers.add_parser("chunks", help=argparse.SUPPRESS)
    chunks.add_argument("--document", required=True, help="相对路径或唯一文件名")
    chunks.add_argument("--page", type=int, help="只显示指定页码")

    search = subparsers.add_parser("search", help=argparse.SUPPRESS)
    search.add_argument("question", help="检索问题")
    search.add_argument("--top-k", type=int, dest="top_k")
    search.add_argument("--document", help="限制到指定文档")

    ask = subparsers.add_parser("ask", help=argparse.SUPPRESS)
    ask.add_argument("question", help="问题")
    ask.add_argument("--debug", action="store_true")
    subparsers.add_parser("chat", help=argparse.SUPPRESS)

    evaluate = subparsers.add_parser("eval", help=argparse.SUPPRESS)
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


def _store(settings: Settings) -> ChromaVectorStore:
    return ChromaVectorStore(settings.db_path, settings.collection_name)


def _retriever(
    settings: Settings,
    embedder: E5Embedder | None = None,
    store: ChromaVectorStore | None = None,
) -> Retriever:
    return Retriever(
        settings,
        embedder or E5Embedder(settings.embedding_model),
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


def _command_documents(settings: Settings) -> int:
    discovered = discover_documents(settings.documents_dir)
    if not discovered:
        raise RagError(
            f"目录中没有 PDF：{settings.documents_dir}",
            "请将 PDF 放入 documents/ 后重试。",
        )
    store = _store(settings)
    manifest = load_manifest(settings.manifest_path)
    health = validate_index(settings, discovered, manifest, store)
    statuses: list[DocumentStatus] = []
    source_by_id = {source.document_id: source for source in discovered}
    for status in health.document_statuses:
        if status.state not in {DocumentState.NEW, DocumentState.CHANGED}:
            statuses.append(status)
            continue
        try:
            pages = parse_pdf(source_by_id[status.document_id])
            statuses.append(replace(status, page_count=len(pages)))
        except PdfParseError as exc:
            statuses.append(
                replace(
                    status,
                    state=DocumentState.INVALID,
                    detail=exc.message,
                )
            )

    print("状态 | 相对路径 | 页数 | Chunks | 文件Hash前12位 | 索引时间")
    for status in sorted(statuses, key=lambda item: item.relative_path.casefold()):
        print(
            f"{status.state.value} | {status.relative_path} | "
            f"{status.page_count if status.page_count is not None else '-'} | "
            f"{status.chunk_count if status.chunk_count is not None else '-'} | "
            f"{(status.file_hash or '-')[:12]} | {status.indexed_at or '-'}"
        )
        if status.detail:
            print(f"  说明：{status.detail}")
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
    indexer = Indexer(
        settings,
        E5Embedder(settings.embedding_model),
        _store(settings),
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
        print(f"Document：{chunk.document_name}")
        print(f"Chunk ID：{chunk.chunk_id}")
        print(f"页码：{chunk.page_number}")
        print(f"Chunk Index：{chunk.chunk_index}")
        print(f"字符数：{len(chunk.text)}")
        print(f"文本：\n{chunk.text}\n")
    return 0


def _print_hits(hits) -> None:
    for rank, hit in enumerate(hits, start=1):
        print(f"排名：{rank}")
        print(f"Document：{hit.document_name}")
        print(f"相对路径：{hit.relative_path}")
        print(f"Chunk ID：{hit.chunk_id}")
        print(f"页码：{hit.page_number}")
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
        print(
            f"- [{hit.document_name}，第{hit.page_number}页，{hit.chunk_id}]"
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
    embedder = E5Embedder(settings.embedding_model)
    pipeline = _pipeline(settings, embedder, store)
    manifest = load_manifest(settings.manifest_path)
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


def _command_eval(settings: Settings, live: bool) -> int:
    cases = load_evaluation_cases(settings.project_root / "eval" / "questions.json")
    retriever = _retriever(settings)
    if live:
        results = Evaluator(retriever, _pipeline(settings)).evaluate_live(cases)
    else:
        results = Evaluator(retriever).evaluate_retrieval(cases)
    _print_evaluation(results)
    return 0 if all(result.passed is not False for result in results) else 3


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _validate_cli_args(parser, args)
    require_api_key = args.command in {"ask", "chat"} or (
        args.command == "eval" and args.live
    )
    try:
        settings = load_settings(require_api_key=require_api_key)
        if args.command == "documents":
            return _command_documents(settings)
        if args.command == "ingest":
            return _command_ingest(settings, args)
        if args.command == "chunks":
            return _command_chunks(settings, args)
        if args.command == "search":
            return _command_search(settings, args)
        if args.command == "ask":
            return _command_ask(settings, args)
        if args.command == "chat":
            return _command_chat(settings)
        if args.command == "eval":
            return _command_eval(settings, args.live)
        parser.error(f"未知命令：{args.command}")
    except RagError as exc:
        print(f"错误：{exc.user_message()}", file=sys.stderr)
        return exc.exit_code
    except Exception:
        print("错误：发生未预期的内部错误。", file=sys.stderr)
        return 1
    return 1

