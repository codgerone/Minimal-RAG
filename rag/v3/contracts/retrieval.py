"""V3 embedding, retrieval and grounded answer boundary models."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from rag.v3.contracts.documents import ChunkKind, ChunkSource
from rag.v3.contracts.storage import IndexIdentity


@dataclass(frozen=True)
class EmbeddingIdentity:
    model_name: str
    revision: str
    dimension: int
    normalize: Literal[True]
    passage_prefix: Literal["passage: "]
    query_prefix: Literal["query: "]
    add_special_tokens: Literal[True]

    def __post_init__(self) -> None:
        if not self.model_name or not self.revision or self.dimension <= 0 or not self.normalize:
            raise ValueError("invalid embedding identity")
        if self.passage_prefix != "passage: " or self.query_prefix != "query: " or not self.add_special_tokens:
            raise ValueError("unsupported E5 prefix or token policy")


@dataclass(frozen=True)
class PassageEmbeddingBatch:
    index_identity: IndexIdentity
    document_id: str
    file_hash: str
    build_id: str
    embedding_identity: EmbeddingIdentity
    chunk_ids: tuple[str, ...]
    vectors: tuple[tuple[float, ...], ...]

    def __post_init__(self) -> None:
        if not self.chunk_ids or len(self.chunk_ids) != len(self.vectors):
            raise ValueError("embedding and chunk counts disagree")
        if len(set(self.chunk_ids)) != len(self.chunk_ids):
            raise ValueError("duplicate embedded chunk ID")
        if any(len(vector) != self.embedding_identity.dimension or
               not all(math.isfinite(value) for value in vector) for vector in self.vectors):
            raise ValueError("invalid passage vector")


@dataclass(frozen=True)
class RetrievalRequest:
    question: str
    top_k: int
    document_id: str | None
    index_identity: IndexIdentity
    configuration_name: str

    def __post_init__(self) -> None:
        if not self.question.strip() or type(self.top_k) is not int or self.top_k <= 0:
            raise ValueError("question and positive K required")


@dataclass(frozen=True)
class QueryEmbedding:
    index_identity: IndexIdentity
    embedding_identity: EmbeddingIdentity
    vector: tuple[float, ...]

    def __post_init__(self) -> None:
        if (len(self.vector) != self.embedding_identity.dimension or
                not all(math.isfinite(value) for value in self.vector)):
            raise ValueError("invalid query vector")


@dataclass(frozen=True)
class RetrievalHit:
    chunk_id: str
    document_id: str
    document_name: str
    relative_path: str
    text: str
    chunk_index: int
    kind: ChunkKind
    sources: tuple[ChunkSource, ...]
    distance: float

    def __post_init__(self) -> None:
        if not self.chunk_id or not math.isfinite(self.distance):
            raise ValueError("invalid retrieval hit")


@dataclass(frozen=True)
class RetrievalResult:
    request: RetrievalRequest
    hits: tuple[RetrievalHit, ...]
    checked_index_identity: IndexIdentity

    def __post_init__(self) -> None:
        if self.checked_index_identity != self.request.index_identity:
            raise ValueError("retrieval checked another index")
        if len({item.chunk_id for item in self.hits}) != len(self.hits):
            raise ValueError("duplicate retrieval hit")
        if list(self.hits) != sorted(self.hits, key=lambda item: (item.distance, item.chunk_id)):
            raise ValueError("retrieval hits are not stable sorted")


@dataclass(frozen=True)
class AnswerRequest:
    question: str
    top_k: int
    document_id: str | None
    configuration_name: str
    index_identity: IndexIdentity

    def __post_init__(self) -> None:
        if not self.question.strip() or self.top_k <= 0 or not self.configuration_name:
            raise ValueError("invalid answer request")


@dataclass(frozen=True)
class PromptMessage:
    role: Literal["system", "user", "assistant"]
    content: str

    def __post_init__(self) -> None:
        if self.role not in {"system", "user", "assistant"} or not self.content:
            raise ValueError("invalid prompt message")


@dataclass(frozen=True)
class LanguageModelRequest:
    messages: tuple[PromptMessage, ...]
    model_name: str
    temperature: float

    def __post_init__(self) -> None:
        if not self.messages or not self.model_name or self.temperature != 0:
            raise ValueError("invalid V3 language model request")


@dataclass(frozen=True)
class AnswerResult:
    answer: str
    retrieval: RetrievalResult
    messages: tuple[PromptMessage, ...]
    model_name: str

    def __post_init__(self) -> None:
        if not self.answer.strip() or not self.messages or not self.model_name:
            raise ValueError("invalid answer result")
