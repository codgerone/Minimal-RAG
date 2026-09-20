"""PipelineRuntime-based indexing orchestration for isolated V1/V2 publication."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from rag.document_registry import discover_documents, resolve_document_selector
from rag.errors import DocumentDirectoryError, ManifestError, RagError
from rag.models import BuiltDocument, IngestDocumentResult, IngestSummary, SourceDocument
from rag.pipeline_manifest import load_pipeline_manifest, make_empty_manifest
from rag.publication import PublicationManager


def _timestamp(clock) -> str:
    return clock().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class RuntimeIndexer:
    def __init__(self, settings, runtime, embedder, vector_store, clock=lambda: datetime.now(UTC)) -> None:
        self.settings = settings
        self.runtime = runtime
        self.embedder = embedder
        self.vector_store = vector_store
        self.clock = clock
        self.publisher = PublicationManager(project_root=settings.project_root, runtime=runtime,
                                            vector_store=vector_store, clock=clock)

    def _build_id(self, source: SourceDocument) -> str:
        return (f"{self.clock().astimezone(UTC):%Y%m%d%H%M%S}-{source.file_hash[:12]}-"
                f"{self.runtime.build_config_fingerprint[:12]}-{uuid4().hex[:8]}")

    def build_document_payload(self, source: SourceDocument) -> BuiltDocument:
        result = self.runtime.document_processor.process(source, self._build_id(source))
        if result.pipeline_id != self.runtime.pipeline_id:
            raise ManifestError("DocumentProcessor 返回了错误 pipeline。")
        embeddings = self.embedder.embed_passages([chunk.text for chunk in result.chunks])
        if len(embeddings) != len(result.chunks) or not embeddings:
            raise ManifestError("chunk 与 embedding 数量不一致或为空。")
        dimensions = {len(item) for item in embeddings}
        if dimensions != {self.runtime.build_config.embedding.vector_dimension}:
            raise ManifestError("实际 embedding 维度与 BuildConfig 不一致。")
        if len({item.chunk_id for item in result.chunks}) != len(result.chunks):
            raise ManifestError("DocumentProcessor 返回重复 chunk_id。")
        return BuiltDocument(result, tuple(tuple(float(value) for value in item) for item in embeddings))

    def _manifest(self, *, allow_incompatible: bool = False):
        manifest = load_pipeline_manifest(self.runtime.manifest_path, self.runtime)
        if manifest is None:
            return make_empty_manifest(self.runtime, _timestamp(self.clock))
        if manifest.build_config_fingerprint != self.runtime.build_config_fingerprint:
            if allow_incompatible:
                return make_empty_manifest(self.runtime, manifest.created_at)
            raise ManifestError("当前 BuildConfig 与已有索引不一致。", "请对所选 pipeline 执行 ingest --force。")
        return manifest

    def ingest_one(self, selector: str, force: bool = False) -> IngestSummary:
        self.publisher.recover_pending()
        sources = discover_documents(self.settings.documents_dir)
        source = resolve_document_selector(selector, sources)
        manifest = self._manifest(allow_incompatible=force)
        record = manifest.documents.get(source.document_id)
        if not force and record and record.file_hash == source.file_hash and \
                self.vector_store.count_document(source.document_id) == record.chunk_count:
            return IngestSummary(1, 0, 0, 1, 0, 0, 0, self.vector_store.count_all(),
                                 (IngestDocumentResult(source.relative_path, "skipped"),))
        built = self.build_document_payload(source)
        self.publisher.replace_document(manifest, built, _timestamp(self.clock))
        existed = record is not None
        return IngestSummary(1, 0 if existed else 1, int(existed), 0, 0, 0, 0,
                             self.vector_store.count_all(),
                             (IngestDocumentResult(source.relative_path, "updated" if existed else "added"),))

    def ingest_all(self, force: bool = False, prune: bool = False) -> IngestSummary:
        self.publisher.recover_pending()
        sources = discover_documents(self.settings.documents_dir)
        if not sources:
            raise DocumentDirectoryError(f"目录中没有 PDF：{self.settings.documents_dir}", "请将 PDF 放入 documents/ 后重试。")
        manifest = self._manifest(allow_incompatible=force)
        if force:
            builds, failures = [], []
            for source in sources:
                try:
                    builds.append(self.build_document_payload(source))
                except RagError as exc:
                    failures.append(IngestDocumentResult(source.relative_path, "failed", exc.user_message()))
            if failures:
                return IngestSummary(len(sources), 0, 0, 0, len(failures), 0, 0,
                                     self.vector_store.count_all(), tuple(failures))
            self.publisher.force_rebuild(manifest, tuple(builds), _timestamp(self.clock))
            return IngestSummary(len(sources), len(sources), 0, 0, 0, 0, 0,
                                 self.vector_store.count_all(),
                                 tuple(IngestDocumentResult(item.result.source.relative_path, "rebuilt") for item in builds))

        added = updated = skipped = failed = pruned = 0
        results = []
        for source in sources:
            record = manifest.documents.get(source.document_id)
            if record and record.file_hash == source.file_hash and self.vector_store.count_document(source.document_id) == record.chunk_count:
                skipped += 1
                results.append(IngestDocumentResult(source.relative_path, "skipped"))
                continue
            try:
                built = self.build_document_payload(source)
                manifest = self.publisher.replace_document(manifest, built, _timestamp(self.clock))
                if record:
                    updated += 1
                    state = "updated"
                else:
                    added += 1
                    state = "added"
                results.append(IngestDocumentResult(source.relative_path, state))
            except RagError as exc:
                failed += 1
                results.append(IngestDocumentResult(source.relative_path, "failed", exc.user_message()))
        source_ids = {item.document_id for item in sources}
        missing = [item for item in manifest.documents if item not in source_ids]
        if prune:
            for document_id in missing:
                try:
                    record = manifest.documents[document_id]
                    prune_id = f"prune-{self.clock().astimezone(UTC):%Y%m%d%H%M%S}-{uuid4().hex[:8]}"
                    manifest = self.publisher.prune_document(manifest, document_id, prune_id, _timestamp(self.clock))
                    pruned += 1
                    results.append(IngestDocumentResult(record.relative_path, "pruned"))
                except RagError as exc:
                    failed += 1
                    results.append(IngestDocumentResult(manifest.documents[document_id].relative_path, "failed", exc.user_message()))
        return IngestSummary(len(sources), added, updated, skipped, failed,
                             0 if prune else len(missing), pruned, self.vector_store.count_all(), tuple(results))
