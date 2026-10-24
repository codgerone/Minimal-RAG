import json

from rag.query.planning import (Planner, check_decomposition, check_phrasings, phrase_messages,
                                question_list)


def entry(qid, translations=("spanish",), hyde=("spanish",), **over):
    e = {"id": qid, "rewrite": "r", "step_back": "s",
         "translations": [{"language": l, "text": "t"} for l in translations],
         "hyde": [{"language": l, "text": "h"} for l in hyde]}
    e.update(over)
    return e


def answer(*entries):
    return json.dumps({"phrasings": list(entries)})


def test_the_question_list_is_the_same_structure_with_or_without_decomposition():
    assert question_list(["Q"]) == [{"id": "Q1", "text": "Q"}]
    assert question_list(["a", "b"]) == [{"id": "Q1", "text": "a"}, {"id": "Q2", "text": "b"}]
    system, user = phrase_messages(question_list(["a"]), ["english", "spanish"], ["C1"])
    assert "written in: english, spanish." in system["content"] and "C1" in system["content"]
    assert json.loads(user["content"]) == {"questions": [{"id": "Q1", "text": "a"}]}
    assert "identify the documents" not in phrase_messages(question_list(["a"]), ["spanish"], [])[0]["content"]


def test_phrasings_are_matched_by_id_and_start_with_the_question():
    listed = question_list(["a", "b"])
    qs, issues = check_phrasings(answer(entry("Q2"), entry("Q1")), listed, ["spanish"])
    assert issues == [] and [q.text for q in qs] == ["a", "b"]
    assert [p.kind for p in qs[0].phrasings] == ["question", "rewrite", "translation", "hyde", "step_back"]
    _, issues = check_phrasings(answer(entry("Q1")), listed, ["spanish"])
    assert any("each id" in i for i in issues)


def test_a_translation_may_be_skipped_but_every_language_needs_a_hyde_passage():
    listed = question_list(["a"])
    qs, issues = check_phrasings(answer(entry("Q1", translations=())), listed, ["spanish"])
    assert issues == [] and qs[0].of({"translation"}) == []
    _, issues = check_phrasings(answer(entry("Q1", hyde=())), listed, ["spanish"])
    assert any("hyde" in i for i in issues)


def test_decomposition_needs_at_least_one_non_empty_question():
    assert check_decomposition(json.dumps({"questions": ["a", "b"]})) == (["a", "b"], [])
    assert check_decomposition(json.dumps({"questions": []}))[1]
    assert check_decomposition("not json")[1]


class FakeLLM:
    model = "m"

    def __init__(self, answers):
        self.answers, self.calls, self.seen = list(answers), 0, []

    def complete_structured(self, messages, name, schema, effort):
        self.calls += 1
        self.seen.append(messages)
        return type("C", (), {"text": self.answers.pop(0), "reasoning": None, "prompt_tokens": 1,
                              "completion_tokens": 1, "reasoning_tokens": 0, "cost_usd": 0.001})()


class MemoryCache:
    def __init__(self):
        self.store = {}

    def key(self, model, messages, schema, effort, version, **extra):
        return json.dumps([model, messages, version, extra], sort_keys=True)

    def get(self, key):
        return self.store.get(key)

    def put(self, key, value):
        self.store[key] = value


def test_decomposed_questions_go_to_one_phrasing_call_as_the_question_list():
    llm = FakeLLM([json.dumps({"questions": ["a", "b"]}), answer(entry("Q1"), entry("Q2"))])
    plan = Planner(llm, None).plan("Q", ["spanish"], True)
    assert [q.text for q in plan.questions] == ["a", "b"] and plan.cost.calls == 2 and plan.degraded == ()
    assert json.loads(llm.seen[1][1]["content"])["questions"] == question_list(["a", "b"])


def test_failed_checks_are_retried_once_then_fall_back():
    plan = Planner(FakeLLM(["{}", "{}", answer(entry("Q1"))]), None).plan("Q", ["spanish"], True)
    assert plan.degraded[0].startswith("decomposition") and [q.text for q in plan.questions] == ["Q"]
    plan = Planner(FakeLLM(["{}", "{}"]), None).plan("Q", ["spanish"], False)
    assert plan.degraded[0].startswith("phrasings")
    assert [p.kind for p in plan.questions[0].phrasings] == ["question"]


def test_plans_are_cached_and_independent_generations_are_cached_apart():
    llm = FakeLLM([answer(entry("Q1")), answer(entry("Q1"))])
    planner = Planner(llm, MemoryCache())
    planner.plan("Q", ["spanish"], False)
    planner.plan("Q", ["spanish"], False)
    assert llm.calls == 1
    planner.plan("Q", ["spanish"], False, generation=1)
    assert llm.calls == 2
