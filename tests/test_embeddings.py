from typing import Any

import pytest

from rag.embeddings import E5Embedder
from rag.errors import EmbeddingError


class FakeModel:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def encode(self, inputs: list[str], **kwargs: Any) -> list[list[float]]:
        self.calls.append((inputs, kwargs))
        return [[float(index), 1.0] for index, _ in enumerate(inputs)]


def test_e5_uses_query_and_passage_prefixes_without_eager_loading() -> None:
    fake = FakeModel()
    factory_calls: list[tuple[str, str]] = []

    def factory(name: str, *, revision: str) -> FakeModel:
        factory_calls.append((name, revision))
        return fake

    embedder = E5Embedder("test-e5", model_factory=factory)
    assert factory_calls == []

    passages = embedder.embed_passages(["alpha", "beta"])
    query = embedder.embed_query("  question  ")

    assert passages == [[0.0, 1.0], [1.0, 1.0]]
    assert query == [0.0, 1.0]
    assert fake.calls[0][0] == ["passage: alpha", "passage: beta"]
    assert fake.calls[1][0] == ["query: question"]
    assert all(call[1]["normalize_embeddings"] for call in fake.calls)
    assert factory_calls == [
        ("test-e5", "614241f622f53c4eeff9890bdc4f31cfecc418b3")
    ]


def test_empty_query_is_rejected_before_loading_model() -> None:
    embedder = E5Embedder(
        "test", model_factory=lambda *_args, **_kwargs: pytest.fail("loaded")
    )
    with pytest.raises(EmbeddingError, match="不能为空"):
        embedder.embed_query(" ")

