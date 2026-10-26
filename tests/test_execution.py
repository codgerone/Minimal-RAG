import json

import pytest

from rag.index.store import StoredChunk
from rag.index.tables import StoredTable
from rag.query.execution import (ORIGINAL, QuerySettings, RoundRobin, fuse, plain, planned)
from rag.query.planning import Phrasing, Plan, PlanCost, Question
from rag.query.retriever import Hit
from rag.query.units import ResultUnit, RetrievalCache, UnitRanking, UnitSearch


def chunk(cid):
    return StoredChunk(cid, "d", "D", "D.pdf", 0, "text", (1,), None, f"text {cid}")


def unit(cid):
    return ResultUnit(Hit(chunk(cid), 1.0))


class FakeSearch:
    """Ranks three chunks; the last two belong to one table, returned as one unit."""
    def __init__(self):
        self.calls = 0

    def rank(self, text, document_ids=None):
        self.calls += 1
        hits = [Hit(chunk(c), 1.0 / n) for n, c in enumerate(("c1", "t1", "t2"), start=1)]
        return UnitRanking((ResultUnit(hits[0]), ResultUnit(hits[1])), ("c1", "t1", "t2"), 0.5)


def test_plain_execution_returns_the_full_ranking_once_and_a_budget_takes_its_head():
    searcher = FakeSearch()
    run = plain(searcher, "q")
    assert searcher.calls == 1 and run.cost.retrievals == 1 and run.cost.llm_calls == 0
    assert run.cost.retrieval_seconds == 0.5
    assert [u.hit.chunk.chunk_id for u in run.units] == ["c1", "t1"]
    assert run.chunk_order == ("c1", "t1", "t2")
    assert [u.hit.chunk.chunk_id for u in run.first(1)] == ["c1"]
    assert all(s.query == "q" and s.round == 1 for s in run.order)


def test_fusion_sums_reciprocal_ranks_per_unit_and_keeps_every_rank():
    units, ranks = fuse([("question", [unit("a"), unit("b"), unit("c")]),
                         ("rewrite", [unit("c"), unit("b"), unit("a")])])
    # a and c both score 1/61 + 1/63 (ties by key), just above b's 2/62
    assert [u.key for u in units] == ["a", "c", "b"]
    assert ranks[0] == (("question", 1), ("rewrite", 3))
    assert ranks[2] == (("question", 2), ("rewrite", 2))


def test_fusion_counts_a_whole_table_once_per_ranking_whichever_chunk_brought_it_in():
    table = StoredTable("T", "d", "grid", ("t1", "t2"))
    via_t1, via_t2 = ResultUnit(Hit(chunk("t1"), 1.0), table), ResultUnit(Hit(chunk("t2"), 1.0), table)
    units, ranks = fuse([("question", [via_t1, unit("a")]), ("rewrite", [unit("a"), via_t2])])
    # T and a both score 1/61 + 1/62: the table is counted in both rankings
    assert [u.key for u in units] == ["T", "a"] and units[0] is via_t1
    assert ranks[0] == (("question", 1), ("rewrite", 2))


def test_round_robin_takes_one_new_unit_per_ranking_each_round_and_skips_taken_ones():
    picks = RoundRobin().select([[unit("a"), unit("b"), unit("c")], [unit("a"), unit("d")], [unit("b")]])
    assert [(p.route, p.unit.key, p.round) for p in picks] == [
        (0, "a", 1), (1, "d", 1), (2, "b", 1), (0, "c", 2)]


class TextSearch:
    """Each text ranks the units named by its letters, in order."""
    def __init__(self):
        self.texts = []

    def rank(self, text, document_ids=None):
        self.texts.append(text)
        return UnitRanking(tuple(unit(c) for c in text.split()[-1]), (), 0.25)


def question(qid, letters, rewrite):
    return Question(qid, f"{qid} {letters}", (Phrasing("question", f"{qid} {letters}"),
                                              Phrasing("rewrite", f"{qid}r {rewrite}"),
                                              Phrasing("hyde", f"{qid}h zz", "en")))


def make_plan(decomposed):
    questions = (question("Q1", "ab", "ba"), question("Q2", "cd", "dc")) if decomposed else (question("Q1", "ab", "ba"),)
    return Plan("orig xa", decomposed, questions, PlanCost(1, 1.0, 0.004) if decomposed else PlanCost(0, 0.0, 0.0),
                PlanCost(1, 2.0, 0.006))


def test_planned_retrieves_each_enabled_phrasing_once_and_selects_across_questions():
    searcher = TextSearch()
    run = planned(searcher, make_plan(True), QuerySettings(frozenset({"rewrite"}), original_route=True))
    assert searcher.texts == ["Q1 ab", "Q1r ba", "Q2 cd", "Q2r dc", "orig xa"]
    assert run.cost.retrievals == 5 and run.cost.retrieval_seconds == 1.25
    assert (run.cost.llm_calls, run.cost.llm_seconds, run.cost.llm_cost_usd) == (2, 3.0, 0.01)
    only_decomposed = planned(TextSearch(), make_plan(True), QuerySettings(original_route=True))
    assert (only_decomposed.cost.llm_calls, only_decomposed.cost.llm_cost_usd) == (1, 0.004)
    # Q1 fuses to a, b; Q2 to c, d; the original route ranks x, a and comes last in each round
    assert [(s.unit.key, s.question_id, s.round) for s in run.order] == [
        ("a", "Q1", 1), ("c", "Q2", 1), ("x", ORIGINAL, 1), ("b", "Q1", 2), ("d", "Q2", 2)]
    assert run.order[0].ranks == (("question", 1), ("rewrite", 2))
    assert run.order[2].ranks == (("question", 1),) and run.order[2].query == "orig xa"
    assert run.chunk_order == ()


def test_planned_without_decomposition_and_phrasings_is_the_plain_ranking():
    run = planned(TextSearch(), make_plan(False), QuerySettings())
    assert [s.unit.key for s in run.order] == ["a", "b"] and run.cost.retrievals == 1
    with pytest.raises(ValueError):
        planned(TextSearch(), make_plan(False), QuerySettings(original_route=True))


class CountingRetriever:
    def __init__(self):
        self.calls = 0

    def retrieve(self, text, top_k, document_ids=None):
        self.calls += 1
        return [Hit(chunk("c2"), 0.9, 1, 0.8, None, None), Hit(chunk("c1"), 0.5, 2, 0.4, 1, 2.5)][:top_k]


class TwoChunkStore:
    def count(self, document_ids=None):
        return 2

    def get_chunks(self, ids):
        return {i: chunk(i) for i in ids}


def test_a_cached_retrieval_returns_the_same_ranking_and_time_without_calling_the_retriever(tmp_path):
    index = tmp_path / "index"
    index.mkdir()
    (index / "manifest.json").write_text("{}", encoding="utf-8")
    cache = RetrievalCache(tmp_path / "cache", index, "hybrid")
    retriever = CountingRetriever()
    searcher = UnitSearch(retriever, TwoChunkStore(), None, "chunk", cache)
    first = searcher.rank("q", ["d"])
    again = searcher.rank("q", ["d"])
    assert retriever.calls == 1
    assert [u.hit for u in again.units] == [u.hit for u in first.units]
    [saved] = (tmp_path / "cache").glob("*.json")
    assert again.seconds >= json.loads(saved.read_text(encoding="utf-8"))["seconds"]
    searcher.rank("q", ["e"])
    searcher.warm_up("q", ["d"])
    assert retriever.calls == 3 and len(list((tmp_path / "cache").glob("*.json"))) == 2
    (index / "manifest.json").write_text('{"updated": 1}', encoding="utf-8")
    UnitSearch(retriever, TwoChunkStore(), None, "chunk",
               RetrievalCache(tmp_path / "cache", index, "hybrid")).rank("q", ["d"])
    assert retriever.calls == 4
