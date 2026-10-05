"""Compare documents/, manifest.json and the vector store to tell what each document needs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from rag.index.manifest import DocumentEntry, Manifest
from rag.index.store import ChromaStore
from rag.ingest.chunkers import chunk_id
from rag.ingest.sources import has_text
from rag.models import SourceDocument
from rag.paths import Workspace

DocumentState = Literal["current", "new", "changed", "incomplete", "missing", "unreadable"]
IndexState = Literal["not_built", "config_changed", "ready"]

STATE_LABELS = {
    "current": "已入库", "new": "未入库", "changed": "PDF 已修改，需更新",
    "incomplete": "索引不完整，需重建", "missing": "PDF 已删除，可清理",
    "unreadable": "无可用文本（扫描件或损坏）",
}


@dataclass(frozen=True)
class DocumentStatus:
    state: DocumentState
    source: SourceDocument | None
    entry: DocumentEntry | None

    @property
    def relative_path(self) -> str:
        return self.source.relative_path if self.source else self.entry.relative_path  # type: ignore[union-attr]

    @property
    def needs_ingest(self) -> bool:
        return self.state in {"new", "changed", "incomplete"}


@dataclass(frozen=True)
class IndexStatus:
    config_name: str
    state: IndexState
    fingerprint: str
    manifest: Manifest | None
    documents: tuple[DocumentStatus, ...]

    @property
    def queryable(self) -> bool:
        """Queries need a built index matching the config; stale documents only produce warnings."""
        return self.state == "ready" and any(d.state in {"current", "changed", "missing"}
                                             for d in self.documents)

    def count(self, state: DocumentState) -> int:
        return sum(item.state == state for item in self.documents)


def _complete(entry: DocumentEntry, ids: set[str], workspace: Workspace, config_name: str) -> bool:
    expected = {chunk_id(entry.document_id, i) for i in range(entry.chunk_count)}
    folder = workspace.index_dir(config_name) / "documents" / entry.folder
    return ids == expected and (folder / "chunks.json").is_file()


def check_status(workspace: Workspace, config_name: str, fingerprint: str,
                 sources: tuple[SourceDocument, ...], store: ChromaStore) -> IndexStatus:
    manifest = Manifest.load(workspace.index_dir(config_name) / "manifest.json")
    if manifest is None:
        documents = tuple(DocumentStatus("new" if has_text(s) else "unreadable", s, None)
                          for s in sources)
        return IndexStatus(config_name, "not_built", fingerprint, None, documents)
    stored_ids = store.ids_by_document()
    by_id = {source.document_id: source for source in sources}
    result: list[DocumentStatus] = []
    for source in sources:
        entry = manifest.documents.get(source.document_id)
        if entry is None:
            state: DocumentState = "new" if has_text(source) else "unreadable"
        elif entry.file_hash != source.file_hash:
            state = "changed" if has_text(source) else "unreadable"
        elif not _complete(entry, stored_ids.get(source.document_id, set()), workspace, config_name):
            state = "incomplete"
        else:
            state = "current"
        result.append(DocumentStatus(state, source, entry))
    for document_id, entry in manifest.documents.items():
        if document_id not in by_id:
            result.append(DocumentStatus("missing", None, entry))
    state: IndexState = "ready" if manifest.fingerprint == fingerprint else "config_changed"
    return IndexStatus(config_name, state, fingerprint, manifest, tuple(result))
