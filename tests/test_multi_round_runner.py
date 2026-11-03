import json

from rag.query.multi_round.planner import Caller, parse_plan, plan_input
from rag.query.multi_round.runner import answer_at, run
from rag.query.multi_round.state import Q0, Status
from test_multi_round_retrieval import LetterSearch

EMPTY_RETRY = {"rewrite": [], "hyde": [], "step_back": [], "translation": []}
RETRY = {"rewrite": [{"language": "question", "text": "rw cfg"}], "hyde": [{"language": "spanish", "text": "hy hgb"}],
         "step_back": [{"language": "question", "text": "sb ijb"}], "translation": [{"language": "spanish", "text": "tr kab"}]}


def judge(qid, answerable=False, action="", accept=(), gap="not_found", retry=None, reason=""):
    return {"id": qid, "accept": list(accept), "note": f"note {qid}", "answerable": answerable,
            "gap": "" if answerable else gap, "action": "" if answerable else action, "reason": reason,
            "retry": retry or EMPTY_RETRY}


def plan(*judgements, new=(), answer="", withdrawals=()):
    return json.dumps({"judgements": list(judgements), "new_questions": list(new), "withdrawals": list(withdrawals),
                       "answer": answer})


class ScriptLLM:
    model = "m"

    def __init__(self, texts):
        self.texts, self.seen = list(texts), []

    def complete_structured(self, messages, name, schema, effort):
        self.seen.append((name, messages))
        return type("C", (), {"text": self.texts.pop(0), "reasoning": None, "prompt_tokens": 10, "completion_tokens": 2,
                              "reasoning_tokens": 0, "cost_usd": 0.001})()


class MemoryCache:
    def __init__(self):
        self.store = {}

    def key(self, model, messages, schema, effort, version, **extra):
        return json.dumps([model, messages, version, extra], sort_keys=True)

    def get(self, key):
        return self.store.get(key)

    def put(self, key, value):
        self.store[key] = value


def go(texts, max_calls=5, cache=None, generation=0):
    llm = ScriptLLM(texts)
    record = run("CODE1 q abcdef", LetterSearch(), None, ["CODE1"], ["spanish"], Caller(llm, cache, generation),
                 max_calls=max_calls, k=2)
    return record, llm


SUB = {"parent": Q0, "text": "sub cdx", "translations": [{"language": "spanish", "text": "sub-es dcy"}]}


def test_a_run_ends_when_q0_is_answered_and_the_planning_call_answers():
    record, llm = go([plan(judge(Q0, action="derive", gap="needs_lead"), new=[SUB]),
                      plan(judge(Q0, True, accept=["a", "c"]), judge("Q1", True, accept=["c"]), answer="the answer")])
    assert record.termination == "answered" and record.answer == "the answer"
    assert [c.kind for c in record.calls] == ["plan", "plan"] and record.new_units == [["a", "b"], ["c", "d"]]
    assert record.questions[Q0].evidence == ["a", "c"] and record.table["c"].accepted_by == [Q0, "Q1"]


def test_the_call_before_the_limit_is_followed_by_the_answering_call():
    record, llm = go([plan(judge(Q0, action="retry", retry=RETRY)), json.dumps({"answer": "partial answer"})], max_calls=2)
    assert record.termination == "limit" and record.answer == "partial answer"
    assert [c.kind for c in record.calls] == ["plan", "answer"]
    assert record.questions[Q0].status == Status.PENDING and len(record.questions[Q0].attempts) == 1   # no retrieval after the limit


def test_nothing_pending_goes_straight_to_the_answering_call():
    two = [SUB, {"parent": Q0, "text": "other xy", "translations": []}]
    record, llm = go([plan(judge(Q0, action="derive", gap="partial"), new=two),
                      plan(judge(Q0, action="wait"), judge("Q1", action="withdraw", reason="r"), judge("Q2", action="withdraw", reason="r")),
                      json.dumps({"answer": "not found"})])
    assert record.termination == "nothing_pending" and [c.kind for c in record.calls] == ["plan", "plan", "answer"]


def test_a_failed_check_is_retried_once_with_the_issues_then_the_call_is_degraded():
    record, llm = go([plan(judge(Q0, True)), plan(judge(Q0, action="retry", retry=RETRY)),
                      json.dumps({"answer": "x"})], max_calls=2)
    call = record.calls[0]
    assert len(call.texts) == 2 and call.degraded == [] and "write the answer" in llm.seen[1][1][-1]["content"]
    record, llm = go(["not json", plan(judge(Q0, True)), json.dumps({"answer": "x"})], max_calls=2)
    assert record.calls[0].degraded and record.questions[Q0].status == Status.JUDGING


def test_the_input_holds_the_question_list_the_evidence_the_new_material_and_who_returned_it():
    record, llm = go([plan(judge(Q0, action="derive", gap="needs_lead", accept=["a"]), new=[SUB]),
                      plan(judge(Q0, True, accept=["a"]), judge("Q1", True), answer="ans")])
    second = json.loads(llm.seen[1][1][1]["content"])
    assert second["scope"] == {"documents": [], "identifier_codes": []}
    first = json.loads(llm.seen[0][1][1]["content"])
    assert first["questions"][0]["retry_texts"] == ["rewrite", "hyde", "step_back", "translation"]
    assert second["questions"][1]["retry_texts"] == ["rewrite", "hyde", "step_back"]   # translation is Q0's alone
    assert [q["id"] for q in second["questions"]] == [Q0, "Q1"] and second["questions"][0]["note"] == "note Q0"
    assert [e["id"] for e in second["evidence"]] == ["a"] and second["evidence"][0]["accepted_by"] == [Q0]
    assert [m["id"] for m in second["new_material"]] == ["c", "d"] and second["new_material"][0]["returned_by"] == ["Q1 first (rank 1)"]
    assert second["to_judge"] == [Q0, "Q1"] and "b" not in [e["id"] for e in second["evidence"]]   # handed but not accepted: gone
    assert "spanish" in llm.seen[1][1][0]["content"]


def test_a_lower_limit_is_cut_from_the_run_and_answered_from_the_state_after_its_last_planning_call():
    texts = [plan(judge(Q0, action="retry", retry=RETRY)), plan(judge(Q0, action="derive", gap="partial"), new=[SUB]),
             plan(judge(Q0, True, accept=["a"]), judge("Q1", True), answer="full")]
    record, _ = go(texts)
    assert record.termination == "answered" and len(record.calls) == 3
    llm = ScriptLLM([json.dumps({"answer": "at two"}), json.dumps({"answer": "at three"})])
    caller = Caller(llm, None)
    text, call, why = answer_at(record, 2, caller)
    assert (text, why) == ("at two", "limit")
    assert json.loads(llm.seen[0][1][1]["content"])["questions"][0]["status"] == "pending"   # after call 1, before round 2
    text, call, why = answer_at(record, 3, caller)
    assert (text, why) == ("at three", "limit") and call is not None
    assert answer_at(record, 4, caller) == ("full", None, "answered")


def test_calls_are_cached_and_generations_apart():
    texts = [plan(judge(Q0, action="retry", retry=RETRY)), json.dumps({"answer": "x"})]
    cache = MemoryCache()
    go(list(texts), max_calls=2, cache=cache)
    record, llm = go([], max_calls=2, cache=cache)
    assert llm.seen == [] and all(c.cached for c in record.calls)
    record, llm = go(list(texts), max_calls=2, cache=cache, generation=1)
    assert len(llm.seen) == 2


def test_parse_plan_maps_empty_strings_to_none_and_keeps_only_given_retry_texts():
    out = parse_plan(plan(judge(Q0, True), judge("Q1", action="retry", retry={**EMPTY_RETRY, "hyde": RETRY["hyde"]})))
    assert out.judgements[0].gap is None and out.judgements[0].action is None
    assert out.judgements[1].fallback == {"hyde": (("spanish", "hy hgb"),)}
    assert parse_plan("{}") is None
