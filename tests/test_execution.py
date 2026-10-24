from rag.index.store import StoredChunk
from rag.query.execution import plain
from rag.query.retriever import Hit
from rag.query.units import ResultUnit


def chunk(cid):
    return StoredChunk(cid, "d", "D", "D.pdf", 0, "text", (1,), None, f"text {cid}")


class FakeSearch:
    """Ranks three chunks; the last two belong to one table, returned as one unit."""
    def __init__(self):
        self.calls = 0

    def rank_hits(self, question, document_ids=None):
        self.calls += 1
        return [Hit(chunk(c), 1.0 / n) for n, c in enumerate(("c1", "t1", "t2"), start=1)]

    def units_for(self, hits):
        return [ResultUnit(hits[0]), ResultUnit(hits[1])]


def test_plain_execution_returns_the_full_ranking_once_and_a_budget_takes_its_head():
    searcher = FakeSearch()
    run = plain(searcher, "q")
    assert searcher.calls == 1 and run.cost.retrievals == 1 and run.cost.llm_calls == 0
    assert [u.hit.chunk.chunk_id for u in run.units] == ["c1", "t1"]
    assert run.chunk_order == ("c1", "t1", "t2")
    assert [u.hit.chunk.chunk_id for u in run.first(1)] == ["c1"]
    assert all(s.query == "q" and s.round == 1 for s in run.order)
