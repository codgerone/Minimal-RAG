"""Retrievers: dense semantic, BM25, and their reciprocal-rank fusion. Rules: docs/rules/lexical.md.

Every retriever orders deterministically (ties by chunk_id) and reports, per hit, the
rank and raw score each underlying ranking gave it, so a result can be traced back.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Collection, Protocol

from rag.index.analyzer import Analyzer
from rag.index.embedder import Embedder
from rag.index.lexical import LexicalStore
from rag.index.store import ChromaStore, StoredChunk

RRF_K = 60


@dataclass(frozen=True)
class Hit:
    chunk: StoredChunk
    score: float                        # the retriever's ranking score, higher first
    semantic_rank: int | None = None    # rank among the scope's chunks by vector similarity
    similarity: float | None = None     # cosine similarity
    lexical_rank: int | None = None     # rank among the scope's chunks with a BM25 score > 0
    bm25: float | None = None


class Retriever(Protocol):
    def retrieve(self, question: str, top_k: int,
                 document_ids: Collection[str] | None = None) -> list[Hit]:
        """Top-K hits within the given documents (None = whole index)."""
        ...


def _check(top_k: int) -> None:
    if top_k <= 0:
        raise ValueError("top_k 必须为正整数")


class SemanticRetriever:
    """Orders hits by (distance, chunk_id). When the K-th distance is tied, the
    candidate pool is widened until the tie group is complete, so equal-distance
    chunks are never cut arbitrarily by the vector index."""

    def __init__(self, embedder: Embedder, store: ChromaStore):
        self.embedder = embedder
        self.store = store

    def retrieve(self, question: str, top_k: int,
                 document_ids: Collection[str] | None = None) -> list[Hit]:
        _check(top_k)
        total = self.store.count(document_ids)
        if total == 0:
            return []
        vector = self.embedder.encode_query(question)
        fetch = min(total, max(top_k + 1, 2 * top_k))
        while True:
            ordered = sorted(self.store.query(vector, fetch, document_ids),
                             key=lambda hit: (hit.distance, hit.chunk.chunk_id))
            if fetch >= total or len(ordered) < top_k:
                break
            if ordered[-1].distance > ordered[top_k - 1].distance:
                break
            fetch = min(total, 2 * fetch)
        return [Hit(h.chunk, 1 - h.distance, semantic_rank=rank, similarity=1 - h.distance)
                for rank, h in enumerate(ordered[:top_k], start=1)]

    def rank_all(self, question: str,
                 document_ids: Collection[str] | None = None) -> list[tuple[StoredChunk, float]]:
        """Every chunk in scope with its similarity, best first."""
        total = self.store.count(document_ids)
        if total == 0:
            return []
        hits = self.store.query(self.embedder.encode_query(question), total, document_ids)
        return [(h.chunk, 1 - h.distance)
                for h in sorted(hits, key=lambda hit: (hit.distance, hit.chunk.chunk_id))]


class BM25Retriever:
    """Chunks in scope with a BM25 score > 0, by (score desc, chunk_id)."""

    def __init__(self, analyzer: Analyzer, lexical: LexicalStore, store: ChromaStore):
        self.analyzer = analyzer
        self.lexical = lexical
        self.store = store

    def rank_all(self, question: str,
                 document_ids: Collection[str] | None = None) -> list[tuple[str, float]]:
        scores = self.lexical.score(self.analyzer.query_terms(question), document_ids)
        return sorted(scores.items(), key=lambda item: (-item[1], item[0]))

    def retrieve(self, question: str, top_k: int,
                 document_ids: Collection[str] | None = None) -> list[Hit]:
        _check(top_k)
        ranked = self.rank_all(question, document_ids)[:top_k]
        chunks = self.store.get_chunks([chunk_id for chunk_id, _ in ranked])
        return [Hit(chunks[chunk_id], score, lexical_rank=rank, bm25=score)
                for rank, (chunk_id, score) in enumerate(ranked, start=1)]


class HybridRetriever:
    """Reciprocal rank fusion: score = 1 / (RRF_K + semantic rank) + α / (RRF_K + BM25 rank) over the
    semantic ranking of all chunks in scope and the BM25 ranking of the chunks with a BM25 score > 0;
    α is `bm25_weight`."""

    def __init__(self, semantic: SemanticRetriever, lexical: BM25Retriever, bm25_weight: float = 1.0):
        self.semantic = semantic
        self.lexical = lexical
        self.bm25_weight = bm25_weight

    def fused(self, semantic_rank: int, lexical_rank: int | None) -> float:
        return 1 / (RRF_K + semantic_rank) + (self.bm25_weight / (RRF_K + lexical_rank) if lexical_rank else 0.0)

    def refuse(self, hits: list[Hit]) -> list[Hit]:
        """Hits that carry both channels' ranks, scored and ordered again with this retriever's α."""
        rescored = [replace(h, score=self.fused(h.semantic_rank, h.lexical_rank)) for h in hits]
        return sorted(rescored, key=lambda hit: (-hit.score, hit.chunk.chunk_id))

    def retrieve(self, question: str, top_k: int,
                 document_ids: Collection[str] | None = None) -> list[Hit]:
        _check(top_k)
        dense = self.semantic.rank_all(question, document_ids)
        sparse = {chunk_id: (rank, score) for rank, (chunk_id, score)
                  in enumerate(self.lexical.rank_all(question, document_ids), start=1)}
        hits = []
        for rank, (chunk, similarity) in enumerate(dense, start=1):
            lexical_rank, bm25 = sparse.get(chunk.chunk_id, (None, None))
            hits.append(Hit(chunk, self.fused(rank, lexical_rank), rank, similarity, lexical_rank, bm25))
        return sorted(hits, key=lambda hit: (-hit.score, hit.chunk.chunk_id))[:top_k]


def build_retriever(assembly, store: ChromaStore, lexical: LexicalStore | None) -> Retriever:
    """The configured retriever; assembly validation guarantees `lexical` for bm25 / hybrid."""
    use = assembly.config.retriever.use
    semantic = SemanticRetriever(assembly.embedder, store)
    if use == "semantic":
        return semantic
    assert lexical is not None and assembly.lexical is not None
    bm25 = BM25Retriever(assembly.lexical, lexical, store)
    return bm25 if use == "bm25" else HybridRetriever(semantic, bm25, assembly.config.bm25_weight)
