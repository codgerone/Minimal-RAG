"""Health-gated semantic retrieval with complete boundary tie expansion."""

from __future__ import annotations

import math
from typing import Protocol

from rag.v3.contracts.documents import SourceDocument
from rag.v3.contracts.retrieval import (
    EmbeddingIdentity, QueryEmbedding, RetrievalHit, RetrievalRequest, RetrievalResult,
)
from rag.v3.contracts.runtime import HealthReport
from rag.v3.contracts.storage import IndexIdentity, VectorQueryHit


class RetrievalError(ValueError):
    pass


class HealthPort(Protocol):
    def check(self, index: IndexIdentity,
              sources: tuple[SourceDocument, ...]) -> HealthReport: ...


class QueryEmbedderPort(Protocol):
    def encode_query(self, request: RetrievalRequest) -> QueryEmbedding: ...


class VectorReaderPort(Protocol):
    def count(self, index: IndexIdentity, document_id: str | None) -> int: ...

    def query(self, index: IndexIdentity, vector: tuple[float, ...], k: int,
              document_id: str | None) -> tuple[VectorQueryHit, ...]: ...


class ReadGatePort(Protocol):
    def begin(self, index: IndexIdentity) -> str: ...

    def finish(self, index: IndexIdentity, original_digest: str) -> None: ...


class StableSemanticRetriever:
    def __init__(self, health: HealthPort, embedder: QueryEmbedderPort,
                 vectors: VectorReaderPort, sources: tuple[SourceDocument, ...],
                 read_gate: ReadGatePort, embedding_identity: EmbeddingIdentity):
        self.health = health
        self.embedder = embedder
        self.vectors = vectors
        self.sources = sources
        self.read_gate = read_gate
        self.embedding_identity = embedding_identity

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        digest = self.read_gate.begin(request.index_identity)
        report = self.health.check(request.index_identity, self.sources)
        if not report.usable or report.index_identity != request.index_identity:
            raise RetrievalError("index is not ready")
        count = self.vectors.count(request.index_identity, request.document_id)
        if type(count) is not int or count < 0:
            raise RetrievalError("vector scope count is invalid")
        if count == 0:
            self.read_gate.finish(request.index_identity, digest)
            return RetrievalResult(request, (), report.index_identity)
        query = self.embedder.encode_query(request)
        if (query.index_identity != request.index_identity
                or query.embedding_identity != self.embedding_identity):
            raise RetrievalError("query embedding index identity mismatch")
        fetch = min(count, max(request.top_k + 1, 2 * request.top_k))
        candidates: tuple[VectorQueryHit, ...] = ()
        while True:
            candidates = self.vectors.query(request.index_identity, query.vector,
                                            fetch, request.document_id)
            if len(candidates) != fetch:
                raise RetrievalError("vector query did not return the requested scope")
            ids = [item.record.chunk_id for item in candidates]
            if len(ids) != len(set(ids)) or any(not math.isfinite(item.distance)
                                               for item in candidates):
                raise RetrievalError("duplicate or nonfinite vector query result")
            for item in candidates:
                record = item.record
                if (record.metadata.build_fingerprint != request.index_identity.build_fingerprint or
                        record.document_id != record.metadata.document_id or
                        request.document_id is not None and record.document_id != request.document_id):
                    raise RetrievalError("vector hit identity mismatch")
            ordered = tuple(sorted(candidates, key=lambda item: (item.distance, item.record.chunk_id)))
            if fetch == count or len(ordered) < request.top_k:
                candidates = ordered
                break
            boundary = ordered[request.top_k - 1].distance
            if ordered[-1].distance > boundary:
                candidates = ordered
                break
            fetch = min(count, 2 * fetch)
        hits = tuple(RetrievalHit(
            item.record.chunk_id, item.record.document_id,
            item.record.metadata.document_name, item.record.metadata.relative_path,
            item.record.text, item.record.metadata.chunk_index, item.record.metadata.kind,
            item.record.metadata.sources, item.distance,
        ) for item in candidates[:request.top_k])
        self.read_gate.finish(request.index_identity, digest)
        return RetrievalResult(request, hits, report.index_identity)
