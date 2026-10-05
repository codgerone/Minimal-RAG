"""V3 command surface. The project entry switches here only after acceptance gates."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import secrets
import sys
import tempfile
from dataclasses import asdict, replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Sequence

from dotenv import load_dotenv

from rag.v3.adapters.config_store import ConfigurationStore
from rag.v3.adapters.e5 import E5Error
from rag.v3.adapters.local_registry import LocalPdfRegistry, RegistryRequest
from rag.v3.adapters.openrouter import LanguageModelError
from rag.v3.application.assembly import (
    AssemblyError, builtin_configuration, effective_builtin_configuration,
    index_identity, make_configuration,
    validate_configuration,
)
from rag.v3.application.live_smoke import run_live_smoke
from rag.v3.contracts.assembly import PluginBinding
from rag.v3.contracts.processing import IndexerInput
from rag.v3.contracts.retrieval import AnswerRequest, RetrievalRequest
from rag.v3.contracts.runtime import CommandContext
from rag.v3.plugins.runtime_factory import RuntimeFactory
from rag.v3.plugins.catalog import PARAMETERS, PLUGINS, SLOTS


def _positive(value: str) -> int:
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return result


def _nonnegative(value: str) -> int:
    result = int(value)
    if result < 0:
        raise argparse.ArgumentTypeError("must be nonnegative")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m rag",
        description="Minimal RAG V3：选择已保存的接口—插件装配配置。",
        epilog=("省略选择参数时使用默认装配（初始为 structured）。"
                "--config NAME 与 --configure 互斥；仅 ingest 支持 --all-configs。"
                "检索 K 默认读取 TOP_K，未设置时为 4。"))
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("documents", "ingest", "chunks", "browse", "search", "ask", "chat", "eval"):
        command = commands.add_parser(name)
        selection = command.add_mutually_exclusive_group()
        selection.add_argument("--config", metavar="NAME", help="选择已保存的 V3 装配")
        selection.add_argument("--configure", action="store_true",
                               help="交互列出插件和索引状态，或建立装配")
        if name == "ingest":
            selection.add_argument("--all-configs", action="store_true",
                                   help="逐份处理所有已保存配置")
            scope = command.add_mutually_exclusive_group()
            scope.add_argument("--all", action="store_true", help="处理全部 PDF（默认）")
            scope.add_argument("--file", help="相对路径或唯一文件名")
            command.add_argument("--force", action="store_true", help="强制重建选定范围")
            command.add_argument("--prune", action="store_true", help="明确清理已删除 PDF")
        elif name == "chunks":
            command.add_argument("--document", required=True)
            command.add_argument("--page", type=_positive)
        elif name == "browse":
            command.add_argument("--document")
            command.add_argument("--offset", type=_nonnegative, default=0)
            command.add_argument("--limit", type=_positive, default=20)
            command.add_argument("--full-metadata", action="store_true")
        elif name == "search":
            command.add_argument("question")
            command.add_argument("--top-k", type=_positive)
            command.add_argument("--document")
        elif name == "ask":
            command.add_argument("question")
            command.add_argument("--top-k", type=_positive)
            command.add_argument("--debug", action="store_true")
        elif name == "chat":
            command.add_argument("--top-k", type=_positive)
        elif name == "eval":
            command.add_argument("--top-k", type=_positive)
            command.add_argument("--live", action="store_true",
                                 help="额外 LLM 冒烟，不计正式指标")
    return parser


def _choose_config(store: ConfigurationStore) -> str:
    names = store.list_names()
    print("已保存装配：")
    root = store.root.parent.parent
    try:
        sources = _sources(root)
    except (ValueError, OSError) as exc:
        sources = None
        source_status = str(exc)
    for number, name in enumerate(names, 1):
        config = effective_builtin_configuration(store.load(name), os.environ)
        print(f"{number}. {name} · 构建 {index_identity(config).build_fingerprint[:12]}")
        print("   插件：" + ", ".join(f"{item.slot_id}={item.plugin_id}"
                                   for item in config.bindings))
        if sources is None:
            print(f"   索引状态：未检查（{source_status}）")
        else:
            try:
                with RuntimeFactory(config, root, sources) as factory:
                    report = factory.one("system.index_health").check(factory.index, sources)
                state = "可用" if report.usable else "不可用"
                codes = ", ".join(sorted({issue.code for issue in report.issues}))
                print(f"   索引状态：{state}" + (f" · {codes}" if codes else ""))
            except Exception as exc:
                print(f"   索引状态：检查失败（{exc}）")
    print("0. 新建配置")
    choice = input("选择配置编号：").strip()
    if choice == "0":
        return _create_config_wizard(store)
    if not choice.isdigit() or not 1 <= int(choice) <= len(names):
        raise ValueError("配置选择已取消或无效")
    return names[int(choice) - 1]


def _prompt(label: str, default: str = "") -> str:
    value = input(f"{label}{f' [{default}]' if default else ''}：").strip()
    if value.casefold() in {"q", "quit", "/quit"}:
        raise ValueError("配置向导已取消；未保存草稿")
    return value or default


def _with_binding(config, slot_id: str,
                  replacement: tuple[PluginBinding, ...]):
    bindings = tuple(item for item in config.bindings if item.slot_id != slot_id)
    return replace(config, bindings=bindings + replacement)


def _valid_candidate(config, slot_id: str,
                     replacement: tuple[PluginBinding, ...]) -> bool:
    try:
        validate_configuration(_with_binding(config, slot_id, replacement))
        return True
    except ValueError:
        return False


def _create_config_wizard(store: ConfigurationStore) -> str:
    print("从一个完整合法装配出发，逐接口选择当前注册的兼容插件。输入 q 取消。")
    seed = _prompt("装配起点 1=plain_text，2=structured", "2")
    if seed not in {"1", "2"}:
        raise ValueError("装配起点无效；未保存配置")
    config = builtin_configuration("plain_text" if seed == "1" else "structured")
    order = ("document_processor.main_parser", "document_processor.table_extractor",
             "document_processor.table_selector", "document_processor.document_assembler",
             "indexer.chunker")
    slots = tuple(dict.fromkeys((*order, *(item.slot_id for item in config.bindings))))
    for slot_id in slots:
        slot = SLOTS[slot_id]
        current = tuple(item for item in config.bindings if item.slot_id == slot_id)
        if slot.cardinality == "multi":
            if not current:
                continue
            print(f"{slot_id}：当前 " + ", ".join(
                f"{number}={item.plugin_id}" for number, item in enumerate(current, 1)))
            keep = _prompt("保留提取器编号（逗号分隔，all=全部，none=不接入）", "all")
            if keep == "none":
                replacement = ()
                trial = _with_binding(config, slot_id, replacement)
                for conditional in ("document_processor.table_selector",
                                    "table_selector.pdf_evidence_reader"):
                    trial = _with_binding(trial, conditional, ())
                validate_configuration(trial)
                config = trial
            elif keep != "all":
                indices = tuple(int(value) for value in keep.split(","))
                if not indices or any(not 1 <= number <= len(current) for number in indices):
                    raise ValueError("提取器选择无效；未保存配置")
                replacement = tuple(replace(current[number - 1], order=position)
                                    for position, number in enumerate(indices))
                trial = _with_binding(config, slot_id, replacement)
                validate_configuration(trial)
                config = trial
            continue
        if not current:
            continue
        binding = current[0]
        compatible = tuple(plugin_id for plugin_id, plugin in PLUGINS.items()
            if plugin.interface_id == slot.interface_id and _valid_candidate(config,
                slot_id, (replace(binding, plugin_id=plugin_id, parameters={}),)))
        if not compatible:
            raise ValueError(f"{slot_id} 无可完成候选；未保存配置")
        print(f"{slot_id}：" + ", ".join(f"{n}={item}"
              for n, item in enumerate(compatible, 1)))
        default = str(compatible.index(binding.plugin_id) + 1)
        choice = _prompt("选择插件编号", default)
        if not choice.isdigit() or not 1 <= int(choice) <= len(compatible):
            raise ValueError("插件选择无效；未保存配置")
        plugin_id = compatible[int(choice) - 1]
        parameters = binding.parameters if plugin_id == binding.plugin_id else {}
        updated = replace(binding, plugin_id=plugin_id, parameters=parameters)
        if PARAMETERS.get(plugin_id) and _prompt("编辑此插件参数？y/N", "N").lower() == "y":
            parameters = dict(updated.parameters)
            for key, spec in PARAMETERS[plugin_id].items():
                shown = parameters.get(key, spec.default)
                entered = input(f"{key}（当前 {shown}；留空保留，输入 JSON 值修改）：").strip()
                if entered.casefold() in {"q", "quit", "/quit"}:
                    raise ValueError("配置向导已取消；未保存草稿")
                if not entered:
                    continue
                value = json.loads(entered, parse_float=Decimal)
                if spec.kind is tuple and isinstance(value, list):
                    value = (tuple(Decimal(str(item)) for item in value)
                             if spec.default and isinstance(spec.default[0], Decimal)
                             else tuple(value))
                parameters[key] = value
            updated = replace(updated, parameters=parameters)
        config = _with_binding(config, slot_id, (updated,))
        validate_configuration(config)
    while True:
        name = _prompt("新配置 NAME（ASCII 字母/数字/_/-）")
        try:
            final = make_configuration(name, config.bindings)
            validate_configuration(final)
            store.save_new(final)
        except AssemblyError as exc:
            if exc.code not in {"invalid_configuration", "reserved_name",
                                "duplicate_name"}:
                raise
            print(f"名称不可用：{exc}；请重新输入。")
            continue
        try:
            store.get_default()
        except AssemblyError as exc:
            if exc.code != "missing_default":
                raise
            store.set_default(name)
        print(f"已保存配置：{name}")
        return name


def resolve_context(args: argparse.Namespace, store: ConfigurationStore,
                    *, interactive: bool, default_k: int) -> CommandContext:
    if args.command == "ingest" and args.file and args.prune:
        raise ValueError("--file 与 --prune 互斥")
    if getattr(args, "all_configs", False):
        return CommandContext("ingest", None, None, None, interactive,
            "all_configs", None, args.file, False, args.force, args.prune, False)
    if args.configure and not interactive:
        raise ValueError("--configure 需要交互终端")
    if args.config is None and not args.configure and not store.list_names():
        if not interactive:
            raise ValueError("尚无已保存配置；请在交互终端使用 --configure 建立装配")
        response = input("尚无已保存配置，是否安装并采用内置最佳装配 structured？y/N：")
        if response.strip().casefold() == "y":
            store.install_builtins()
            name, mode = store.get_default().name, "default"
        else:
            name, mode = _create_config_wizard(store), "wizard"
    else:
        name = (_choose_config(store) if args.configure else args.config
                if args.config is not None else store.get_default().name)
        mode = "wizard" if args.configure else "named" if args.config else "default"
    try:
        configuration = effective_builtin_configuration(store.load(name), os.environ)
    except AssemblyError as exc:
        if exc.code != "unknown_configuration":
            raise
        available = ", ".join(store.list_names()) or "无"
        raise ValueError(f"未知配置 {name}；可用配置：{available}") from exc
    query_k = (getattr(args, "top_k", None) or default_k
               if args.command in {"search", "ask", "chat", "eval"} else None)
    return CommandContext(args.command, configuration.name, configuration,
        index_identity(configuration), interactive, mode, query_k,
        getattr(args, "document", None) or getattr(args, "file", None),
        getattr(args, "debug", False), getattr(args, "force", False),
        getattr(args, "prune", False), getattr(args, "live", False))


def _sources(root: Path, selector: str | None = None):
    return LocalPdfRegistry().discover(RegistryRequest(root / "documents", selector))


def _factory(context: CommandContext, root: Path, sources):
    retry = (lambda: _request_llm_key(root, "OpenRouter 认证失败；输入新 Key 重试一次")
             if context.interactive else None)
    return RuntimeFactory(context.configuration, root, sources,
                          auth_retry_provider=retry)


def _require_healthy(factory: RuntimeFactory, sources, *, interactive: bool) -> None:
    for _ in range(4):
        report = factory.one("system.index_health").check(factory.index, sources)
        if report.usable:
            return
        codes = {issue.code for issue in report.issues}
        print("索引问题：" + ", ".join(sorted(codes)))
        if not interactive:
            raise ValueError("所选配置索引不可用；请先执行 ingest 或恢复")
        if codes & {"pending_recovery"}:
            action, default = "recover", "y"
        elif codes & {"recovery_failed", "source_unprocessable"}:
            raise ValueError("索引需要人工修复源文件或恢复证据")
        elif codes & {"manifest_invalid", "manifest_missing_with_collection",
                      "configuration_mismatch", "collection_manifest_count_mismatch",
                      "unknown_vector_document"}:
            action, default = "force", "N"
        elif codes & {"source_missing"}:
            action, default = "prune", "N"
        else:
            action, default = "incremental_ingest", "y"
        response = input(f"是否对当前配置执行 {action} 后继续原命令？y/N "
                         f"（默认 {default}）：").strip().casefold() or default.casefold()
        if response != "y":
            raise ValueError("已取消索引修复；原命令未执行")
        if action == "recover":
            journals = factory.one("index_health.recovery_store").list_pending(factory.index)
            for journal in journals:
                result = factory.one("system.recovery").recover(factory.index,
                                                                journal.transaction_id)
                if result.outcome == "failed":
                    raise ValueError("索引恢复未能验证完成")
        else:
            result = factory.one("system.indexer").ingest(IndexerInput(
                factory.configuration.name, factory.index, sources, None,
                action == "force", action == "prune", action == "prune"))
            if any(item.state == "failed" for item in result.documents):
                raise ValueError("索引修复含文档失败；原命令未执行")
    raise ValueError("索引修复后仍未就绪；原命令未执行")


def _show_hits(result, *, debug: bool = False) -> None:
    for number, hit in enumerate(result.hits, 1):
        pages = sorted({span.page_number for source in hit.sources
                        for span in source.page_spans})
        print(f"#{number} {hit.document_name} · 页 {pages} · {hit.chunk_id}"
              f" · distance={hit.distance:.6f}")
        print(hit.text)


def _request_llm_key(root: Path, label: str) -> str | None:
    value = getpass.getpass(label + "（仅本进程使用；留空取消）：").strip()
    if not value or "\n" in value or "\r" in value:
        return None
    os.environ["OPENROUTER_API_KEY"] = value
    if input("是否明确保存到 .env？y/N：").strip().casefold() != "y":
        return value
    target = root / ".env"
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    lines = existing.splitlines()
    updated = False
    for position, line in enumerate(lines):
        if line.strip().startswith("OPENROUTER_API_KEY="):
            lines[position] = "OPENROUTER_API_KEY=" + value
            updated = True
    if not updated:
        lines.append("OPENROUTER_API_KEY=" + value)
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    with tempfile.NamedTemporaryFile("wb", dir=root, prefix=".env-v3-",
                                     delete=False) as stream:
        staging = Path(stream.name)
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(staging, target)
    finally:
        staging.unlink(missing_ok=True)
    return value


def _ensure_llm_key(root: Path, interactive: bool) -> None:
    if os.getenv("OPENROUTER_API_KEY", "").strip():
        return
    if not interactive:
        raise LanguageModelError("llm_missing_credentials",
                                 "缺少 OPENROUTER_API_KEY；非交互命令不会等待输入")
    if not _request_llm_key(root, "OpenRouter API Key"):
        raise ValueError("未获得有效 OpenRouter API Key")


def _one(context: CommandContext, args: argparse.Namespace, root: Path,
         batch_summary: list[str] | None = None) -> int:
    sources = _sources(root)
    with _factory(context, root, sources) as factory:
        return _one_bound(context, args, root, sources, factory, batch_summary)


def _one_bound(context: CommandContext, args: argparse.Namespace, root: Path,
               sources, factory: RuntimeFactory,
               batch_summary: list[str] | None = None) -> int:
    print(f"装配：{context.configuration_name}；collection：{factory.index.collection_name}")
    if context.command == "documents":
        if context.interactive:
            _require_healthy(factory, sources, interactive=True)
        report = factory.one("system.index_health").check(factory.index, sources)
        print("索引状态：" + ("可用" if report.usable else "不可用"))
        for item in report.document_statuses:
            print(f"{item.relative_path} | {item.state} | 页 {item.page_count} | "
                  f"chunks {item.chunk_count}")
        for issue in report.issues:
            print(f"问题：{issue.code} · {issue.document_id or '组合'}")
        return 0 if report.usable else 2
    if context.command == "ingest":
        selected = (LocalPdfRegistry.resolve_selector(args.file, sources).document_id
                    if args.file else None)
        result = factory.one("system.indexer").ingest(IndexerInput(
            context.configuration_name, factory.index, sources, selected,
            context.force, context.prune, context.prune))
        for item in result.documents:
            print(f"{item.state} | {item.relative_path}"
                  + (f" | {item.error.code}" if item.error else ""))
        print(f"PDF {result.scanned}；collection records {result.collection_count}")
        failed = any(item.state == "failed" for item in result.documents)
        disposition = ("失败" if failed else "跳过" if not result.documents or
                       all(item.state in {"skipped", "missing"}
                           for item in result.documents) else "成功")
        if batch_summary is not None:
            batch_summary.append(disposition)
        if any(item.error is not None and item.error.code == "embedding_failed"
               for item in result.documents):
            return 4
        return 3 if failed else 0
    _require_healthy(factory, sources, interactive=context.interactive)
    if context.command in {"chunks", "browse"}:
        selected = (LocalPdfRegistry.resolve_selector(context.document_selector, sources)
                    if context.document_selector else None)
        records = factory.one("index_health.vector_reader").list_text_metadata(
            factory.index, selected.document_id if selected else None)
        records = tuple(sorted(records, key=lambda item:
            (item.metadata.relative_path, item.metadata.chunk_index, item.chunk_id)))
        if context.command == "chunks":
            records = tuple(item for item in records
                if args.page is None or args.page in item.metadata.page_numbers)
            if not records:
                raise ValueError("未找到该文档的已索引 chunk")
        else:
            print(f"记录总数：{len(records)}")
            records = records[args.offset:args.offset + args.limit]
        for item in records:
            print(f"{item.chunk_id} | {item.metadata.relative_path} | "
                  f"页 {item.metadata.page_numbers} | 序号 {item.metadata.chunk_index}")
            print(item.text)
            if context.command == "browse":
                metadata = asdict(item.metadata)
                if not args.full_metadata:
                    metadata.pop("sources", None)
                print(json.dumps(metadata, ensure_ascii=False, sort_keys=True))
        return 0
    if context.command == "search":
        selected = (LocalPdfRegistry.resolve_selector(args.document, sources)
                    if args.document else None)
        result = factory.one("system.retriever").retrieve(RetrievalRequest(
            args.question, context.query_k, selected.document_id if selected else None,
            factory.index, context.configuration_name))
        _show_hits(result)
        return 0
    if context.command == "ask":
        _ensure_llm_key(root, context.interactive)
        result = factory.one("system.chatbot").answer(AnswerRequest(
            args.question, context.query_k, None, context.configuration_name, factory.index))
        print(result.answer)
        if context.debug:
            _show_hits(result.retrieval, debug=True)
            for message in result.messages:
                print(f"[{message.role}] {message.content}")
        return 0
    if context.command == "chat":
        _ensure_llm_key(root, context.interactive)
        debug = False
        while True:
            question = input("> ").strip()
            if question in {"/exit", "/quit"}:
                return 0
            if question == "/help":
                print("/help /exit /quit /debug on /debug off")
                continue
            if question in {"/debug on", "/debug off"}:
                debug = question.endswith("on")
                continue
            if not question:
                continue
            result = factory.one("system.chatbot").answer(AnswerRequest(
                question, context.query_k, None, context.configuration_name, factory.index))
            print(result.answer)
            if debug:
                _show_hits(result.retrieval, debug=True)
                for message in result.messages:
                    print(f"[{message.role}] {message.content}")
    if context.command == "eval":
        if context.live:
            _ensure_llm_key(root, context.interactive)
            results = run_live_smoke(factory.one("evaluator.evaluation_repository"),
                factory.one("system.chatbot"), factory.index,
                context.configuration_name, context.query_k)
            for result in results:
                print(f"{result.status} | {result.case_id}"
                      + (f" | {result.answer}" if result.answer is not None
                         else f" | {result.error_code}"))
            return 3 if any(item.status == "failed" for item in results) else 0
        folder = (root / "eval" / "test-sets" /
            f"system-v3.0__assembly-{context.configuration_name}__cfg-"
            f"{factory.index.build_fingerprint[:12]}")
        manifest = json.loads((folder / "manifest.json").read_bytes())
        ground = json.loads((root / "eval/ground-truth/manifest.json").read_bytes())
        from rag.v3.contracts.evaluation import (
            EvaluationQueryConfig, EvaluationRunRequest, GroundTruthIdentity,
            TestSetIdentity,
        )
        request = EvaluationRunRequest(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "-" + secrets.token_hex(6), context.configuration_name, factory.index,
            factory.projection, EvaluationQueryConfig(context.query_k, "query: ", None,
                "strip_v1", "ascending", "chunk_id_ascending", True),
            GroundTruthIdentity(ground["dataset_version"],
                                ground["ground_truth_fingerprint"]),
            TestSetIdentity(manifest["test_set_version"],
                            manifest["test_set_fingerprint"],
                            manifest["annotation_rule_version"]),
            "retrieval_evaluation_v2", datetime.now(timezone.utc).isoformat())
        result = factory.one("system.evaluator").run(request)
        print(f"评估 {result.run_id}：{result.status}"
              + (f" · {result.error.code}" if result.error else ""))
        return 0 if result.status == "completed" else 2
    raise ValueError("unknown V3 command")


def main(argv: Sequence[str] | None = None, *, workspace_root: Path | None = None,
         interactive: bool | None = None) -> int:
    if os.name == "nt":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(argv)
    root = (workspace_root or Path.cwd()).resolve()
    load_dotenv(root / ".env", override=False)
    try:
        try:
            default_k = _positive(os.getenv("TOP_K", "4"))
        except (ValueError, argparse.ArgumentTypeError) as exc:
            raise ValueError("TOP_K 必须是正整数") from exc
        store = ConfigurationStore(root / ".rag" / "system-v3")
        context = resolve_context(args, store,
            interactive=sys.stdin.isatty() if interactive is None else interactive,
            default_k=default_k)
        if context.selection_mode == "all_configs":
            outcomes = []
            summaries = []
            names = store.list_names()
            if not names:
                raise ValueError("没有已保存配置可供 --all-configs 入库")
            for name in names:
                selected_args = argparse.Namespace(**vars(args))
                selected_args.config = name
                selected_args.all_configs = False
                selected = resolve_context(selected_args, store,
                    interactive=context.interactive, default_k=default_k)
                try:
                    batch_summary: list[str] = []
                    code = _one(selected, selected_args, root, batch_summary)
                    outcomes.append(code)
                    summaries.append((name, batch_summary[0] if batch_summary else
                                      "失败", "文档失败" if code else ""))
                except Exception as exc:
                    print(f"{name}：失败 · {exc}", file=sys.stderr)
                    outcomes.append(3)
                    summaries.append((name, "失败", str(exc)))
            print("各装配入库结果：")
            for name, status, reason in summaries:
                print(f"{name} | {status}" + (f" | {reason}" if reason else ""))
            return 3 if any(outcome != 0 for outcome in outcomes) else 0
        return _one(context, args, root)
    except (E5Error, LanguageModelError) as exc:
        print(f"V3 命令失败：{exc.code} · {exc}", file=sys.stderr)
        return 4
    except (ValueError, OSError) as exc:
        print(f"V3 命令失败：{exc}", file=sys.stderr)
        return 2
