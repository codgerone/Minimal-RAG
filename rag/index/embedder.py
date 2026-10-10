"""Embedder interface and the sentence-transformers implementations."""

from __future__ import annotations

from typing import Any, Protocol


class Embedder(Protocol):
    def identity(self) -> dict[str, object]: ...
    def encode_passages(self, texts: list[str]) -> list[list[float]]: ...
    def encode_query(self, text: str) -> list[float]: ...


class EmbeddingError(RuntimeError):
    """A passage does not fit the model input; encoding it would silently truncate."""


# Generic retrieval instruction from the instruct models' cards; not tuned to our questions.
RETRIEVAL_TASK = "Given a web search query, retrieve relevant passages that answer the query"


class SentenceTransformerEmbedder:
    """A pinned sentence-transformers model with fixed query / passage prefixes, L2-normalized."""

    def __init__(self, model: str, revision: str, query_prefix: str, passage_prefix: str,
                 batch_size: int = 32):
        self.model_name = model
        self.revision = revision
        self.query_prefix = query_prefix
        self.passage_prefix = passage_prefix
        self.batch_size = batch_size
        self._model: Any = None

    def identity(self) -> dict[str, object]:
        return {"model": self.model_name, "revision": self.revision,
                "passage_prefix": self.passage_prefix, "query_prefix": self.query_prefix,
                "normalized": True}

    def _load_model(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            # Always float32: some checkpoints load as float16 / bfloat16, which CPUs without
            # half-precision kernels run many times slower.
            self._model = SentenceTransformer(self.model_name, revision=self.revision,
                                              model_kwargs={"dtype": "float32"})
        return self._model

    def _encode(self, texts: list[str], batch_size: int) -> list[list[float]]:
        vectors = self._load_model().encode(texts, batch_size=batch_size, show_progress_bar=False,
                                            normalize_embeddings=True)
        result = [[float(value) for value in vector] for vector in vectors.tolist()]
        if len(result) != len(texts):
            raise RuntimeError("编码结果数量与输入不一致")
        return result

    def _check_input_limit(self, texts: list[str]) -> None:
        model = self._load_model()
        limit = model.max_seq_length
        for index, text in enumerate(texts):
            count = len(model.tokenizer(text, truncation=False)["input_ids"])
            if count > limit:
                raise EmbeddingError(f"第 {index + 1} 个 chunk 有 {count} token，超过编码器 "
                                     f"{self.model_name} 的输入上限 {limit}，编码会被截断")

    def encode_passages(self, texts: list[str]) -> list[list[float]]:
        inputs = [self.passage_prefix + text for text in texts]
        self._check_input_limit(inputs)
        return self._encode(inputs, self.batch_size)

    def encode_query(self, text: str) -> list[float]:
        return self._encode([self.query_prefix + text.strip()], 1)[0]


def e5_small(batch_size: int = 32) -> SentenceTransformerEmbedder:
    return SentenceTransformerEmbedder(
        "intfloat/multilingual-e5-small", "614241f622f53c4eeff9890bdc4f31cfecc418b3",
        "query: ", "passage: ", batch_size)


def e5_large_instruct(batch_size: int = 32) -> SentenceTransformerEmbedder:
    return SentenceTransformerEmbedder(
        "intfloat/multilingual-e5-large-instruct", "274baa43b0e13e37fafa6428dbc7938e62e5c439",
        f"Instruct: {RETRIEVAL_TASK}\nQuery: ", "", batch_size)


def bge_m3(batch_size: int = 32) -> SentenceTransformerEmbedder:
    return SentenceTransformerEmbedder(
        "BAAI/bge-m3", "5617a9f61b028005a4858fdac845db406aefb181", "", "", batch_size)


def qwen3_embedding_0_6b(batch_size: int = 32) -> SentenceTransformerEmbedder:
    return SentenceTransformerEmbedder(
        "Qwen/Qwen3-Embedding-0.6B", "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
        f"Instruct: {RETRIEVAL_TASK}\nQuery:", "", batch_size)
