"""E5 input identity and structured budget gates."""

from __future__ import annotations

import pytest

from rag.v3.adapters.e5 import E5EmbeddingAdapter, E5Error, E5PassageCounter, default_identity
from rag.v3.application.assembly import builtin_configuration, index_identity
from rag.v3.contracts.documents import ChunkBatch, ChunkSource, DocumentChunk, PageSpan
from rag.v3.contracts.retrieval import RetrievalRequest


class RecordingModel:
    def __init__(self):
        self.calls = []

    def encode(self, texts, *, batch_size, show_progress_bar, normalize_embeddings):
        self.calls.append((texts, batch_size, show_progress_bar, normalize_embeddings))
        return [[0.0] * 383 + [1.0] for _ in texts]


class CharacterTokenizer:
    def __call__(self, text, *, add_special_tokens, truncation):
        return {"input_ids": list(range(len(text) + (2 if add_special_tokens else 0)))}


def sample_batch():
    source = ChunkSource("page-1", (PageSpan(1, None, "page:1"),), 0, 5, False, "none")
    chunk = DocumentChunk("doc-p1-c00", "doc", 0, "text", "hello", None,
                          (source,), None, 0, 1)
    return ChunkBatch("doc", "a" * 64, (chunk,), 5, ())


def test_passage_query_prefixes_and_identity_are_explicit():
    identity = default_identity()
    model = RecordingModel()
    adapter = E5EmbeddingAdapter(identity, lambda *a, **kw: model)
    index = index_identity(builtin_configuration("plain_text"))
    result = adapter.encode_passages(sample_batch(), "build", index)
    query = adapter.encode_query(RetrievalRequest("  order  ", 1, None, index, "plain_text"))
    assert result.embedding_identity == query.embedding_identity == identity
    assert model.calls[0][0] == ["passage: hello"]
    assert model.calls[1][0] == ["query: order"]
    assert all(call[-1] is True for call in model.calls)


def test_structured_budget_is_checked_before_model_encode():
    identity = default_identity()
    model = RecordingModel()
    adapter = E5EmbeddingAdapter(identity, lambda *a, **kw: model)
    counter = E5PassageCounter(identity, lambda *a, **kw: CharacterTokenizer())
    index = index_identity(builtin_configuration("structured"))
    assert counter.count_passage("hello") == len("passage: hello") + 2
    with pytest.raises(E5Error) as raised:
        adapter.encode_passages(sample_batch(), "build", index, counter=counter,
                                maximum_input_tokens=2)
    assert raised.value.code == "token_budget_exceeded"
    assert model.calls == []
