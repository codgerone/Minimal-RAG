from rag.index.store import StoredChunk, VectorHit
from rag.query.prompt import SYSTEM_PROMPT, build_messages
from rag.index.tables import StoredTable
from rag.query.retriever import Hit, SemanticRetriever
from rag.query.units import ResultUnit, to_units


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
    assert [(h.semantic_rank, h.similarity, h.lexical_rank) for h in hits] == [(1, 0.9, None), (2, 0.8, None)]
    assert store.requests[-1] > store.requests[0]      # widened until the tie group was complete


def test_empty_index_returns_no_hits():
    assert SemanticRetriever(Embedder(), TieStore({})).retrieve("q", 3) == []


def test_prompt_keeps_chunk_text_verbatim_and_escapes_attributes():
    hit = Hit(StoredChunk("d-c0001", "d", 'a"b.pdf', "x/a.pdf", 1, "table", (2, 3), 10,
                          "C&I Meter 3&4 Wires"), 0.9)
    system, user = build_messages("  价格？ ", [ResultUnit(hit)])
    assert system["content"] == SYSTEM_PROMPT
    assert "C&I Meter 3&4 Wires" in user["content"]
    assert 'document="a&quot;b.pdf"' in user["content"] and 'pages="2,3"' in user["content"]
    assert user["content"].endswith("用户问题：价格？")


def test_a_table_takes_the_place_of_its_best_ranked_chunk_and_later_results_move_up():
    table = StoredTable("doc-tbl", "doc", "whole table", ("c2", "c4", "c5"))
    hits = [Hit(chunk(cid), 1.0 - i / 10) for i, cid in enumerate(["c1", "c4", "c2", "c3", "c5", "c6"])]
    units = to_units(hits, {cid: table for cid in table.chunk_ids}, {"doc-tbl": (2, 3)})
    assert [(u.hit.chunk.chunk_id, u.table is not None) for u in units] == [
        ("c1", False), ("c4", True), ("c3", False), ("c6", False)]
    assert units[1].text == "whole table" and units[1].chunk_ids == ("c2", "c4", "c5")
    assert units[1].pages == (2, 3) and units[0].chunk_ids == ("c1",)


def test_prompt_marks_a_whole_table_and_cites_its_table_id():
    table = StoredTable("doc-tbl", "doc", "| A | 1 |", ("c2",))
    _, user = build_messages("q", [ResultUnit(Hit(chunk("c2"), 0.9), table, (4,))])
    assert '[TABLE document="a.pdf"' in user["content"] and 'id="doc-tbl"' in user["content"]
    assert 'pages="4"' in user["content"] and "| A | 1 |\n[/TABLE]" in user["content"]
