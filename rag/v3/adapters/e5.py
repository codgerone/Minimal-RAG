"""Locked E5 passage/query encoder and exact structured passage counter."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from rag.v3.contracts.documents import ChunkBatch
from rag.v3.contracts.retrieval import (
    EmbeddingIdentity, PassageEmbeddingBatch, QueryEmbedding, RetrievalRequest,
)
from rag.v3.contracts.storage import IndexIdentity


class E5Error(ValueError):
    def __init__(self, code: Literal["model_load_failed", "tokenizer_load_failed",
                                     "encoding_failed", "invalid_embedding_input",
                                     "embedding_shape_mismatch", "token_budget_exceeded"], detail: str):
        self.code = code
        super().__init__(detail)


def default_identity() -> EmbeddingIdentity:
    return EmbeddingIdentity("intfloat/multilingual-e5-small",
                             "614241f622f53c4eeff9890bdc4f31cfecc418b3",
                             384, True, "passage: ", "query: ", True)


class E5PassageCounter:
    def __init__(self, identity: EmbeddingIdentity,
                 tokenizer_factory: Callable[..., Any] | None = None):
        self.identity = identity
        self._factory = tokenizer_factory
        self._tokenizer: Any | None = None

    def _load(self) -> Any:
        if self._tokenizer is None:
            try:
                if self._factory is None:
                    from transformers import AutoTokenizer
                    self._tokenizer = AutoTokenizer.from_pretrained(
                        self.identity.model_name, revision=self.identity.revision)
                else:
                    self._tokenizer = self._factory(self.identity.model_name,
                                                    revision=self.identity.revision)
            except Exception as exc:
                raise E5Error("tokenizer_load_failed", self.identity.model_name) from exc
        return self._tokenizer

    def _count(self, text: str, *, special: bool) -> int:
        try:
            encoded = self._load()(text, add_special_tokens=special, truncation=False)
            ids = encoded["input_ids"]
            if ids and isinstance(ids[0], list):
                ids = ids[0]
            return len(ids)
        except E5Error:
            raise
        except Exception as exc:
            raise E5Error("encoding_failed", "token count failed") from exc

    def count_passage(self, text: str) -> int:
        if not text:
            raise E5Error("invalid_embedding_input", "passage cannot be empty")
        return self._count(self.identity.passage_prefix + text, special=True)

    def count_text(self, text: str) -> int:
        return self._count(text, special=False)


class E5EmbeddingAdapter:
    def __init__(self, identity: EmbeddingIdentity,
                 model_factory: Callable[..., Any] | None = None):
        self.identity = identity
        self._factory = model_factory
        self._model: Any | None = None

    def _load(self) -> Any:
        if self._model is None:
            try:
                if self._factory is None:
                    from sentence_transformers import SentenceTransformer
                    self._model = SentenceTransformer(self.identity.model_name,
                                                      revision=self.identity.revision)
                else:
                    self._model = self._factory(self.identity.model_name,
                                                revision=self.identity.revision)
            except Exception as exc:
                raise E5Error("model_load_failed", self.identity.model_name) from exc
        return self._model

    def _encode(self, texts: list[str], *, batch_size: int) -> tuple[tuple[float, ...], ...]:
        try:
            encoded = self._load().encode(texts, batch_size=batch_size,
                                          show_progress_bar=False, normalize_embeddings=True)
            values = encoded.tolist() if hasattr(encoded, "tolist") else encoded
            vectors = tuple(tuple(float(value) for value in vector) for vector in values)
            if len(vectors) != len(texts):
                raise E5Error("embedding_shape_mismatch", "vector count differs from input count")
            return vectors
        except E5Error:
            raise
        except Exception as exc:
            raise E5Error("encoding_failed", self.identity.model_name) from exc

    def encode_passages(self, batch: ChunkBatch, build_id: str,
                        index: IndexIdentity, *, counter: E5PassageCounter | None = None,
                        maximum_input_tokens: int | None = None) -> PassageEmbeddingBatch:
        if not build_id or not batch.chunks:
            raise E5Error("invalid_embedding_input", "build ID and chunks required")
        if (counter is None) != (maximum_input_tokens is None):
            raise E5Error("invalid_embedding_input", "counter and budget must be paired")
        if counter is not None:
            if counter.identity != self.identity:
                raise E5Error("invalid_embedding_input", "tokenizer identity differs from embedder")
            if any(counter.count_passage(chunk.text) > maximum_input_tokens for chunk in batch.chunks):
                raise E5Error("token_budget_exceeded", "structured passage exceeds token budget")
        vectors = self._encode([self.identity.passage_prefix + item.text for item in batch.chunks],
                               batch_size=32)
        try:
            return PassageEmbeddingBatch(index, batch.document_id, batch.file_hash, build_id,
                                         self.identity, tuple(item.chunk_id for item in batch.chunks), vectors)
        except ValueError as exc:
            raise E5Error("embedding_shape_mismatch", str(exc)) from exc

    def encode_query(self, request: RetrievalRequest) -> QueryEmbedding:
        question = request.question.strip()
        if not question:
            raise E5Error("invalid_embedding_input", "query cannot be empty")
        vectors = self._encode([self.identity.query_prefix + question], batch_size=1)
        try:
            return QueryEmbedding(request.index_identity, self.identity, vectors[0])
        except ValueError as exc:
            raise E5Error("embedding_shape_mismatch", str(exc)) from exc
