"""Retriever interface and dense semantic retrieval with a deterministic top-K boundary."""

from __future__ import annotations

from typing import Protocol

from rag.index.embedder import Embedder
from rag.index.store import ChromaStore, VectorHit


class Retriever(Protocol):
    def retrieve(self, question: str, top_k: int,
                 document_id: str | None = None) -> list[VectorHit]: ...


class SemanticRetriever:
    """Orders hits by (distance, chunk_id). When the K-th distance is tied, the
    candidate pool is widened until the tie group is complete, so equal-distance
    chunks are never cut arbitrarily by the vector index."""

    def __init__(self, embedder: Embedder, store: ChromaStore):
        self.embedder = embedder
        self.store = store

    def retrieve(self, question: str, top_k: int,
                 document_id: str | None = None) -> list[VectorHit]:
        if top_k <= 0:
            raise ValueError("top_k 必须为正整数")
        total = self.store.count(document_id)
        if total == 0:
            return []
        vector = self.embedder.encode_query(question)
        fetch = min(total, max(top_k + 1, 2 * top_k))
        while True:
            ordered = sorted(self.store.query(vector, fetch, document_id),
                             key=lambda hit: (hit.distance, hit.chunk.chunk_id))
            if fetch >= total or len(ordered) < top_k:
                break
            if ordered[-1].distance > ordered[top_k - 1].distance:
                break
            fetch = min(total, 2 * fetch)
        return ordered[:top_k]
