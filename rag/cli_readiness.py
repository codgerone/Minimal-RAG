"""CLI-only index inspection and user-confirmed recovery orchestration."""

from __future__ import annotations

import getpass
import json
import os
import sys
from dataclasses import dataclass, replace
from enum import Enum
from typing import Protocol

from rag.config import SelectedPipelineSettings, Settings, update_env_key
from rag.document_registry import discover_documents
from rag.errors import PdfParseError, RagError
from rag.bootstrap import create_runtime
from rag.pipeline_indexer import RuntimeIndexer
from rag.pipeline_manifest import load_pipeline_manifest
from rag.manifest import load_manifest, validate_index
from rag.models import DocumentState, DocumentStatus, IndexHealth, IndexIssue, Manifest, SourceDocument
from rag.pdf_parser import parse_pdf
from rag.vector_store import ChromaVectorStore

if os.name == "nt":
    import msvcrt


class Terminal(Protocol):
    def is_interactive(self) -> bool: ...
    def write(self, text: str = "") -> None: ...
    def confirm(self, prompt: str, *, default: bool) -> bool: ...
    def read_secret(self, prompt: str) -> str | None: ...
    def choose(self, prompt: str, choices: set[str], default: str) -> str: ...


class ConsoleTerminal:
    def is_interactive(self) -> bool:
        return sys.stdin.isatty() and sys.stdout.isatty()

    def write(self, text: str = "") -> None:
        print(text)

    def confirm(self, prompt: str, *, default: bool) -> bool:
        try:
            answer = input(prompt).strip().casefold()
        except (EOFError, KeyboardInterrupt):
            return False
        if not answer:
            return default
        return answer in {"y", "yes"}

    def read_secret(self, prompt: str) -> str | None:
        try:
            if os.name == "nt":
                return _read_masked_windows_secret(prompt)
            return getpass.getpass(prompt)
        except (EOFError, KeyboardInterrupt):
            return None

    def choose(self, prompt: str, choices: set[str], default: str) -> str:
        try:
            answer = input(prompt).strip().casefold()
        except (EOFError, KeyboardInterrupt):
            return default
        return answer if answer in choices else default


def _read_masked_windows_secret(prompt: str) -> str | None:
    """Read a Windows console secret while showing one mask per typed character."""
    sys.stdout.write(prompt)
    sys.stdout.flush()
    characters: list[str] = []
    while True:
        character = msvcrt.getwch()
        if character in {"\r", "\n"}:
            sys.stdout.write("\n")
            sys.stdout.flush()
            return "".join(characters)
        if character == "\x03":
            raise KeyboardInterrupt
        if character == "\x1a":
            return None
        if character in {"\b", "\x7f"}:
            if characters:
                characters.pop()
                sys.stdout.write("\b \b")
                sys.stdout.flush()
            continue
        if character.isprintable():
            characters.append(character)
            sys.stdout.write("*")
            sys.stdout.flush()


class ReadinessResult(str, Enum):
    READY = "ready"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True)
class IndexInspection:
    sources: tuple[SourceDocument, ...]
    manifest: Manifest | None
    health: IndexHealth
    pipeline_id: str | None = None
    collection_count: int | None = None

    @property
    def usable(self) -> bool:
        return self.health.usable

    @property
    def documents(self) -> tuple[DocumentStatus, ...]:
        return self.health.document_statuses

    @property
    def issues(self) -> tuple[IndexIssue, ...]:
        return self.health.issues


_DISPLAY = {
    DocumentState.CURRENT: ("可用", "-"),
    DocumentState.NEW: ("不可用", "新文档，尚未建立索引。"),
    DocumentState.CHANGED: ("不可用", "文档内容已变化，原索引已失效。"),
    DocumentState.INVALID: ("不可用", "索引数据与文档记录不一致，需要重新建立索引。"),
    DocumentState.MISSING: ("不可用", "未在 documents/ 找到源文件，原索引不可继续使用。"),
    DocumentState.UNASSESSED: ("不可用", "索引前置检查未完成。"),
}


def display_status(status: DocumentStatus) -> tuple[str, str]:
    if status.state is DocumentState.UNPROCESSABLE:
        return ("需人工处理", status.detail or "无法提取文字，请提供带文字层的 PDF 或完成 OCR。")
    label, detail = _DISPLAY[status.state]
    return label, status.detail or detail


def inspect_index_for_cli(settings: Settings, store: ChromaVectorStore) -> IndexInspection:
    if isinstance(settings, SelectedPipelineSettings):
        return _inspect_selected_pipeline(settings, store)
    sources = tuple(discover_documents(settings.documents_dir))
    manifest = load_manifest(settings.manifest_path)
    health = validate_index(settings, list(sources), manifest, store)
    by_id = {source.document_id: source for source in sources}
    statuses: list[DocumentStatus] = []
    extra_issues: list[IndexIssue] = []
    for status in health.document_statuses:
        if status.state not in {DocumentState.NEW, DocumentState.CHANGED}:
            statuses.append(status)
            continue
        try:
            pages = parse_pdf(by_id[status.document_id])
            statuses.append(replace(status, page_count=len(pages)))
        except PdfParseError as exc:
            detail = exc.message
            statuses.append(replace(status, state=DocumentState.UNPROCESSABLE, detail=detail))
            extra_issues.append(IndexIssue(f"unprocessable:{status.document_id}", detail, "请提供带文字层的 PDF，或完成 OCR 后重试。"))
    statuses.sort(key=lambda item: item.relative_path.casefold())
    issues = tuple((*health.issues, *extra_issues))
    return IndexInspection(sources, manifest, replace(health, usable=health.usable and not extra_issues, document_statuses=tuple(statuses), issues=issues))


def _inspect_selected_pipeline(settings: SelectedPipelineSettings, store: ChromaVectorStore) -> IndexInspection:
    sources = tuple(discover_documents(settings.documents_dir))
    runtime = create_runtime(settings)
    issues: list[IndexIssue] = []
    recovery = runtime.manifest_path.parents[2] / "recovery" / runtime.pipeline_id
    if recovery.exists() and any(path.name == "journal.json" for path in recovery.rglob("journal.json")):
        issues.append(IndexIssue("recovery_journal_present", "存在未完成的索引恢复事务。", "请显式执行 ingest 触发恢复。"))
    try:
        manifest = load_pipeline_manifest(runtime.manifest_path, runtime)
    except RagError as exc:
        manifest = None
        issues.append(IndexIssue("manifest_invalid", exc.message, "请对所选 pipeline 执行 ingest --force。"))
    if manifest and manifest.build_config_fingerprint != runtime.build_config_fingerprint:
        issues.append(IndexIssue("global_config_mismatch", "构建配置与现有索引不一致。", "请对所选 pipeline 执行 ingest --force。"))
    metadata = store.get_metadatas()
    by_doc: dict[str, list[dict]] = {}
    for item in metadata:
        by_doc.setdefault(str(item.get("document_id", "")), []).append(item)
    records = manifest.documents if manifest else {}
    if manifest is None and metadata:
        issues.append(IndexIssue("orphan_collection", "Collection 存在但所选 pipeline 的 Manifest 不存在。", "请执行 ingest --force。"))
    source_by_id = {item.document_id: item for item in sources}
    statuses = []
    for source in sources:
        record = records.get(source.document_id)
        if record is None:
            state, detail = DocumentState.NEW, None
        elif record.file_hash != source.file_hash:
            state, detail = DocumentState.CHANGED, None
        else:
            rows = by_doc.get(source.document_id, [])
            valid = len(rows) == record.chunk_count and all(
                str(row.get("file_hash")) == record.file_hash
                and (runtime.pipeline_id == "v1" or (
                    str(row.get("build_id")) == record.build_id
                    and str(row.get("build_config_fingerprint")) == record.build_config_fingerprint
                )) for row in rows
            )
            if valid and runtime.pipeline_id == "v2":
                artifact = settings.project_root / (record.artifact_path or "")
                required = ("raw-docling-document.json", "parsed-document.json", "chunks.json",
                            "selection-summary.json", "winner-review.html")
                valid = bool(record.artifact_path) and all((artifact / item).is_file() for item in required)
                if valid:
                    try:
                        json.loads((artifact / required[0]).read_text(encoding="utf-8"))
                        expected_schemas = {
                            "parsed-document.json": "parsed_document_v1",
                            "chunks.json": "document_chunks_v1",
                            "selection-summary.json": "table_selection_summary_v1",
                        }
                        for name, schema in expected_schemas.items():
                            envelope = json.loads((artifact / name).read_text(encoding="utf-8"))
                            expected = {
                                "schema_version": schema, "pipeline_id": "v2",
                                "document_id": record.document_id, "build_id": record.build_id,
                                "file_hash": record.file_hash,
                                "build_config_fingerprint": record.build_config_fingerprint,
                            }
                            if any(envelope.get(key) != value for key, value in expected.items()):
                                valid = False
                                break
                    except (OSError, UnicodeError, json.JSONDecodeError):
                        valid = False
            state, detail = (DocumentState.CURRENT, None) if valid else (DocumentState.INVALID, "向量或 active artifact 与 Manifest 不一致。")
            if not valid:
                issues.append(IndexIssue(f"artifact_index_mismatch:{source.document_id}", detail, "请重建该文档。"))
        page_count = record.page_count if record else None
        if state in {DocumentState.NEW, DocumentState.CHANGED}:
            try:
                page_count = len(parse_pdf(source))
            except PdfParseError as exc:
                state, detail = DocumentState.UNPROCESSABLE, exc.message
                issues.append(IndexIssue(f"unprocessable:{source.document_id}", detail,
                                         "请提供带文字层的 PDF，或完成 OCR 后重试。"))
        statuses.append(DocumentStatus(state, source.document_id, source.relative_path, source.document_name,
                                       source.file_hash, page_count,
                                       record.chunk_count if record else None,
                                       record.indexed_at if record else None, detail,
                                       recorded_file_hash=record.file_hash if record else None))
    for document_id, record in records.items():
        if document_id not in source_by_id:
            statuses.append(DocumentStatus(DocumentState.MISSING, document_id, record.relative_path,
                                           record.document_name, None, record.page_count,
                                           record.chunk_count, record.indexed_at, None,
                                           recorded_file_hash=record.file_hash))
    if manifest and set(by_doc) - set(records):
        issues.append(IndexIssue("unknown_documents", "Collection 包含 Manifest 未登记文档。", "请执行 ingest --force。"))
    if records and not store.collection_exists():
        issues.append(IndexIssue("collection_missing", "Manifest 已登记文档但 Collection 不存在。", "请重新建立索引。"))
    if runtime.pipeline_id == "v2":
        active = {record.artifact_path for record in records.values() if record.artifact_path}
        document_root = settings.artifacts_path / "v2" / "documents"
        if document_root.exists():
            for build in sorted(path for path in document_root.glob("*/*") if path.is_dir()):
                relative = build.resolve().relative_to(settings.project_root.resolve()).as_posix()
                if relative not in active:
                    issues.append(IndexIssue("orphan_artifact_build", f"发现未被 Manifest 引用的 artifact：{relative}",
                                             "可在下一次成功 ingest 后清理。", "artifact", None, False))
        staging_root = settings.artifacts_path / "v2" / "staging"
        if staging_root.exists() and any(path.is_dir() for path in staging_root.glob("*/*")):
            issues.append(IndexIssue("stale_artifact_staging", "发现未完成构建留下的 staging 目录。",
                                     "确认没有构建进程后可清理对应 staging。", "artifact", None, False))
    health = IndexHealth(not any(item.blocking for item in issues)
                         and all(item.state is DocumentState.CURRENT for item in statuses),
                         tuple(sorted(statuses, key=lambda item: item.relative_path.casefold())), tuple(issues))
    return IndexInspection(sources, manifest, health, runtime.pipeline_id,
                           store.count_all() if store.collection_exists() else 0)


def _indexer(settings: Settings, store: ChromaVectorStore):
    from rag.embeddings import E5Embedder
    if isinstance(settings, SelectedPipelineSettings):
        return RuntimeIndexer(
            settings, create_runtime(settings),
            E5Embedder(settings.embedding_model, settings.embedding_model_revision), store,
        )
    from rag.indexer import Indexer
    return Indexer(
        settings,
        E5Embedder(settings.embedding_model, settings.embedding_model_revision),
        store,
    )


def _write_issues(terminal: Terminal, inspection: IndexInspection) -> None:
    terminal.write("索引当前不可用：")
    for issue in inspection.health.issues:
        terminal.write(f"- {issue.message}")


def ensure_index_ready(settings: Settings, store: ChromaVectorStore, terminal: Terminal) -> tuple[ReadinessResult, Settings, IndexInspection]:
    inspection = inspect_index_for_cli(settings, store)
    while not inspection.health.usable:
        _write_issues(terminal, inspection)
        if not terminal.is_interactive():
            terminal.write("非交互环境不会修改索引。请先运行 documents 查看状态，再显式执行 ingest。")
            return ReadinessResult.STOPPED, settings, inspection
        codes = {issue.code for issue in inspection.health.issues}
        states = {status.state for status in inspection.health.document_statuses}
        try:
            if "recovery_journal_present" in codes:
                if not terminal.confirm("检测到未完成发布事务，是否立即恢复？[y/N] ", default=False):
                    return ReadinessResult.STOPPED, settings, inspection
                selected_indexer = _indexer(settings, store)
                if not isinstance(selected_indexer, RuntimeIndexer):
                    return ReadinessResult.FAILED, settings, inspection
                selected_indexer.publisher.recover_pending()
                inspection = inspect_index_for_cli(settings, store)
                continue
            if codes & {"orphan_collection", "global_config_mismatch", "unknown_documents", "collection_count_mismatch"}:
                if not terminal.confirm("是否立即全量重建？[y/N] ", default=False):
                    return ReadinessResult.STOPPED, settings, inspection
                summary = _indexer(settings, store).ingest_all(force=True)
            elif "collection_missing" in codes or states & {DocumentState.NEW, DocumentState.CHANGED, DocumentState.INVALID}:
                if not terminal.confirm("是否立即建立或更新索引？[Y/n] ", default=True):
                    return ReadinessResult.STOPPED, settings, inspection
                summary = _indexer(settings, store).ingest_all()
            elif DocumentState.MISSING in states:
                if not terminal.confirm("确认清理这些遗留索引吗？[y/N] ", default=False):
                    return ReadinessResult.STOPPED, settings, inspection
                summary = _indexer(settings, store).ingest_all(prune=True)
            else:
                for status in inspection.health.document_statuses:
                    if status.state is DocumentState.UNPROCESSABLE:
                        terminal.write(f"需人工处理：{status.relative_path}：{status.detail}")
                return ReadinessResult.STOPPED, settings, inspection
        except RagError as exc:
            terminal.write(f"修复失败：{exc.user_message()}")
            return ReadinessResult.FAILED, settings, inspection
        if summary.failed:
            terminal.write("修复未完成，请处理失败的 PDF 后重试。")
            return ReadinessResult.FAILED, settings, inspection
        inspection = inspect_index_for_cli(settings, store)
    return ReadinessResult.READY, settings, inspection


def ensure_llm_credentials(settings: Settings, terminal: Terminal) -> tuple[ReadinessResult, Settings]:
    def valid_key(value: str | None) -> bool:
        return bool(value) and not any(character.isspace() for character in value)

    if valid_key(settings.openrouter_api_key):
        return ReadinessResult.READY, settings
    if not terminal.is_interactive():
        terminal.write("缺少 OPENROUTER_API_KEY；非交互环境无法录入密钥。")
        return ReadinessResult.STOPPED, settings
    if settings.openrouter_api_key:
        terminal.write("现有 OPENROUTER_API_KEY 格式无效（不应包含空格）；请重新录入。")
    key = terminal.read_secret("请输入 OpenRouter API Key：")
    key = key.strip() if key else ""
    if not valid_key(key):
        if key:
            terminal.write("API Key 格式无效：不得包含空格或换行，未保存。")
        return ReadinessResult.STOPPED, settings
    terminal.write(f"已录入 API Key：{'*' * len(key)}")
    choice = terminal.choose("1=仅本次使用，2=保存到 .env，0=取消：", {"0", "1", "2"}, "0")
    if choice == "0":
        return ReadinessResult.STOPPED, settings
    if isinstance(settings, SelectedPipelineSettings):
        updated = replace(
            settings,
            application=replace(settings.application, openrouter_api_key=key),
        )
    else:
        updated = replace(settings, openrouter_api_key=key)
    if choice == "2":
        try:
            update_env_key(settings.project_root / ".env", "OPENROUTER_API_KEY", updated.openrouter_api_key)
        except RagError as exc:
            terminal.write(f"无法保存密钥：{exc.user_message()}")
            return ReadinessResult.FAILED, updated
    return ReadinessResult.READY, updated
