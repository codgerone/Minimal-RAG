"""Ingest: turn PDFs into chunks + vectors + audit files, keeping the index consistent.

Consistency rule: manifest.json is written last. If anything fails before that, the
manifest still describes the old state and `status` reports the document as needing
work; re-running ingest repairs it. A full rebuild (`--force`, or a change of the encoder,
whose vectors cannot share a store with the old ones) processes every PDF first and only
touches the index once all of them succeeded.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import Callable, Literal

from rag.index.audit import load_audit, write_audit
from rag.index.build_steps import DocumentFacts, fingerprint, step_settings
from rag.index.lexical import LexicalChunk, LexicalStore
from rag.index.manifest import DocumentEntry, Manifest, now
from rag.index.status import IndexStatus, check_status
from rag.index.step_cache import StepCache
from rag.index.store import ChromaStore, StoredChunk
from rag.index.tables import StoredTable, TableStore, collect_tables
from rag.ingest.regions import attach_regions
from rag.ingest.pipeline import ProcessedDocument, process_document
from rag.ingest.sources import discover, select
from rag.ingest.timing import StepTimes
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
    lexical: list[LexicalChunk] | None     # None when the assembly has no BM25 index
    tables: list[StoredTable]
    entry: DocumentEntry


def open_store(workspace: Workspace, config_name: str) -> ChromaStore:
    return ChromaStore(workspace.index_dir(config_name) / "chroma")


def open_lexical(workspace: Workspace, assembly: Assembly) -> LexicalStore | None:
    if assembly.lexical is None:
        return None
    return LexicalStore(workspace.index_dir(assembly.name) / "bm25.sqlite")


def open_tables(workspace: Workspace, config_name: str) -> TableStore:
    return TableStore(workspace.index_dir(config_name) / "tables.sqlite")


def current_status(workspace: Workspace, assembly: Assembly,
                   sources: tuple[SourceDocument, ...] | None = None) -> IndexStatus:
    sources = discover(workspace.documents) if sources is None else sources
    return check_status(workspace, assembly.name, lambda facts: step_settings(assembly, facts), sources,
                        open_store(workspace, assembly.name), open_lexical(workspace, assembly),
                        open_tables(workspace, assembly.name), ocr=assembly.ocr is not None)


def prepare(source: SourceDocument, assembly: Assembly,
            progress: Callable[[str], None] = lambda message: None,
            cache: StepCache | None = None) -> Prepared:
    """`cache` keeps the parser's raw output, each table extractor's report and the passage vectors."""
    times = StepTimes()
    first_lookup = len(cache.log) if cache is not None else 0
    processed = process_document(source, assembly.parser, assembly.extractors, assembly.table_steps,
                                 assembly.ocr, progress, times, cache)
    with times.step("chunk"):
        batch = attach_regions(assembly.chunker.chunk(processed.document), processed.document)
    texts = [chunk.text for chunk in batch.chunks]
    with times.step("embed"):
        if cache is None:
            vectors = assembly.embedder.encode_passages(texts)
        else:
            vectors, hits = cache.embeddings().encode_passages(assembly.embedder, texts)
            times.cache["embed"] = f"{hits}/{len(texts)}"
    if cache is not None:
        for step, hit in cache.log[first_lookup:]:
            times.cache[step] = "hit" if hit else "miss"
    lexical = None
    if assembly.lexical is not None:
        language = processed.document.language
        with times.step("lexical"):
            lexical = [LexicalChunk(chunk.chunk_id, language, assembly.lexical.terms(chunk.text, language))
                       for chunk in batch.chunks]
    facts = DocumentFacts(any(page.kind == "scanned" for page in processed.pages.pages),
                          len(processed.prepared_tables) > 0)
    record = step_settings(assembly, facts)
    entry = DocumentEntry(source.document_id, source.document_name, source.relative_path,
                          source.file_hash, readable_name(source.relative_path),
                          processed.parse.primary.page_count, len(batch.chunks),
                          len(processed.prepared_tables), now(), times.rounded(),
                          times.cache, facts.scanned_pages, record, fingerprint(record))
    tables = collect_tables(processed.document, batch)
    if len(tables) != entry.table_count:
        raise IngestError(f"整表数 {len(tables)} 与表格数 {entry.table_count} 不一致")
    return Prepared(source, processed, batch, vectors, lexical, tables, entry)


def _stored(prepared: Prepared) -> list[StoredChunk]:
    source = prepared.source
    return [StoredChunk(chunk.chunk_id, source.document_id, source.document_name,
                        source.relative_path, chunk.chunk_index, chunk.kind,
                        tuple(dict.fromkeys(span.page_number for item in chunk.sources
                                            for span in item.page_spans)),
                        chunk.token_count, chunk.text)
            for chunk in prepared.batch.chunks]


def _write_reports(workspace: Workspace, config_name: str, entry: DocumentEntry) -> None:
    from rag.reports.ingest import write_document_reports
    audit = load_audit(workspace.index_dir(config_name) / "documents" / entry.folder)
    write_document_reports(workspace, config_name, audit, entry)


def _new_manifest(assembly: Assembly) -> Manifest:
    return Manifest(assembly.name, now())


def _encoder_changed(status: IndexStatus) -> bool:
    """A document built with another encoder (or before records were kept per document): its vectors
    may have another dimension, so the store is rebuilt as a whole."""
    return any(item.state == "stale" and (not item.changed_steps or "embed" in item.changed_steps)
               for item in status.documents)


def ingest(workspace: Workspace, assembly: Assembly, *, file: str | None = None,
           force: bool = False, prune: bool = False, refresh_cache: bool = False,
           progress: Callable[[str], None] = lambda message: None) -> list[DocumentOutcome]:
    """`refresh_cache` recomputes the parse, table extraction and passage vectors of every document
    processed, overwriting their cache entries (OCR facts and LLM results are kept)."""
    cache = StepCache(workspace.cache_dir, refresh_cache)
    sources = discover(workspace.documents)
    status = current_status(workspace, assembly, sources)
    if _encoder_changed(status):
        if file is not None:
            raise IngestError(f"配置 {assembly.name} 的编码设置已改变（或索引是 V4.1.1 之前建的），"
                              f"向量库要整体重建；请不加 --file 运行 ingest --config {assembly.name}")
        return _rebuild_all(workspace, assembly, sources, progress, cache)
    if force and file is None:
        return _rebuild_all(workspace, assembly, sources, progress, cache)

    index_dir = workspace.index_dir(assembly.name)
    manifest = status.manifest or _new_manifest(assembly)
    store = open_store(workspace, assembly.name)
    lexical = open_lexical(workspace, assembly)
    tables = open_tables(workspace, assembly.name)
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
                if lexical is not None:
                    lexical.delete_document(item.entry.document_id)
                tables.delete_document(item.entry.document_id)
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
        reason = f"（{item.stale_reason}）" if item.state == "stale" else ""
        progress(f"处理 {item.relative_path}{reason} …")
        try:
            prepared = prepare(item.source, assembly, progress, cache=cache)
            if item.entry is not None and item.entry.folder != prepared.entry.folder:
                shutil.rmtree(index_dir / "documents" / item.entry.folder, ignore_errors=True)
            write_audit(index_dir / "documents" / prepared.entry.folder, prepared.processed, prepared.batch)
            store.replace_document(item.source.document_id, _stored(prepared), prepared.vectors)
            if lexical is not None:
                lexical.replace_document(item.source.document_id, prepared.lexical or [])
            tables.replace_document(item.source.document_id, prepared.tables)
            manifest.documents[item.source.document_id] = prepared.entry
            manifest.save(index_dir / "manifest.json")
        except Exception as exc:  # one bad PDF must not stop the others
            outcomes.append(DocumentOutcome(item.relative_path, "failed",
                                            error=f"{type(exc).__name__}: {exc}"))
            continue
        _write_reports(workspace, assembly.name, prepared.entry)
        action: Action = "added" if item.entry is None else "updated"
        outcomes.append(DocumentOutcome(item.relative_path, action, len(prepared.batch.chunks)))
    return outcomes


def _rebuild_all(workspace: Workspace, assembly: Assembly,
                 sources: tuple[SourceDocument, ...],
                 progress: Callable[[str], None], cache: StepCache) -> list[DocumentOutcome]:
    index_dir = workspace.index_dir(assembly.name)
    staging = index_dir / "documents.staging"
    shutil.rmtree(staging, ignore_errors=True)
    prepared_all: list[Prepared] = []
    outcomes: list[DocumentOutcome] = []
    from rag.ingest.sources import has_text
    for source in sources:
        if not has_text(source, ocr=assembly.ocr is not None):
            outcomes.append(DocumentOutcome(source.relative_path, "unreadable"))
            continue
        progress(f"处理 {source.relative_path} …")
        try:
            prepared = prepare(source, assembly, progress, cache=cache)
            write_audit(staging / prepared.entry.folder, prepared.processed, prepared.batch)
        except Exception as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise IngestError(f"全量重建中止，现有索引未改动：{source.relative_path} 处理失败"
                              f"（{type(exc).__name__}: {exc}）") from exc
        prepared_all.append(prepared)

    # Every document succeeded: now replace the index contents.
    store = open_store(workspace, assembly.name)
    store.clear()
    lexical = open_lexical(workspace, assembly)
    if lexical is not None:
        lexical.clear()
    tables = open_tables(workspace, assembly.name)
    tables.clear()
    manifest = _new_manifest(assembly)
    for prepared in prepared_all:
        store.replace_document(prepared.source.document_id, _stored(prepared), prepared.vectors)
        if lexical is not None:
            lexical.replace_document(prepared.source.document_id, prepared.lexical or [])
        tables.replace_document(prepared.source.document_id, prepared.tables)
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
        _write_reports(workspace, assembly.name, prepared.entry)
        outcomes.append(DocumentOutcome(prepared.source.relative_path, "rebuilt",
                                        len(prepared.batch.chunks)))
    return sorted(outcomes, key=lambda item: item.relative_path.casefold())
