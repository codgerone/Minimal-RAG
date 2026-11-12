"""Compare documents/, manifest.json and the vector store to tell what each document needs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

from rag.index.build_steps import STEP_LABELS, DocumentFacts, changed_steps, fingerprint
from rag.index.lexical import LexicalStore
from rag.index.manifest import DocumentEntry, Manifest
from rag.index.store import ChromaStore
from rag.index.tables import TableStore
from rag.ingest.chunkers import chunk_id
from rag.ingest.sources import has_text
from rag.models import SourceDocument
from rag.paths import Workspace

DocumentState = Literal["current", "new", "changed", "stale", "incomplete", "missing", "unreadable"]
# stale: a step the document passes through has other settings, rules or library versions now.
IndexState = Literal["not_built", "stale", "ready"]

STATE_LABELS = {
    "current": "已入库", "new": "未入库", "changed": "PDF 已修改，需更新",
    "stale": "构建设置已改变，需重建",
    "incomplete": "索引不完整，需重建", "missing": "PDF 已删除，可清理",
    "unreadable": "无可用文本（扫描件需配置 [ocr]，或文件损坏）",
}


@dataclass(frozen=True)
class DocumentStatus:
    state: DocumentState
    source: SourceDocument | None
    entry: DocumentEntry | None
    # Whether each index holds exactly the manifest's chunks for this document;
    # None when the document is not in the manifest, or (lexical) the config has no BM25 index.
    vectors_complete: bool | None = None
    lexical_complete: bool | None = None
    tables_complete: bool | None = None     # the whole-table store holds the manifest's table count
    # stale: the steps whose record changed, in pipeline order; () when the entry predates per-document records.
    changed_steps: tuple[str, ...] = ()

    @property
    def stale_reason(self) -> str:
        if not self.changed_steps:
            return "入库记录为 V4.1.1 之前的格式"
        return "、".join(STEP_LABELS[step] for step in self.changed_steps) + " 已改变"

    @property
    def relative_path(self) -> str:
        return self.source.relative_path if self.source else self.entry.relative_path  # type: ignore[union-attr]

    @property
    def needs_ingest(self) -> bool:
        return self.state in {"new", "changed", "stale", "incomplete"}


@dataclass(frozen=True)
class IndexStatus:
    config_name: str
    state: IndexState
    manifest: Manifest | None
    documents: tuple[DocumentStatus, ...]

    @property
    def queryable(self) -> bool:
        """Queries need a built index whose documents were built with the config's settings;
        documents whose PDF changed or was deleted only produce warnings."""
        return self.state == "ready" and any(d.state in {"current", "changed", "missing"}
                                             for d in self.documents)

    def count(self, state: DocumentState) -> int:
        return sum(item.state == state for item in self.documents)


def _complete(entry: DocumentEntry, ids: set[str], workspace: Workspace, config_name: str) -> bool:
    expected = {chunk_id(entry.document_id, i) for i in range(entry.chunk_count)}
    folder = workspace.index_dir(config_name) / "documents" / entry.folder
    return ids == expected and (folder / "chunks.json").is_file()


def expected_record(entry: DocumentEntry,
                    settings_for: Callable[[DocumentFacts], dict[str, Any]]) -> dict[str, Any] | None:
    """The build record the config would give this document now; None for a pre-V4.1.1 entry."""
    if entry.scanned_pages is None:
        return None
    return settings_for(DocumentFacts(entry.scanned_pages, entry.table_count > 0))


def check_status(workspace: Workspace, config_name: str,
                 settings_for: Callable[[DocumentFacts], dict[str, Any]],
                 sources: tuple[SourceDocument, ...], store: ChromaStore,
                 lexical: LexicalStore | None = None,
                 tables: TableStore | None = None, *, ocr: bool = False) -> IndexStatus:
    """`settings_for` gives a document's build record from its facts (rag/index/build_steps.py).
    `lexical` is the BM25 index when the config has one; its chunk IDs are checked like the vectors'.
    `tables` is the whole-table store; each document must hold as many tables as the manifest records.
    `ocr`: the config has [ocr], so documents with scanned pages are readable."""
    manifest = Manifest.load(workspace.index_dir(config_name) / "manifest.json")
    if manifest is None:
        documents = tuple(DocumentStatus("new" if has_text(s, ocr=ocr) else "unreadable", s, None)
                          for s in sources)
        return IndexStatus(config_name, "not_built", None, documents)
    stored_ids = store.ids_by_document()
    lexical_ids = lexical.ids_by_document() if lexical is not None else None
    table_counts = tables.counts_by_document() if tables is not None else None
    by_id = {source.document_id: source for source in sources}
    result: list[DocumentStatus] = []
    for source in sources:
        entry = manifest.documents.get(source.document_id)
        vectors = lexical_ok = tables_ok = None
        changed: tuple[str, ...] = ()
        if entry is not None:
            if table_counts is not None:
                tables_ok = table_counts.get(source.document_id, 0) == entry.table_count
            vectors = _complete(entry, stored_ids.get(source.document_id, set()), workspace, config_name)
            if lexical_ids is not None:
                lexical_ok = _complete(entry, lexical_ids.get(source.document_id, set()), workspace, config_name)
        if entry is None:
            state: DocumentState = "new" if has_text(source, ocr=ocr) else "unreadable"
        elif entry.file_hash != source.file_hash:
            state = "changed" if has_text(source, ocr=ocr) else "unreadable"
        elif not _record_current(entry, settings_for):
            state = "stale"
            expected = expected_record(entry, settings_for)
            if expected is not None and entry.build_settings is not None:
                changed = changed_steps(entry.build_settings, expected)
        elif not vectors or lexical_ok is False or tables_ok is False:
            state = "incomplete"
        else:
            state = "current"
        result.append(DocumentStatus(state, source, entry, vectors, lexical_ok, tables_ok, changed))
    for document_id, entry in manifest.documents.items():
        if document_id not in by_id:
            result.append(DocumentStatus("missing", None, entry))
    state: IndexState = "stale" if any(item.state == "stale" for item in result) else "ready"
    return IndexStatus(config_name, state, manifest, tuple(result))


def _record_current(entry: DocumentEntry, settings_for: Callable[[DocumentFacts], dict[str, Any]]) -> bool:
    expected = expected_record(entry, settings_for)
    return expected is not None and entry.fingerprint == fingerprint(expected)
