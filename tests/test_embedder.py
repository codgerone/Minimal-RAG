import numpy as np
import pytest

from rag.index import embedder as embedders
from rag.index.embedder import EmbeddingError


class FakeModel:
    """Stands in for SentenceTransformer: one token per word, records what it encodes."""

    max_seq_length = 5

    def __init__(self):
        self.encoded: list[str] = []

    def tokenizer(self, text, truncation=False):
        return {"input_ids": text.split()}

    def encode(self, texts, **kwargs):
        self.encoded.extend(texts)
        return np.ones((len(texts), 3))


def with_fake(item):
    item._model = FakeModel()
    return item


def test_e5_small_identity_is_unchanged():
    # The built-in indexes' fingerprints depend on this exact identity.
    assert embedders.e5_small().identity() == {
        "model": "intfloat/multilingual-e5-small",
        "revision": "614241f622f53c4eeff9890bdc4f31cfecc418b3",
        "passage_prefix": "passage: ", "query_prefix": "query: ", "normalized": True}


@pytest.mark.parametrize("factory, query, passage", [
    (embedders.e5_small, "query: q", "passage: p"),
    (embedders.e5_large_instruct, f"Instruct: {embedders.RETRIEVAL_TASK}\nQuery: q", "p"),
    (embedders.bge_m3, "q", "p"),
    (embedders.qwen3_embedding_0_6b, f"Instruct: {embedders.RETRIEVAL_TASK}\nQuery:q", "p"),
])
def test_query_and_passage_inputs(factory, query, passage):
    item = with_fake(factory())
    item.encode_query(" q ")
    item.encode_passages(["p"])
    assert item._model.encoded == [query, passage]


def test_every_candidate_has_a_distinct_pinned_identity():
    identities = [f().identity() for f in (embedders.e5_small, embedders.e5_large_instruct,
                                           embedders.bge_m3, embedders.qwen3_embedding_0_6b)]
    assert all(len(i["revision"]) == 40 for i in identities)
    assert len({(i["model"], i["revision"]) for i in identities}) == 4


def test_passage_over_the_model_input_limit_is_rejected():
    item = with_fake(embedders.bge_m3())
    item.encode_passages(["one two three four five"])
    with pytest.raises(EmbeddingError, match="超过编码器"):
        item.encode_passages(["ok", "one two three four five six"])
