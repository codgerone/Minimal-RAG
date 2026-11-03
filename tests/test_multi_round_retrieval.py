from rag.index.store import StoredChunk
from rag.query.multi_round.retrieval import Retrieval, UnitTable
from rag.query.multi_round.state import (FIRST, Q0, Judgement, NewQuestion, PlanOutput, QuestionList, Schedule)
from rag.query.retriever import Hit
from rag.query.units import ResultUnit, UnitRanking


def unit(cid):
    return ResultUnit(Hit(StoredChunk(cid, "d", "D", "D.pdf", 0, "text", (1,), None, f"text {cid}"), 1.0))


class LetterSearch:
    """A text ranks the units named by the letters of its last word, in order."""
    def __init__(self):
        self.texts = []

    def rank(self, text, document_ids=None):
        self.texts.append(text)
        return UnitRanking(tuple(unit(c) for c in text.split()[-1]), (), 0.0)


FALLBACK = {"rewrite": (("question", "rw cfg"),), "hyde": (("spanish", "hy hgb"),), "step_back": (("question", "sb ijb"),),
            "translation": (("spanish", "tr kab"),)}


def setup(k=2, schedule=None):
    ql = QuestionList("CODE1 q abcdef", schedule) if schedule else QuestionList("CODE1 q abcdef")
    return ql, Retrieval(LetterSearch(), None, ["CODE1"], k), UnitTable()


def test_q0_is_first_retrieved_by_its_text_without_the_codes_and_hands_k_units():
    ql, r, table = setup()
    new = r.round(ql, 1, 1, table)
    assert r.search.texts == ["q abcdef"] and new == ["a", "b"] and table.order == ["a", "b"]
    run = ql[Q0].attempts[0].runs[0]
    assert (run.mode, run.queries, run.cursor) == (FIRST, ["q abcdef"], 2) and [h.rank for h in run.units] == [1, 2]
    assert table["a"].sources == [(Q0, 1, FIRST, 1)] and table["a"].call == 1


def test_a_new_question_is_first_retrieved_by_its_text_and_translations_fused():
    ql, r, table = setup()
    r.round(ql, 1, 1, table)
    passed, issues = ql.check(PlanOutput((Judgement(Q0, (), "", False, "needs_lead", "derive"),),
                                         (NewQuestion(Q0, "CODE1 sub cdx", (("spanish", "sub-es dcy"),)),)), {"a", "b"}, ["spanish"])
    ql.apply(PlanOutput((Judgement(Q0, (), "", False, "needs_lead", "derive"),),
                        (NewQuestion(Q0, "CODE1 sub cdx", (("spanish", "sub-es dcy"),)),)), passed)
    new = r.round(ql, 2, 2, table)
    assert r.search.texts[-2:] == ["sub cdx", "sub-es dcy"]
    run = ql["Q1"].attempts[0].runs[0]
    assert new == ["c", "d"] and [h.unit_id for h in run.units] == ["c", "d"]   # c: 1st and 2nd; d: 2nd and 1st; tie by key
    assert run.units[0].ranks == ((0, 1), (1, 2))


def retry_q0(ql, r, table, handed):
    out = PlanOutput((Judgement(Q0, (), "", False, "not_found", "retry", fallback=FALLBACK),))
    passed, issues = ql.check(out, handed, ["spanish"])
    assert issues == []
    ql.apply(out, passed)


def test_a_fallback_attempt_runs_every_mode_of_its_group_and_skips_units_handed_before():
    ql, r, table = setup()
    r.round(ql, 1, 1, table)                        # a, b handed
    retry_q0(ql, r, table, {"a", "b"})
    new = r.round(ql, 2, 2, table)
    runs = {run.mode: [h.unit_id for h in run.units] for run in ql[Q0].attempts[1].runs}
    assert runs == {"continue": ["c", "d"], "rewrite": ["c", "f"], "hyde": ["h", "g"], "step_back": ["i", "j"],
                    "translation": ["k"]}            # translation: a is skipped, b too; only k left
    # handed once each, in order of question, mode, rank; c came from two modes
    assert new == ["c", "d", "f", "h", "g", "i", "j", "k"]
    assert [s[2] for s in table["c"].sources] == ["continue", "rewrite"]


def test_continue_goes_on_from_where_the_first_take_stopped():
    ql, r, table = setup(k=2, schedule=Schedule((("rewrite",), ("continue",))))
    r.round(ql, 1, 1, table)                       # a, b; cursor 2
    for n in (2, 3):
        out = PlanOutput((Judgement(Q0, (), "", False, "not_found", "retry", fallback=FALLBACK),))
        passed, issues = ql.check(out, table.handed(), ["spanish"])
        assert issues == []
        ql.apply(out, passed)
        r.round(ql, n, n, table)
    assert [h.unit_id for h in ql[Q0].attempts[1].runs[0].units] == ["c", "f"]   # rewrite
    run = ql[Q0].attempts[2].runs[0]
    assert run.mode == "continue" and [h.unit_id for h in run.units] == ["d", "e"] and run.cursor == 5   # c skipped


def test_a_mode_without_texts_is_skipped():
    ql, r, table = setup()
    r.round(ql, 1, 1, table)
    retry_q0(ql, r, table, {"a", "b"})
    ql[Q0].fallback.pop("translation")
    r.round(ql, 2, 2, table)
    assert [run.mode for run in ql[Q0].attempts[1].runs] == ["continue", "rewrite", "hyde", "step_back"]
