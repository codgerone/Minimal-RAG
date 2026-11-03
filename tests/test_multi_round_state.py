import pytest

from rag.query.multi_round.state import (FIRST, Q0, SCHEDULE_A, Attempt, Judgement, ModeRun, NewQuestion, PlanOutput,
                                          QuestionList, Schedule, Status, Withdrawal)

LANGS = ["spanish"]
TEXTS = {"rewrite": (("question", "r"), ("spanish", "r-es")), "hyde": (("spanish", "h"),),
         "step_back": (("question", "s"), ("spanish", "s-es")), "translation": (("spanish", "t"),)}


def retrieve(ql, *ids):
    for qid in ids:
        ql.retrieved(qid, Attempt(1, [ModeRun(FIRST, [ql[qid].text])]))


def run(ql, out, handed=frozenset({"u1", "u2"})):
    passed, issues = ql.check(out, set(handed), LANGS)
    made = ql.apply(out, passed)
    return passed, issues, made


def no(qid, action, **kw):
    return Judgement(qid, kw.pop("accept", ()), kw.pop("note", ""), False, kw.pop("gap", "not_found"), action, **kw)


def yes(qid, *accept):
    return Judgement(qid, accept, "found", True)


def started():
    ql = QuestionList("question")
    retrieve(ql, Q0)
    return ql


def with_child():
    """Q0 waiting on Q1, Q1 retrieved."""
    ql = started()
    run(ql, PlanOutput((no(Q0, "derive", gap="needs_lead"),), (NewQuestion(Q0, "sub", (("spanish", "sub-es"),)),)))
    retrieve(ql, "Q1")
    return ql


def test_a_run_starts_with_q0_pending_and_a_retrieved_question_is_judged_next():
    ql = QuestionList("question")
    assert [(q.id, q.status) for q in ql] == [(Q0, Status.PENDING)] and ql.to_judge() == []
    retrieve(ql, Q0)
    assert ql[Q0].status == Status.JUDGING and ql.to_judge() == [ql[Q0]]
    with pytest.raises(ValueError):
        retrieve(ql, Q0)


def test_answerable_needs_an_answer_for_q0_and_accepts_only_units_already_handed():
    ql = started()
    _, issues, _ = run(ql, PlanOutput((yes(Q0, "u1"),)))
    assert any("write the answer" in i for i in issues) and ql[Q0].status == Status.JUDGING
    _, issues, _ = run(ql, PlanOutput((yes(Q0, "u9"),), answer="a"))
    assert any("u9" in i for i in issues)
    _, issues, _ = run(ql, PlanOutput((yes(Q0, "u1", "u1"),), answer="a"))
    assert issues == [] and ql[Q0].status == Status.ANSWERED and ql[Q0].evidence == ["u1"]


def test_an_answer_is_only_written_when_q0_is_answerable():
    ql = started()
    _, issues, _ = run(ql, PlanOutput((no(Q0, "retry", fallback=TEXTS),), answer="a"))
    assert any("only when Q0 is answerable" in i for i in issues)


def test_retry_needs_the_fallback_texts_and_a_method_left():
    ql = started()
    _, issues, _ = run(ql, PlanOutput((no(Q0, "retry", fallback={**TEXTS, "rewrite": (("spanish", "r-es"),)}),)))
    assert any("rewrite" in i and "question's language" in i for i in issues)
    _, issues, _ = run(ql, PlanOutput((no(Q0, "retry", fallback={**TEXTS, "hyde": ()}),)))
    assert any("hyde" in i for i in issues)
    _, issues, _ = run(ql, PlanOutput((no(Q0, "retry", fallback=TEXTS),)))
    assert issues == [] and ql[Q0].status == Status.PENDING and ql[Q0].fallback["hyde"] == [("spanish", "h")]
    ql.retrieved(Q0, Attempt(2, [ModeRun("rewrite", ["r"])]))
    _, issues, _ = run(ql, PlanOutput((no(Q0, "retry", fallback=TEXTS),)))
    assert any("no retrieval method left" in i for i in issues)   # arrangement A: two attempts


def test_the_schedule_gives_each_attempt_its_modes_and_q0_alone_the_translation():
    assert SCHEDULE_A.max_attempts == 2 and SCHEDULE_A.modes(1, "Q3") == (FIRST,)
    assert "translation" in SCHEDULE_A.modes(2, Q0) and "translation" not in SCHEDULE_A.modes(2, "Q3")
    b = Schedule((("continue", "rewrite"), ("hyde", "step_back", "translation")))
    assert b.max_attempts == 3 and b.modes(3, "Q1") == ("hyde", "step_back")
    for bad in ((), (("continue",), ("continue",)), (("guess",),)):
        with pytest.raises(ValueError):
            Schedule(bad)


def test_derive_makes_pending_sub_questions_with_their_parent_and_the_parent_waits():
    ql = started()
    _, issues, _ = run(ql, PlanOutput((no(Q0, "derive", gap="needs_lead"),)))
    assert any("at least one new question" in i for i in issues)
    _, issues, made = run(ql, PlanOutput((no(Q0, "derive", gap="partial"),),
                                         (NewQuestion(Q0, "a"), NewQuestion(Q0, "b", (("spanish", "b-es"),)))))
    assert issues == [] and made == ["Q1", "Q2"] and ql[Q0].status == Status.WAITING
    assert [(q.parent_id, q.status) for q in ql.pending()] == [(Q0, Status.PENDING)] * 2
    assert ql["Q2"].translation == {"spanish": "b-es"}


def test_a_new_question_needs_a_parent_that_derives_and_valid_translations():
    ql = started()
    _, issues, _ = run(ql, PlanOutput((no(Q0, "retry", fallback=TEXTS),), (NewQuestion(Q0, "a"),)))
    assert any("does not derive" in i for i in issues)
    ql = started()
    _, issues, _ = run(ql, PlanOutput((no(Q0, "derive"),), (NewQuestion(Q0, "a", (("english", "x"),)),)))
    assert any("translations" in i for i in issues)


def test_every_judged_question_needs_exactly_one_judgement_and_a_gap_and_action_when_not_answerable():
    ql = with_child()   # Q0 waiting, Q1 judging: both are judged
    _, issues, _ = run(ql, PlanOutput((no("Q1", "wait"),)))
    assert any("Q0: needs a judgement" in i for i in issues)
    _, issues, _ = run(ql, PlanOutput((no(Q0, "wait"), no(Q0, "wait"), no("Q1", "retry", fallback=TEXTS))))
    assert any("more than one" in i for i in issues)
    ql = with_child()
    _, issues, _ = run(ql, PlanOutput((no(Q0, "wait"), Judgement("Q1", (), "", False, None, None))))
    assert any("needs a gap" in i for i in issues) and any("needs an action" in i for i in issues)


def test_wait_is_for_a_question_with_unfinished_sub_questions_whatever_its_state():
    ql = with_child()
    _, issues, _ = run(ql, PlanOutput((no(Q0, "wait"), no("Q1", "wait"))))
    assert any("Q1: wait" in i for i in issues) and not any("Q0: wait" in i for i in issues)
    assert ql[Q0].status == Status.WAITING and ql["Q1"].status == Status.JUDGING   # Q1's judgement failed: unchanged


def test_a_question_with_unfinished_sub_questions_may_retry_and_both_paths_go_on():
    ql = with_child()
    _, issues, _ = run(ql, PlanOutput((no(Q0, "retry", fallback=TEXTS), no("Q1", "retry", fallback=TEXTS))))
    assert issues == [] and [q.id for q in ql.pending()] == [Q0, "Q1"]


def test_withdraw_ends_the_question_and_its_unfinished_descendants_but_never_q0():
    ql = with_child()
    _, issues, made = run(ql, PlanOutput((no(Q0, "wait"), no("Q1", "derive")), (NewQuestion("Q1", "deeper"),)))
    assert made == ["Q2"]
    retrieve(ql, "Q2")
    _, issues, _ = run(ql, PlanOutput((no(Q0, "wait"), no("Q1", "withdraw"), no("Q2", "retry", fallback=TEXTS))))
    assert any("withdraw needs a reason" in i for i in issues)
    _, issues, _ = run(ql, PlanOutput((no(Q0, "withdraw", reason="x"), no("Q1", "wait"), no("Q2", "retry", fallback=TEXTS))))
    assert any("Q0 cannot be withdrawn" in i for i in issues)
    ql = with_child()
    run(ql, PlanOutput((no(Q0, "wait"), no("Q1", "derive")), (NewQuestion("Q1", "deeper"),)))
    retrieve(ql, "Q2")
    _, issues, _ = run(ql, PlanOutput((no(Q0, "wait"), no("Q1", "withdraw", reason="wrong plan"), no("Q2", "retry", fallback=TEXTS))))
    assert issues == []
    assert (ql["Q1"].status, ql["Q1"].abandon_reason) == (Status.ABANDONED, "withdrawn")
    assert (ql["Q2"].status, ql["Q2"].abandon_reason) == (Status.ABANDONED, "parent_withdrawn")


def test_a_pending_question_can_be_withdrawn_outside_the_judgements():
    ql = started()
    run(ql, PlanOutput((no(Q0, "derive"),), (NewQuestion(Q0, "a"), NewQuestion(Q0, "b"))))
    retrieve(ql, "Q1")   # Q2 stays pending
    _, issues, _ = run(ql, PlanOutput((no(Q0, "wait"), no("Q1", "retry", fallback=TEXTS)), withdrawals=(Withdrawal("Q1", "x"),)))
    assert any("Q1: only an unfinished question" in i for i in issues)
    ql = started()
    run(ql, PlanOutput((no(Q0, "derive"),), (NewQuestion(Q0, "a"), NewQuestion(Q0, "b"))))
    retrieve(ql, "Q1")
    _, issues, _ = run(ql, PlanOutput((no(Q0, "wait"), no("Q1", "retry", fallback=TEXTS)), withdrawals=(Withdrawal("Q2", "redundant"),)))
    assert issues == [] and ql["Q2"].abandon_reason == "withdrawn"


def test_an_answered_question_ends_its_unfinished_descendants_but_keeps_answered_ones():
    ql = started()
    run(ql, PlanOutput((no(Q0, "derive"),), (NewQuestion(Q0, "a"), NewQuestion(Q0, "b"))))
    retrieve(ql, "Q1", "Q2")
    _, issues, _ = run(ql, PlanOutput((yes(Q0, "u1"), yes("Q1", "u1"), no("Q2", "retry", fallback=TEXTS)), answer="ans"))
    assert issues == []
    assert ql["Q1"].status == Status.ANSWERED and (ql["Q2"].status, ql["Q2"].abandon_reason) == (Status.ABANDONED, "parent_answered")


def test_stop_ends_a_question_only_when_nothing_is_left_to_try():
    ql = started()
    _, issues, _ = run(ql, PlanOutput((no(Q0, "stop"),)))
    assert any("stop is only" in i for i in issues)
    run(ql, PlanOutput((no(Q0, "retry", fallback=TEXTS),)))
    ql.retrieved(Q0, Attempt(2, [ModeRun("rewrite", ["r"])]))
    _, issues, _ = run(ql, PlanOutput((no(Q0, "stop"),)))
    assert issues == [] and (ql[Q0].status, ql[Q0].abandon_reason) == (Status.ABANDONED, "exhausted")


def test_judgements_that_pass_are_carried_out_and_the_rest_keep_their_state():
    ql = started()
    run(ql, PlanOutput((no(Q0, "derive"),), (NewQuestion(Q0, "a"), NewQuestion(Q0, "b"))))
    retrieve(ql, "Q1", "Q2")
    passed, issues, _ = run(ql, PlanOutput((no(Q0, "wait"), yes("Q1", "u2"), no("Q2", "retry", fallback={}))))
    assert passed == {Q0, "Q1"} and any(i.startswith("Q2") for i in issues)
    assert ql["Q1"].status == Status.ANSWERED and ql["Q1"].evidence == ["u2"] and ql["Q2"].status == Status.JUDGING


def test_evidence_only_grows_and_the_note_is_rewritten():
    ql = with_child()
    run(ql, PlanOutput((no(Q0, "wait", accept=("u1",), note="first"), no("Q1", "retry", fallback=TEXTS))))
    run(ql, PlanOutput((no(Q0, "wait", accept=("u2", "u1"), note="second"),)), handed={"u1", "u2"})
    assert ql[Q0].evidence == ["u1", "u2"] and ql[Q0].note == "second"
