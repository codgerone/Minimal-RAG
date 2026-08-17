"""CLI-only index inspection and user-confirmed recovery orchestration."""

from __future__ import annotations

import getpass
import os
import sys
from dataclasses import dataclass, replace
from enum import Enum
from typing import Protocol

from rag.config import Settings, update_env_key
from rag.document_registry import discover_documents
from rag.errors import PdfParseError, RagError
from rag.indexer import Indexer
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


def _indexer(settings: Settings, store: ChromaVectorStore) -> Indexer:
    from rag.embeddings import E5Embedder
    return Indexer(settings, E5Embedder(settings.embedding_model), store)


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
    updated = replace(settings, openrouter_api_key=key)
    if choice == "2":
        try:
            update_env_key(settings.project_root / ".env", "OPENROUTER_API_KEY", updated.openrouter_api_key)
        except RagError as exc:
            terminal.write(f"无法保存密钥：{exc.user_message()}")
            return ReadinessResult.FAILED, updated
    return ReadinessResult.READY, updated
