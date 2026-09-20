"""Offline indexing orchestration and multi-document lifecycle management."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import json
from uuid import uuid4
from typing import Callable

from rag.config import Settings
from rag.document_processor import V1DocumentProcessor
from rag.document_registry import discover_documents, resolve_document_selector
from rag.embeddings import E5Embedder
from rag.errors import DocumentDirectoryError, ManifestError, RagError, VectorStoreError
from rag.manifest import (
    global_config_matches,
    load_manifest,
    make_empty_manifest,
    save_manifest_atomic,
    validate_index,
)
from rag.models import (
    BuiltDocument,
    DocumentBuildResult,
    DocumentState,
    IngestDocumentResult,
    IngestSummary,
    Manifest,
    ManifestDocument,
    SourceDocument,
)
from rag.pipeline_registry import DocumentProcessor
from rag.vector_store import ChromaVectorStore


def utc_now() -> datetime:
    return datetime.now(UTC)


def _format_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class Indexer:
    def __init__(
        self,
        settings: Settings,
        embedder: E5Embedder,
        vector_store: ChromaVectorStore,
        clock: Callable[[], datetime] = utc_now,
        document_processor: DocumentProcessor | None = None,
    ) -> None:
        self.settings = settings
        self.embedder = embedder
        self.vector_store = vector_store
        self.clock = clock
        self.document_processor = document_processor or V1DocumentProcessor(
            settings.chunk_size, settings.chunk_overlap
        )

    def _new_build_id(self, source: SourceDocument) -> str:
        config_payload = json.dumps(
            {
                "embedding_model": self.settings.embedding_model,
                "chunk_size": self.settings.chunk_size,
                "chunk_overlap": self.settings.chunk_overlap,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        config_hash = hashlib.sha256(config_payload).hexdigest()
        timestamp = self.clock().astimezone(UTC).strftime("%Y%m%d%H%M%S")
        return f"{timestamp}-{source.file_hash[:12]}-{config_hash[:12]}-{uuid4().hex[:8]}"

    def build_document_payload(self, source: SourceDocument) -> BuiltDocument:
        result = self.document_processor.process(source, self._new_build_id(source))
        chunks = result.chunks
        embeddings = self.embedder.embed_passages([chunk.text for chunk in chunks])
        if len(chunks) != len(embeddings):
            raise ManifestError(
                f"文档 {source.relative_path} 的 chunk 与 embedding 数量不一致。"
            )
        dimensions = {len(vector) for vector in embeddings}
        if len(dimensions) != 1 or 0 in dimensions:
            raise ManifestError(
                f"文档 {source.relative_path} 的 embedding 维度不一致或为空。"
            )
        if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
            raise ManifestError(f"文档 {source.relative_path} 生成了重复 Chunk ID。")
        return BuiltDocument(
            result=result,
            embeddings=tuple(tuple(vector) for vector in embeddings),
        )

    def _load_compatible_manifest(self) -> Manifest:
        manifest = load_manifest(self.settings.manifest_path)
        if manifest is None:
            return make_empty_manifest(self.settings)
        if not global_config_matches(manifest, self.settings):
            raise ManifestError(
                "当前 Embedding 模型或分块配置与已有索引不一致。",
                "请执行 python -m rag ingest --force。",
            )
        return manifest

    def _record_success(
        self, manifest: Manifest, built: BuiltDocument
    ) -> Manifest:
        timestamp = _format_time(self.clock())
        source = built.result.source
        documents = dict(manifest.documents)
        documents[source.document_id] = ManifestDocument(
            relative_path=source.relative_path,
            document_name=source.document_name,
            file_hash=source.file_hash,
            page_count=built.result.stats.page_count,
            character_count=built.result.stats.character_count,
            chunk_count=built.result.stats.chunk_count,
            indexed_at=timestamp,
        )
        return replace(manifest, documents=documents, updated_at=timestamp)

    def _replace_one(
        self, manifest: Manifest, built: BuiltDocument
    ) -> Manifest:
        document_id = built.result.source.document_id
        self.vector_store.delete_document(document_id)
        self.vector_store.add_chunks(built.result.chunks, built.embeddings)
        if self.vector_store.count_document(document_id) != len(built.result.chunks):
            raise VectorStoreError(
                f"写入后文档计数校验失败：{built.result.source.relative_path}"
            )
        updated = self._record_success(manifest, built)
        save_manifest_atomic(self.settings.manifest_path, updated)
        return updated

    def ingest_all(self, force: bool = False, prune: bool = False) -> IngestSummary:
        sources = discover_documents(self.settings.documents_dir)
        if not sources:
            raise DocumentDirectoryError(
                f"目录中没有 PDF：{self.settings.documents_dir}",
                "请将 PDF 放入 documents/ 后重试。",
            )
        if force:
            return self._force_all(sources)

        manifest = self._load_compatible_manifest()
        health = validate_index(self.settings, sources, manifest, self.vector_store)
        states = {
            status.document_id: status.state for status in health.document_statuses
        }
        added = updated = skipped = failed = pruned = 0
        results: list[IngestDocumentResult] = []

        for source in sources:
            state = states.get(source.document_id, DocumentState.NEW)
            if state == DocumentState.CURRENT:
                skipped += 1
                results.append(IngestDocumentResult(source.relative_path, "skipped"))
                continue
            try:
                built = self.build_document_payload(source)
                existed = source.document_id in manifest.documents
                manifest = self._replace_one(manifest, built)
                if existed:
                    updated += 1
                    result_state = "updated"
                else:
                    added += 1
                    result_state = "added"
                results.append(IngestDocumentResult(source.relative_path, result_state))
            except RagError as exc:
                failed += 1
                results.append(
                    IngestDocumentResult(
                        source.relative_path, "failed", exc.user_message()
                    )
                )

        source_ids = {source.document_id for source in sources}
        missing_ids = [
            document_id
            for document_id in manifest.documents
            if document_id not in source_ids
        ]
        if prune:
            for document_id in missing_ids:
                recorded = manifest.documents[document_id]
                try:
                    self.vector_store.delete_document(document_id)
                    documents = dict(manifest.documents)
                    del documents[document_id]
                    timestamp = _format_time(self.clock())
                    manifest = replace(
                        manifest, documents=documents, updated_at=timestamp
                    )
                    save_manifest_atomic(self.settings.manifest_path, manifest)
                    pruned += 1
                    results.append(
                        IngestDocumentResult(recorded.relative_path, "pruned")
                    )
                except RagError as exc:
                    failed += 1
                    results.append(
                        IngestDocumentResult(
                            recorded.relative_path, "failed", exc.user_message()
                        )
                    )

        return IngestSummary(
            scanned=len(sources),
            added=added,
            updated=updated,
            skipped=skipped,
            failed=failed,
            missing=0 if prune else len(missing_ids),
            pruned=pruned,
            collection_count=self.vector_store.count_all(),
            results=tuple(results),
        )

    def ingest_one(self, selector: str, force: bool = False) -> IngestSummary:
        sources = discover_documents(self.settings.documents_dir)
        source = resolve_document_selector(selector, sources)
        manifest = self._load_compatible_manifest()
        recorded = manifest.documents.get(source.document_id)
        if (
            not force
            and recorded is not None
            and recorded.file_hash == source.file_hash
            and self.vector_store.count_document(source.document_id)
            == recorded.chunk_count
        ):
            return IngestSummary(
                1,
                0,
                0,
                1,
                0,
                0,
                0,
                self.vector_store.count_all(),
                (IngestDocumentResult(source.relative_path, "skipped"),),
            )
        built = self.build_document_payload(source)
        self._replace_one(manifest, built)
        existed = recorded is not None
        return IngestSummary(
            1,
            0 if existed else 1,
            1 if existed else 0,
            0,
            0,
            0,
            0,
            self.vector_store.count_all(),
            (
                IngestDocumentResult(
                    source.relative_path, "updated" if existed else "added"
                ),
            ),
        )

    def _force_all(self, sources: list[SourceDocument]) -> IngestSummary:
        built_documents: list[BuiltDocument] = []
        failures: list[IngestDocumentResult] = []
        for source in sources:
            try:
                built_documents.append(self.build_document_payload(source))
            except RagError as exc:
                failures.append(
                    IngestDocumentResult(
                        source.relative_path, "failed", exc.user_message()
                    )
                )
        if failures:
            return IngestSummary(
                len(sources),
                0,
                0,
                0,
                len(failures),
                0,
                0,
                self.vector_store.count_all(),
                tuple(failures),
            )

        self.vector_store.recreate_collection()
        manifest = make_empty_manifest(self.settings)
        for built in built_documents:
            self.vector_store.add_chunks(built.result.chunks, built.embeddings)
            if self.vector_store.count_document(built.result.source.document_id) != len(
                built.result.chunks
            ):
                raise VectorStoreError(
                    f"全量重建计数校验失败：{built.result.source.relative_path}"
                )
            manifest = self._record_success(manifest, built)
        expected = sum(item.chunk_count for item in manifest.documents.values())
        actual = self.vector_store.count_all()
        if actual != expected:
            raise VectorStoreError(
                f"全量重建总计数校验失败：实际 {actual}，预期 {expected}。"
            )
        save_manifest_atomic(self.settings.manifest_path, manifest)
        return IngestSummary(
            len(sources),
            len(sources),
            0,
            0,
            0,
            0,
            0,
            actual,
            tuple(
                IngestDocumentResult(item.result.source.relative_path, "rebuilt")
                for item in built_documents
            ),
        )
