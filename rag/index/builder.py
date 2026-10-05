"""Ingest: turn PDFs into chunks + vectors + audit files, keeping the index consistent.

Consistency rule: manifest.json is written last. If anything fails before that, the
manifest still describes the old state and `status` reports the document as needing
work; re-running ingest repairs it. A full `--force` rebuild processes every PDF
first and only touches the index once all of them succeeded.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import Callable, Literal

from rag.index.manifest import DocumentEntry, Manifest, now
from rag.index.status import IndexStatus, check_status
from rag.index.store import ChromaStore, StoredChunk
from rag.ingest.pipeline import ProcessedDocument, process_document
from rag.ingest.sources import discover, select
from rag.jsonio import write_json
from rag.models import ChunkBatch, SourceDocument
from rag.paths import Workspace, readable_name
from rag.registry import Assembly

Action = Literal["added", "updated", "rebuilt", "skipped", "failed", "pruned", "unreadable"]


class IngestError(RuntimeError):
    pass


@dataclass(frozen=True)
class DocumentOutcome:
    relative_path: str
    action: Action
    chunk_count: int = 0
    error: str | None = None


@dataclass(frozen=True)
class Prepared:
    source: SourceDocument
    processed: ProcessedDocument
    batch: ChunkBatch
    vectors: list[list[float]]
    entry: DocumentEntry


def open_store(workspace: Workspace, config_name: str) -> ChromaStore:
    return ChromaStore(workspace.index_dir(config_name) / "chroma")


def current_status(workspace: Workspace, assembly: Assembly,
                   sources: tuple[SourceDocument, ...] | None = None) -> IndexStatus:
    sources = discover(workspace.documents) if sources is None else sources
    return check_status(workspace, assembly.name, assembly.fingerprint(), sources,
                        open_store(workspace, assembly.name))


def prepare(source: SourceDocument, assembly: Assembly) -> Prepared:
    processed = process_document(source, assembly.parser, assembly.extractors, assembly.formatter)
    batch = assembly.chunker.chunk(processed.document)
    vectors = assembly.embedder.encode_passages([chunk.text for chunk in batch.chunks])
    entry = DocumentEntry(source.document_id, source.document_name, source.relative_path,
                          source.file_hash, readable_name(source.relative_path),
                          processed.parse.primary.page_count, len(batch.chunks),
                          len(processed.prepared_tables), now())
    return Prepared(source, processed, batch, vectors, entry)


def _stored(prepared: Prepared) -> list[StoredChunk]:
    source = prepared.source
    return [StoredChunk(chunk.chunk_id, source.document_id, source.document_name,
                        source.relative_path, chunk.chunk_index, chunk.kind,
                        tuple(dict.fromkeys(span.page_number for item in chunk.sources
                                            for span in item.page_spans)),
                        chunk.token_count, chunk.text)
            for chunk in prepared.batch.chunks]


def write_audit_files(folder, prepared: Prepared) -> None:
    """Intermediate results of every stage, for debugging and the HTML reports."""
    processed = prepared.processed
    if folder.exists():
        shutil.rmtree(folder)
    write_json(folder / "parse-native.json", processed.parse.native)
    write_json(folder / "parsed.json", processed.document)
    if processed.parse.primary.table_slots:
        write_json(folder / "tables.json", {
            "slots": processed.parse.primary.table_slots,
            "extraction_reports": processed.extraction_reports,
            "grouping": processed.grouping,
            "scoring": processed.scoring,
            "resolutions": processed.resolutions,
            "prepared_tables": processed.prepared_tables,
        })
    write_json(folder / "chunks.json", prepared.batch)


def _write_reports(workspace: Workspace, assembly: Assembly, prepared: Prepared) -> None:
    from rag.reports.ingest import write_document_reports
    write_document_reports(workspace, assembly.name, prepared.processed, prepared.batch,
                           prepared.entry)


def _new_manifest(assembly: Assembly) -> Manifest:
    return Manifest(assembly.name, assembly.fingerprint(), assembly.build_settings(), now())


def ingest(workspace: Workspace, assembly: Assembly, *, file: str | None = None,
           force: bool = False, prune: bool = False,
           progress: Callable[[str], None] = lambda message: None) -> list[DocumentOutcome]:
    sources = discover(workspace.documents)
    status = current_status(workspace, assembly, sources)
    if status.state == "config_changed" and not (force and file is None):
        raise IngestError(f"配置 {assembly.name} 的构建设置已改变，现有索引已过期；"
                          f"请运行 ingest --config {assembly.name} --force 全量重建")
    if force and file is None:
        return _rebuild_all(workspace, assembly, sources, progress)

    index_dir = workspace.index_dir(assembly.name)
    manifest = status.manifest or _new_manifest(assembly)
    store = open_store(workspace, assembly.name)
    targets = status.documents
    if file is not None:
        chosen = select(file, sources)
        targets = tuple(item for item in status.documents if item.source == chosen)
    outcomes: list[DocumentOutcome] = []
    for item in targets:
        if item.state == "missing":
            if prune:
                assert item.entry is not None
                store.delete_document(item.entry.document_id)
                shutil.rmtree(index_dir / "documents" / item.entry.folder, ignore_errors=True)
                shutil.rmtree(workspace.ingest_reports(assembly.name) / item.entry.folder,
                              ignore_errors=True)
                del manifest.documents[item.entry.document_id]
                manifest.save(index_dir / "manifest.json")
                outcomes.append(DocumentOutcome(item.relative_path, "pruned"))
            continue
        if item.state == "unreadable":
            outcomes.append(DocumentOutcome(item.relative_path, "unreadable"))
            continue
        if not (item.needs_ingest or force):
            outcomes.append(DocumentOutcome(item.relative_path, "skipped"))
            continue
        assert item.source is not None
        progress(f"处理 {item.relative_path} …")
        try:
            prepared = prepare(item.source, assembly)
            if item.entry is not None and item.entry.folder != prepared.entry.folder:
                shutil.rmtree(index_dir / "documents" / item.entry.folder, ignore_errors=True)
            write_audit_files(index_dir / "documents" / prepared.entry.folder, prepared)
            store.replace_document(item.source.document_id, _stored(prepared), prepared.vectors)
            manifest.documents[item.source.document_id] = prepared.entry
            manifest.save(index_dir / "manifest.json")
        except Exception as exc:  # one bad PDF must not stop the others
            outcomes.append(DocumentOutcome(item.relative_path, "failed",
                                            error=f"{type(exc).__name__}: {exc}"))
            continue
        _write_reports(workspace, assembly, prepared)
        action: Action = "added" if item.entry is None else "updated"
        outcomes.append(DocumentOutcome(item.relative_path, action, len(prepared.batch.chunks)))
    return outcomes


def _rebuild_all(workspace: Workspace, assembly: Assembly,
                 sources: tuple[SourceDocument, ...],
                 progress: Callable[[str], None]) -> list[DocumentOutcome]:
    index_dir = workspace.index_dir(assembly.name)
    staging = index_dir / "documents.staging"
    shutil.rmtree(staging, ignore_errors=True)
    prepared_all: list[Prepared] = []
    outcomes: list[DocumentOutcome] = []
    from rag.ingest.sources import has_text
    for source in sources:
        if not has_text(source):
            outcomes.append(DocumentOutcome(source.relative_path, "unreadable"))
            continue
        progress(f"处理 {source.relative_path} …")
        try:
            prepared = prepare(source, assembly)
            write_audit_files(staging / prepared.entry.folder, prepared)
        except Exception as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise IngestError(f"全量重建中止，现有索引未改动：{source.relative_path} 处理失败"
                              f"（{type(exc).__name__}: {exc}）") from exc
        prepared_all.append(prepared)

    # Every document succeeded: now replace the index contents.
    store = open_store(workspace, assembly.name)
    keep = {item.source.document_id for item in prepared_all}
    for document_id in store.ids_by_document():
        if document_id not in keep:
            store.delete_document(document_id)
    manifest = _new_manifest(assembly)
    for prepared in prepared_all:
        store.replace_document(prepared.source.document_id, _stored(prepared), prepared.vectors)
        manifest.documents[prepared.source.document_id] = prepared.entry
    old = index_dir / "documents.old"
    shutil.rmtree(old, ignore_errors=True)
    if (index_dir / "documents").exists():
        (index_dir / "documents").rename(old)
    staging.rename(index_dir / "documents")
    manifest.save(index_dir / "manifest.json")
    shutil.rmtree(old, ignore_errors=True)

    shutil.rmtree(workspace.ingest_reports(assembly.name), ignore_errors=True)
    for prepared in prepared_all:
        _write_reports(workspace, assembly, prepared)
        outcomes.append(DocumentOutcome(prepared.source.relative_path, "rebuilt",
                                        len(prepared.batch.chunks)))
    return sorted(outcomes, key=lambda item: item.relative_path.casefold())
