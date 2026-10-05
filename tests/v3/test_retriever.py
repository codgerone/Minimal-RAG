"""Stable K-boundary retrieval must include every tied candidate."""

from __future__ import annotations

import hashlib

import pytest

from rag.v3.adapters.e5 import default_identity
from rag.v3.application.assembly import builtin_configuration, index_identity
from rag.v3.application.retriever import RetrievalError, StableSemanticRetriever
from rag.v3.contracts.documents import ChunkSource, PageSpan
from rag.v3.contracts.retrieval import QueryEmbedding, RetrievalRequest
from rag.v3.contracts.runtime import HealthReport
from rag.v3.contracts.storage import ChunkMetadata, VectorQueryHit, VectorRecord


class Health:
    def __init__(self, usable=True):
        self.usable = usable

    def check(self, index, sources):
        return HealthReport(self.usable, index, (), (), "2026-09-25T00:00:00Z", True)


class Encoder:
    def __init__(self):
        self.calls = 0

    def encode_query(self, request):
        self.calls += 1
        return QueryEmbedding(request.index_identity, default_identity(), (0.0,) * 384)


class Gate:
    def begin(self, index):
        return "old-sha"

    def finish(self, index, original_digest):
        assert original_digest == "old-sha"


class Vectors:
    def __init__(self, rows):
        self.rows = rows
        self.fetches = []

    def count(self, index, document_id):
        return len(self.rows)

    def query(self, index, vector, k, document_id):
        self.fetches.append(k)
        return tuple(self.rows[:k])


def record(index, chunk_id):
    text = chunk_id
    source = ChunkSource("node", (PageSpan(1, None, "p1"),), 0, len(text), False, "none")
    metadata = ChunkMetadata("chunk_metadata_v3", "doc", "a.pdf", "a.pdf", "build",
                             "a" * 64, index.build_fingerprint,
                             hashlib.sha256(text.encode()).hexdigest(), 0, "text", None,
                             (1,), (source,), None, 0, 1)
    return VectorRecord(chunk_id, "doc", "build", "a" * 64, text, metadata, (0.0,) * 384)


def test_expands_tie_boundary_then_sorts_by_full_distance_and_id():
    index = index_identity(builtin_configuration("plain_text"))
    rows = [VectorQueryHit(record(index, chunk_id), distance) for chunk_id, distance in (
        ("d", 0.1), ("c", 0.1), ("b", 0.1), ("aa", 0.1), ("z", 0.2))]
    vectors = Vectors(rows)
    encoder = Encoder()
    retriever = StableSemanticRetriever(Health(), encoder, vectors, (), Gate(), default_identity())
    result = retriever.retrieve(RetrievalRequest("price?", 2, None, index, "plain_text"))
    assert vectors.fetches == [4, 5]
    assert [hit.chunk_id for hit in result.hits] == ["aa", "b"]


def test_unhealthy_index_stops_before_embedding_or_vector_read():
    index = index_identity(builtin_configuration("plain_text"))
    encoder = Encoder()
    vectors = Vectors(())
    with pytest.raises(RetrievalError):
        StableSemanticRetriever(Health(False), encoder, vectors, (), Gate(), default_identity()).retrieve(
            RetrievalRequest("price?", 2, None, index, "plain_text"))
    assert encoder.calls == 0
    assert vectors.fetches == []


def test_manifest_change_discards_completed_zero_hit_read():
    index = index_identity(builtin_configuration("plain_text"))

    class ChangedGate(Gate):
        def finish(self, index, original_digest):
            raise RuntimeError("manifest changed")

    with pytest.raises(RuntimeError, match="manifest changed"):
        StableSemanticRetriever(Health(), Encoder(), Vectors(()), (), ChangedGate(),
                                default_identity()).retrieve(
            RetrievalRequest("price?", 2, None, index, "plain_text"))
