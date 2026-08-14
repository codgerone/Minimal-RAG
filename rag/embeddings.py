"""Explicit multilingual E5 embedding adapter."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from rag.errors import EmbeddingError


class E5Embedder:
    def __init__(
        self,
        model_name: str,
        model_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.model_name = model_name
        self._model_factory = model_factory
        self._model: Any | None = None

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            if self._model_factory is None:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(self.model_name)
            else:
                self._model = self._model_factory(self.model_name)
            return self._model
        except Exception as exc:
            raise EmbeddingError(
                f"无法加载 Embedding 模型 {self.model_name!r}。",
                "请检查网络连接、模型名称和本机磁盘空间后重试。",
                cause=exc,
            ) from exc

    @staticmethod
    def _as_python_vectors(encoded: Any) -> list[list[float]]:
        values = encoded.tolist() if hasattr(encoded, "tolist") else encoded
        return [[float(value) for value in vector] for vector in values]

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        if any(not text.strip() for text in texts):
            raise EmbeddingError("待向量化的 passage 不能为空。")
        inputs = [f"passage: {text}" for text in texts]
        try:
            encoded = self._load_model().encode(
                inputs,
                batch_size=32,
                show_progress_bar=False,
                normalize_embeddings=True,
            )
            return self._as_python_vectors(encoded)
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(
                f"使用模型 {self.model_name!r} 生成 passage 向量失败。",
                "请检查模型文件和可用内存后重试。",
                cause=exc,
            ) from exc

    def embed_query(self, question: str) -> list[float]:
        stripped = question.strip()
        if not stripped:
            raise EmbeddingError("检索问题不能为空。")
        try:
            encoded = self._load_model().encode(
                [f"query: {stripped}"],
                batch_size=1,
                show_progress_bar=False,
                normalize_embeddings=True,
            )
            return self._as_python_vectors(encoded)[0]
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(
                f"使用模型 {self.model_name!r} 生成 query 向量失败。",
                "请检查模型文件和可用内存后重试。",
                cause=exc,
            ) from exc

