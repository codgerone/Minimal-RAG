import json

from rag.query.planning import Planner, check_decomposition, check_phrasings


def phrased(own="chinese", translations=("spanish",), hyde=("spanish",), **over):
    data = {"question_language": own, "rewrite": "r", "step_back": "s",
            "translations": [{"language": l, "text": "t"} for l in translations],
            "hyde": [{"language": l, "text": "h"} for l in hyde]}
    data.update(over)
    return json.dumps(data)


def test_phrasings_start_with_the_question_and_cover_every_kind():
    q, issues = check_phrasings(phrased(), "Q", ["spanish"])
    assert issues == [] and q.text == "Q"
    assert [p.kind for p in q.phrasings] == ["question", "rewrite", "translation", "hyde", "step_back"]


def test_no_translation_into_the_language_of_the_question_but_hyde_in_every_document_language():
    _, issues = check_phrasings(phrased("english", translations=(), hyde=("english", "spanish")),
                                "Q", ["english", "spanish"])
    assert any("translations" in i for i in issues)          # spanish translation missing
    q, issues = check_phrasings(phrased("english", translations=("spanish",), hyde=("english", "spanish")),
                                "Q", ["english", "spanish"])
    assert issues == [] and [p.language for p in q.of({"translation"})] == ["spanish"]
    _, issues = check_phrasings(phrased("spanish", translations=("spanish",)), "Q", ["spanish"])
    assert any("translations" in i for i in issues)


def test_decomposition_needs_at_least_one_non_empty_question():
    assert check_decomposition(json.dumps({"questions": ["a", "b"]})) == (["a", "b"], [])
    assert check_decomposition(json.dumps({"questions": []}))[1]
    assert check_decomposition("not json")[1]


class FakeLLM:
    model = "m"

    def __init__(self, answers):
        self.answers, self.calls = list(answers), 0

    def complete_structured(self, messages, name, schema, effort):
        self.calls += 1
        text = self.answers.pop(0)
        return type("C", (), {"text": text, "reasoning": None, "prompt_tokens": 1, "completion_tokens": 1,
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


def test_decomposed_questions_are_phrased_one_call_each():
    llm = FakeLLM([json.dumps({"questions": ["a", "b"]}), phrased(), phrased()])
    plan = Planner(llm, None).plan("Q", ["spanish"], True)
    assert [q.text for q in plan.questions] == ["a", "b"] and plan.cost.calls == 3 and plan.degraded is None


def test_failed_checks_are_retried_once_then_fall_back_to_the_question_itself():
    plan = Planner(FakeLLM(["{}", "{}", phrased()]), None).plan("Q", ["spanish"], True)
    assert plan.degraded and [q.text for q in plan.questions] == ["Q"] and plan.questions[0].degraded is None
    plan = Planner(FakeLLM(["{}", "{}"]), None).plan("Q", ["spanish"], False)
    assert plan.questions[0].degraded and [p.kind for p in plan.questions[0].phrasings] == ["question"]


def test_a_question_is_phrased_once_and_independent_generations_are_cached_apart():
    cache = MemoryCache()
    llm = FakeLLM([phrased(), json.dumps({"questions": ["Q"]}), phrased()])
    planner = Planner(llm, cache)
    planner.plan("Q", ["spanish"], False)
    planner.plan("Q", ["spanish"], True)        # decomposes to the same question: phrasing cached
    assert llm.calls == 2
    planner.plan("Q", ["spanish"], False, generation=1)
    assert llm.calls == 3
