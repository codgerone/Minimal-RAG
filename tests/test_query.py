from rag.index.store import StoredChunk, VectorHit
from rag.query.prompt import SYSTEM_PROMPT, build_messages
from rag.query.retriever import SemanticRetriever


def chunk(chunk_id: str, text: str = "x") -> StoredChunk:
    return StoredChunk(chunk_id, "doc", "a.pdf", "a.pdf", 0, "text", (1,), None, text)


class TieStore:
    """The vector index returns ties in arbitrary order and only the requested count."""

    def __init__(self, distances: dict[str, float]):
        self.distances = distances
        self.requests: list[int] = []

    def count(self, document_ids=None):
        return len(self.distances)

    def query(self, vector, n, document_ids=None):
        self.requests.append(n)
        ranked = sorted(self.distances.items(), key=lambda kv: (kv[1], -int(kv[0][1:])))
        return [VectorHit(chunk(cid), d) for cid, d in ranked[:n]]


class Embedder:
    def encode_query(self, text):
        return [1.0]


def test_ties_at_the_k_boundary_are_resolved_by_chunk_id():
    store = TieStore({"c1": 0.1, "c5": 0.2, "c4": 0.2, "c3": 0.2, "c2": 0.2, "c6": 0.3})
    hits = SemanticRetriever(Embedder(), store).retrieve("q", 2)
    assert [h.chunk.chunk_id for h in hits] == ["c1", "c2"]
    assert store.requests[-1] > store.requests[0]      # widened until the tie group was complete


def test_empty_index_returns_no_hits():
    assert SemanticRetriever(Embedder(), TieStore({})).retrieve("q", 3) == []


def test_prompt_keeps_chunk_text_verbatim_and_escapes_attributes():
    hit = VectorHit(StoredChunk("d-c0001", "d", 'a"b.pdf', "x/a.pdf", 1, "table", (2, 3), 10,
                                "C&I Meter 3&4 Wires"), 0.1)
    system, user = build_messages("  价格？ ", [hit])
    assert system["content"] == SYSTEM_PROMPT
    assert "C&I Meter 3&4 Wires" in user["content"]
    assert 'document="a&quot;b.pdf"' in user["content"] and 'pages="2,3"' in user["content"]
    assert user["content"].endswith("用户问题：价格？")
