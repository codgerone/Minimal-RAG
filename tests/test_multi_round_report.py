from rag.reports.multi_round import at_limit, completion

GROUPS = [{"id": "e1", "item": "a", "sets": [["c1"], ["c9"]]}, {"id": "e2", "item": "b", "sets": [["c2", "c3"]]}]


def test_completion_takes_best_set_and_last_chunk_of_a_set():
    assert completion([["c9"], ["c2"], ["c1", "c3"]], GROUPS) == [1, 3]
    assert completion([["c2"]], GROUPS) == [None, None]


def unit(call, chunks, tokens=10, sources=(("Q0", 1, "first", 1),)):
    return {"call": call, "chunks": chunks, "tokens": tokens, "sources": [list(s) for s in sources]}


def q0(status, evidence):
    return [{"id": "Q0", "parent": None, "text": "?", "status": status, "abandon_reason": None, "evidence": evidence}]


RUN = {
    "plan_calls": 3, "termination": "answered", "answer": "full",
    "order": ["u1", "u2", "u3"],
    "units": {"u1": unit(1, ["c1"]), "u2": unit(2, ["c2"], sources=(("Q1", 1, "first", 1),)),
              "u3": unit(3, ["c3"], sources=(("Q0", 2, "rewrite", 1),))},
    "rounds": [[1, 0.1], [2, 0.1], [3, 0.1]],
    "calls": [{"kind": "plan", "list": q0("judging", ["u1"]), "seconds": 1, "cost_usd": 0.01, "prompt_tokens": 100, "output": "{}"},
              {"kind": "plan", "list": q0("waiting", ["u1", "u2"]), "seconds": 1, "cost_usd": 0.01, "prompt_tokens": 100, "output": "{}"},
              {"kind": "plan", "list": q0("answered", ["u1", "u2", "u3"]), "seconds": 1, "cost_usd": 0.01, "prompt_tokens": 100, "output": "{}"}],
    "limits": {"2": {"answer": "a2", "termination": "limit", "call": {"kind": "answer", "seconds": 1, "cost_usd": 0.001, "prompt_tokens": 50}},
               "3": {"answer": "a3", "termination": "limit", "call": {"kind": "answer", "seconds": 1, "cost_usd": 0.001, "prompt_tokens": 50}},
               "4": {"answer": "full", "termination": "answered", "call": None}},
}
CASE = {"answerable": True, "groups": GROUPS}
BASE = {"default": {"n": [1, 5], "u": [[["c1"], 10], [["x"], 10], [["y"], 10]]}}


def test_lower_limit_is_cut_before_planning_call_limit():
    f = at_limit(CASE, RUN, 3, BASE)   # planning calls 1–2, then the answering call for limit 3
    assert f["seq"] == ["u1", "u2"] and f["n"] == [1, None]
    assert not f["answered"] and f["result"] == "unanswered" and f["answer"] == "a3"
    assert f["plan"] == 2 and f["answer_calls"] == 1 and abs(f["cost"] - 0.021) < 1e-9
    assert f["retrievals"] == 3   # rounds 1 and 2


def test_full_run_and_baseline_cut_at_the_same_tokens():
    f = at_limit(CASE, RUN, 4, BASE)
    assert f["seq"] == ["u1", "u2", "u3"] and f["n"] == [1, 3] and f["round"] == [1, 3]
    assert f["result"] == "complete" and f["termination"] == "answered"
    vs = f["vs"]["default"]
    assert vs["prefix"] == 3 and vs["base_cov"] == [True, False] and vs["verdict"] == "better"
    assert f["modes"]["rewrite"]["completed"] == {"e2"} and f["modes"]["first_q0"]["completed"] == {"e1"}
