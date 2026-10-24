import json

from rag.query.planning import Planner, check


def sub(question="q", langs=("spanish",), **over):
    item = {"question": question, "rewrite": "r", "step_back": "s",
            "translations": [{"language": l, "text": "t"} for l in langs],
            "hyde": [{"language": l, "text": "h"} for l in langs]}
    item.update(over)
    return item


def test_a_valid_plan_gives_every_phrasing_and_the_question_first():
    subs, issues = check(json.dumps({"sub_questions": [sub("a"), sub("b")]}), "Q", ["spanish"], True)
    assert issues == [] and [s.question for s in subs] == ["a", "b"]
    assert [p.kind for p in subs[0].phrasings] == ["question", "rewrite", "translation", "hyde", "step_back"]
    assert {p.kind for p in subs[0].of({"translation", "hyde"})} == {"translation", "hyde"}


def test_without_decomposition_the_question_itself_is_the_only_sub_question():
    subs, issues = check(json.dumps({"sub_questions": [sub("echo")]}), "Q", ["spanish"], False)
    assert issues == [] and subs[0].question == "Q" and subs[0].phrasings[0].text == "Q"
    _, issues = check(json.dumps({"sub_questions": [sub(), sub()]}), "Q", ["spanish"], False)
    assert any("only sub-question" in i for i in issues)


def test_each_document_language_needs_one_translation_and_one_hyde():
    _, issues = check(json.dumps({"sub_questions": [sub(langs=("spanish",))]}), "Q", ["english", "spanish"], True)
    assert any("translations" in i for i in issues) and any("hyde" in i for i in issues)
    _, issues = check("not json", "Q", ["spanish"], True)
    assert issues


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


def test_a_failed_check_is_retried_once_then_the_plan_degrades_to_the_question():
    good = json.dumps({"sub_questions": [sub("a")]})
    plan = Planner(FakeLLM(["{}", good]), None).plan("Q", [("D", "spanish")], True)
    assert plan.degraded is None and plan.cost.calls == 2 and plan.sub_questions[0].question == "a"
    plan = Planner(FakeLLM(["{}", "{}"]), None).plan("Q", [("D", "spanish")], True)
    assert plan.degraded and [s.question for s in plan.sub_questions] == ["Q"]
    assert [p.kind for p in plan.sub_questions[0].phrasings] == ["question"]


def test_plans_are_cached_and_independent_generations_are_cached_apart():
    good = json.dumps({"sub_questions": [sub("a")]})
    llm, cache = FakeLLM([good, good]), MemoryCache()
    planner = Planner(llm, cache)
    planner.plan("Q", [("D", "spanish")], True)
    again = planner.plan("Q", [("D", "spanish")], True)
    assert llm.calls == 1 and again.cost.cached and again.cost.cost_usd == 0
    planner.plan("Q", [("D", "spanish")], True, generation=1)
    assert llm.calls == 2
