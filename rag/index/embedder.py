"""Embedder interface and the E5 implementation."""

from __future__ import annotations

from typing import Any, Protocol


class Embedder(Protocol):
    def identity(self) -> dict[str, object]: ...
    def encode_passages(self, texts: list[str]) -> list[list[float]]: ...
    def encode_query(self, text: str) -> list[float]: ...


class E5Embedder:
    """multilingual-e5: `passage: ` / `query: ` prefixes, L2-normalized vectors."""

    passage_prefix = "passage: "
    query_prefix = "query: "

    def __init__(self, model: str = "intfloat/multilingual-e5-small",
                 revision: str = "614241f622f53c4eeff9890bdc4f31cfecc418b3",
                 batch_size: int = 32):
        self.model_name = model
        self.revision = revision
        self.batch_size = batch_size
        self._model: Any = None

    def identity(self) -> dict[str, object]:
        return {"model": self.model_name, "revision": self.revision,
                "passage_prefix": self.passage_prefix, "query_prefix": self.query_prefix,
                "normalized": True}

    def _load_model(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name, revision=self.revision)
        return self._model

    def _encode(self, texts: list[str], batch_size: int) -> list[list[float]]:
        vectors = self._load_model().encode(texts, batch_size=batch_size, show_progress_bar=False,
                                            normalize_embeddings=True)
        result = [[float(value) for value in vector] for vector in vectors.tolist()]
        if len(result) != len(texts):
            raise RuntimeError("编码结果数量与输入不一致")
        return result

    def encode_passages(self, texts: list[str]) -> list[list[float]]:
        return self._encode([self.passage_prefix + text for text in texts], self.batch_size)

    def encode_query(self, text: str) -> list[float]:
        return self._encode([self.query_prefix + text.strip()], 1)[0]
