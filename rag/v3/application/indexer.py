"""V3 ingest orchestration over validated processor, chunker and store ports."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Protocol

from rag.v3.application.artifact_paths import build_artifact_path
from rag.v3.application.vector_records import make_vector_records
from rag.v3.application.health import IndexHealth
from rag.v3.application.publication import Publisher, Recovery
from rag.v3.contracts.assembly import BuildProjection
from rag.v3.contracts.documents import ChunkBatch, ParsedDocument, SourceDocument
from rag.v3.contracts.processing import (
    ChunkingContext, DocumentRequest, IndexerInput, IngestDocumentResult,
    IngestResult,
)
from rag.v3.contracts.retrieval import PassageEmbeddingBatch
from rag.v3.contracts.runtime import OperationError
from rag.v3.contracts.storage import (
    IndexIdentity, Manifest, ManifestDocumentRecord, PublicationRequest, VectorRecord,
)


class Chunker(Protocol):
    def chunk(self, document: ParsedDocument, context: ChunkingContext) -> ChunkBatch: ...


class PassageEmbedder(Protocol):
    def encode_passages(self, batch: ChunkBatch, build_id: str,
                        index: IndexIdentity, **kwargs: object) -> PassageEmbeddingBatch: ...


class ArtifactBuilder(Protocol):
    def stage(self, processing: object, batch: ChunkBatch, projection: BuildProjection,
              transaction_id: str): ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_id(source: SourceDocument, index: IndexIdentity) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{stamp}-{source.file_hash[:12]}-{index.build_fingerprint[:12]}-{uuid.uuid4().hex[:8]}"


def _operation_error(exc: Exception, stage: str,
                     document_id: str | None) -> OperationError:
    codes = {
        "main_parsing": "main_parser_failed", "chunking": "chunking_failed",
        "artifact_staging": "artifact_failed", "embedding": "embedding_failed",
        "publication": "publication_failed", "recovery": "recovery_failed",
    }
    code = codes[stage]
    scope = "transaction" if stage in {"publication", "recovery"} else (
        "artifact" if stage == "artifact_staging" else "document")
    return OperationError(code, stage, scope, document_id if scope in {"artifact", "document"}
                          else None, False, str(exc) or code, type(exc).__name__)


class Indexer:
    def __init__(self, configuration_name: str, index: IndexIdentity,
                 projection: BuildProjection, context: ChunkingContext,
                 table_branch_attached: bool, processor: object, chunker: Chunker,
                 embedder: PassageEmbedder, artifact_builder: ArtifactBuilder,
                 publisher: Publisher, health: IndexHealth, recovery: Recovery):
        self.configuration_name = configuration_name
        self.index = index
        self.projection = projection
        self.context = context
        self.table_branch_attached = table_branch_attached
        self.processor = processor
        self.chunker = chunker
        self.embedder = embedder
        self.artifact_builder = artifact_builder
        self.publisher = publisher
        self.health = health
        self.recovery = recovery

    def _source_error(self, source: SourceDocument) -> OperationError | None:
        probe = self.health.source_probe.probe(source)
        if probe.status == "processable":
            return None
        return OperationError(probe.reason, "source_probe", "document",
                              source.document_id, False,
                              "source is not processable", None)

    def _prepare(self, source: SourceDocument, transaction_id: str):
        build_id = _build_id(source, self.index)
        stage = "main_parsing"
        try:
            processing = self.processor.process(DocumentRequest(
                source, self.index, build_id, self.table_branch_attached))
            stage = "chunking"
            context = replace(self.context, build_id=build_id)
            batch = self.chunker.chunk(processing.parsed_document, context)
            stage = "artifact_staging"
            staged = self.artifact_builder.stage(processing, batch, self.projection,
                                                 transaction_id)
            stage = "embedding"
            options = ({"counter": getattr(self.chunker, "counter", None),
                        "maximum_input_tokens": context.maximum_input_tokens}
                       if context.maximum_input_tokens is not None else {})
            embedded = self.embedder.encode_passages(batch, build_id, self.index, **options)
            records = make_vector_records(source, batch, embedded)
            now = _now()
            provisional = ManifestDocumentRecord(
                source.document_id, source.document_name, source.relative_path,
                source.file_hash, build_id, processing.primary_document.page_count,
                batch.character_count, len(batch.chunks), "pending",
                {**{item.role: item.sha256 for item in staged.files},
                 "snapshot_manifest": staged.snapshot_manifest_sha256}, now,
            )
            entry = replace(provisional, artifact_path=build_artifact_path(provisional))
            return staged, records, entry
        except Exception as exc:
            try:
                self.publisher.artifacts.cleanup_staging(self.index, transaction_id)
            except Exception:
                pass
            raise IndexBuildError(_operation_error(exc, stage, source.document_id)) from exc

    def _manifest(self, old: Manifest | None,
                  documents: dict[str, ManifestDocumentRecord]) -> Manifest:
        return Manifest("index_manifest_v3", self.index, self.projection,
                        self.context.embedding_identity, documents,
                        old.created_at if old is not None else _now(), _now())

    def ingest(self, request: IndexerInput) -> IngestResult:
        if request.configuration_name != self.configuration_name or request.index_identity != self.index:
            raise ValueError("indexer request differs from validated binding")
        pending = self.publisher.recovery.list_pending(self.index)
        for journal in pending:
            result = self.recovery.recover(self.index, journal.transaction_id)
            if result.outcome == "failed":
                raise RuntimeError("pending index recovery could not be verified")
        sources = tuple(item for item in request.sources if request.selected_document_id is None
                        or item.document_id == request.selected_document_id)
        health = self.health.check(self.index, request.sources)
        issue_codes = {item.code for item in health.issues}
        rebuild_collection = request.force and request.selected_document_id is None
        if issue_codes & {"pending_recovery", "recovery_failed"}:
            raise RuntimeError("index health blocks ingest")
        if issue_codes & {"manifest_invalid", "configuration_mismatch"} and not rebuild_collection:
            raise RuntimeError("index structure requires full force")
        old = (None if "manifest_invalid" in issue_codes
               else self.publisher.manifest.load(self.index))
        if "collection_missing_with_manifest" in issue_codes:
            if not set(old.documents).issubset({item.document_id for item in request.sources}):
                raise RuntimeError("missing old sources require explicit prune before collection repair")
            rebuild_collection = True
        if issue_codes & {"manifest_missing_with_collection", "unknown_vector_document",
                          "collection_manifest_count_mismatch"} and not rebuild_collection:
            raise RuntimeError("index structure requires full force")
        if rebuild_collection:
            if old is not None and set(old.documents) - {item.document_id for item in sources}:
                raise RuntimeError("missing registered sources require explicit prune before full force")
            return self._force_all(request, sources, old)
        return self._incremental(request, sources, old, health)

    def _force_all(self, request: IndexerInput, sources: tuple[SourceDocument, ...],
                   old: Manifest | None) -> IngestResult:
        previous_document_ids = (set(old.documents) if old is not None else
                                 {item.document_id for item in
                                  self.publisher.vector.list_records(self.index)})
        transaction_id = uuid.uuid4().hex
        prepared = []
        results = []
        failed = False
        for source in sources:
            source_error = self._source_error(source)
            if source_error is not None:
                failed = True
                results.append(IngestDocumentResult(source.document_id, source.relative_path,
                                                    "failed", source_error, None))
                continue
            try:
                prepared.append((source, *self._prepare(source, transaction_id)))
            except IndexBuildError as exc:
                failed = True
                results.append(IngestDocumentResult(source.document_id, source.relative_path,
                                                    "failed", exc.error, None))
        if failed:
            self.publisher.artifacts.cleanup_staging(self.index, transaction_id)
            results.extend(IngestDocumentResult(item[0].document_id, item[0].relative_path,
                                                "not_published", None, None)
                           for item in prepared)
            return self._result(request, results, len(sources))
        manifest = self._manifest(old, {source.document_id: entry
                                        for source, _, _, entry in prepared})
        old_sha = self.publisher.manifest.raw_unvalidated(self.index)[1]
        publication = PublicationRequest(transaction_id, "replace_collection", self.index,
            None, tuple(record for _, _, records, _ in prepared for record in records),
            tuple(staged for _, staged, _, _ in prepared), manifest, old_sha)
        try:
            outcome = self.publisher.publish(publication)
        except Exception as exc:
            if not self.publisher.recovery.read(self.index, transaction_id):
                self.publisher.artifacts.cleanup_staging(self.index, transaction_id)
            error = _operation_error(exc, "publication", None)
            results.extend(IngestDocumentResult(source.document_id, source.relative_path,
                                                "failed", error, transaction_id)
                           for source, _, _, _ in prepared)
            return self._result(request, results, len(sources))
        for source, _, _, _ in prepared:
            if outcome.outcome == "committed":
                state = "updated" if source.document_id in previous_document_ids else "added"
                results.append(IngestDocumentResult(source.document_id, source.relative_path,
                                                    state, None, transaction_id))
            else:
                error = _operation_error(RuntimeError(outcome.outcome), "publication", None)
                results.append(IngestDocumentResult(source.document_id, source.relative_path,
                                                    "failed", error, transaction_id))
        return self._result(request, results, len(sources))

    def _incremental(self, request: IndexerInput, sources: tuple[SourceDocument, ...],
                     old: Manifest | None, health) -> IngestResult:
        statuses = {item.document_id: item for item in health.document_statuses}
        results = []
        for source in sources:
            status = statuses[source.document_id]
            if status.state == "current" and not request.force:
                results.append(IngestDocumentResult(source.document_id, source.relative_path,
                                                    "skipped", None, None))
                continue
            if status.state == "unprocessable":
                error = self._source_error(source)
                assert error is not None
                results.append(IngestDocumentResult(source.document_id, source.relative_path,
                                                    "failed", error, None))
                continue
            transaction_id = uuid.uuid4().hex
            try:
                staged, records, entry = self._prepare(source, transaction_id)
                active = self.publisher.manifest.load(self.index)
                documents = dict(active.documents) if active is not None else {}
                state = "updated" if source.document_id in documents else "added"
                documents[source.document_id] = entry
                publication = PublicationRequest(transaction_id, "replace_document",
                    self.index, source.document_id, records, (staged,),
                    self._manifest(active, documents), self.publisher.manifest.raw(self.index)[1])
                outcome = self.publisher.publish(publication)
                if outcome.outcome != "committed":
                    raise RuntimeError(outcome.outcome)
                results.append(IngestDocumentResult(source.document_id, source.relative_path,
                                                    state, None, transaction_id))
            except IndexBuildError as exc:
                results.append(IngestDocumentResult(source.document_id, source.relative_path,
                                                    "failed", exc.error, None))
            except Exception as exc:
                if not self.publisher.recovery.read(self.index, transaction_id):
                    self.publisher.artifacts.cleanup_staging(self.index, transaction_id)
                results.append(IngestDocumentResult(source.document_id, source.relative_path,
                    "failed", _operation_error(exc, "publication", None), transaction_id))
        source_ids = {item.document_id for item in request.sources}
        if old is not None and request.selected_document_id is None:
            for entry in old.documents.values():
                if entry.document_id in source_ids:
                    continue
                if not request.prune:
                    results.append(IngestDocumentResult(entry.document_id, entry.relative_path,
                                                        "missing", None, None))
                    continue
                transaction_id = uuid.uuid4().hex
                try:
                    active = self.publisher.manifest.load(self.index)
                    documents = dict(active.documents)
                    documents.pop(entry.document_id)
                    publication = PublicationRequest(transaction_id, "prune_document",
                        self.index, entry.document_id, (), (), self._manifest(active, documents),
                        self.publisher.manifest.raw(self.index)[1])
                    outcome = self.publisher.publish(publication)
                    if outcome.outcome != "committed":
                        raise RuntimeError(outcome.outcome)
                    results.append(IngestDocumentResult(entry.document_id, entry.relative_path,
                                                        "pruned", None, transaction_id))
                except Exception as exc:
                    results.append(IngestDocumentResult(entry.document_id, entry.relative_path,
                        "failed", _operation_error(exc, "publication", None), transaction_id))
        return self._result(request, results, len(sources))

    def _result(self, request: IndexerInput, documents: list[IngestDocumentResult],
                scanned: int) -> IngestResult:
        documents.sort(key=lambda item: (item.relative_path.casefold(), item.relative_path,
                                         item.document_id))
        return IngestResult(request.configuration_name, self.index, scanned,
                            tuple(documents), self.publisher.vector.count(self.index))


class IndexBuildError(RuntimeError):
    def __init__(self, error: OperationError):
        self.error = error
        super().__init__(error.message)
