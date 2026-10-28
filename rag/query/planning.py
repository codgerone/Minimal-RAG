"""Query planning by LLM: decomposition (P3.1, optional), then phrasing (P3.2.x).

The two steps meet in one structure, the question list: with decomposition it is the questions
the decomposition call returns, without it the original question alone. The phrasing call takes
the question list (JSON, each question with an id) and returns the phrasings of every question,
by id. Retrieval then runs each enabled phrasing of each question (rag/query/execution.py).
Rules: docs/changes/v3.14.md.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

# Each step's prompt is versioned on its own; the phrasing prompt is unchanged since v7.
PROMPT_VERSIONS = {"decomposition": "query_plan_v9", "phrasings": "query_plan_v7"}
REASONING_EFFORT = "medium"

DECOMPOSE_SYSTEM = """You split a question into separate questions for document retrieval. You never answer the question.

Rules:
- Write one question per information item the question explicitly asks for. Items asked for together, joined by a conjunction or listed one after another, are separate items. When the question asks for something and then spells out what it should include, the spelled-out parts are the items, not the general request. A question asking for one item gives one question, besides any questions the next two rules call for.
- When the question refers to something by a description instead of naming it, and the answer can only be found once it is known which one it is, also write a question asking which one it is.
- Ask for what can be read from the documents, not for conclusions computed from it: when an item is such a conclusion, ask for the facts it is computed from.
- Each question must stand alone: repeat every qualifier of the original question that applies to it (the entities, conditions, time ranges and the document it refers to), word for word. A qualifier stated once and left out of later parts of the original question still applies to those parts: write it into every question it applies to. A shortened name in a later part of the original question stands for the full name given earlier: write the full name.
- Do not add questions about anything the original question neither asks for nor needs.
- Write the questions in the language of the original question.

Answer with the list of questions."""

DECOMPOSE_USER = "Question: {question}"

PHRASE_SYSTEM = """You write alternative phrasings of questions for document retrieval. You never answer the questions.

The documents searched are written in: {languages}.
{codes}
The input is a list of questions, each with an id. For each question write the following phrasings, each from the question itself (no phrasing is derived from another):
- rewrite: in the language of the question, the question as a statement or noun phrase without question words and filler; keep names, codes, numbers and quoted terms verbatim.
- translations: the question translated into each of {languages}, as a question, in the terms documents in that language use; skip a language the question is already written in; keep names, codes and numbers as written.
- hyde: for each of {languages}, a short passage in that language written as the answer would appear in such a document; write unknown values (numbers, amounts, dates, names not given in the question) as placeholders in square brackets; keep the names given in the question verbatim.
- step_back: in the language of the question, a broader but still concrete question about the same documents: drop the qualifiers that narrow the question to one instance (one entity, item or period) when the answer for every instance would be written in the same place; never ask for general knowledge or what such documents usually contain.

Answer with one entry per question, with its id."""

PHRASE_CODES = "These codes identify the documents searched; leave them out of every phrasing below: {codes}.\n"

RETRY_TEMPLATE = """Your answer did not pass these checks:
{issues}
Answer again in full."""


@dataclass(frozen=True)
class Phrasing:
    kind: str            # question | rewrite | translation | hyde | step_back
    text: str
    language: str = ""   # for translation and hyde


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    phrasings: tuple[Phrasing, ...]   # the question itself first

    def of(self, kinds: set[str]) -> list[Phrasing]:
        return [p for p in self.phrasings if p.kind in kinds]


@dataclass(frozen=True)
class PlanCost:
    calls: int
    seconds: float      # LLM time of the calls; a cached call counts the time it took when made
    cost_usd: float     # likewise

    def __add__(self, other: "PlanCost") -> "PlanCost":
        return PlanCost(self.calls + other.calls, self.seconds + other.seconds, self.cost_usd + other.cost_usd)


NO_COST = PlanCost(0, 0.0, 0.0)


@dataclass(frozen=True)
class Plan:
    question: str
    decomposed: bool
    questions: tuple[Question, ...]    # the question list with each question's phrasings
    decomposition: PlanCost            # the decomposition step (NO_COST without decomposition)
    phrasing: PlanCost                 # the phrasing step
    degraded: tuple[str, ...] = ()     # steps that fell back: why

    @property
    def cost(self) -> PlanCost:
        return self.decomposition + self.phrasing


class StructuredLLM(Protocol):
    model: str

    def complete_structured(self, messages: list[dict[str, str]], name: str,
                            schema: dict[str, Any], reasoning_effort: str) -> Any: ...


DECOMPOSE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["questions"],
                    "properties": {"questions": {"type": "array", "items": {"type": "string"}}}}


def phrase_schema(languages: list[str]) -> dict[str, Any]:
    by_language = {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["language", "text"],
        "properties": {"language": {"type": "string", "enum": languages}, "text": {"type": "string"}}}}
    entry = {"type": "object", "additionalProperties": False,
             "required": ["id", "rewrite", "translations", "hyde", "step_back"],
             "properties": {"id": {"type": "string"}, "rewrite": {"type": "string"},
                            "translations": by_language, "hyde": by_language, "step_back": {"type": "string"}}}
    return {"type": "object", "additionalProperties": False, "required": ["phrasings"],
            "properties": {"phrasings": {"type": "array", "items": entry}}}


def decompose_messages(question: str) -> list[dict[str, str]]:
    return [{"role": "system", "content": DECOMPOSE_SYSTEM},
            {"role": "user", "content": DECOMPOSE_USER.format(question=question)}]


def question_list(texts: list[str]) -> list[dict[str, str]]:
    """The structure handed from decomposition (or the original question) to phrasing."""
    return [{"id": f"Q{n}", "text": text} for n, text in enumerate(texts, start=1)]


def phrase_messages(questions: list[dict[str, str]], languages: list[str], codes: list[str]) -> list[dict[str, str]]:
    system = PHRASE_SYSTEM.format(languages=", ".join(languages),
                                  codes=PHRASE_CODES.format(codes=", ".join(codes)) if codes else "")
    return [{"role": "system", "content": system},
            {"role": "user", "content": json.dumps({"questions": questions}, ensure_ascii=False)}]


def check_decomposition(raw: str) -> tuple[list[str], list[str]]:
    """The questions, and the checks the answer fails (empty when it passes)."""
    try:
        questions = [str(q).strip() for q in json.loads(raw)["questions"]]
    except (ValueError, KeyError, TypeError):
        return [], ["the answer is not the JSON object of the schema"]
    issues = (["there is no question"] if not questions else []) + \
             (["a question is empty"] if any(not q for q in questions) else [])
    return questions, issues


def check_phrasings(raw: str, questions: list[dict[str, str]], languages: list[str]) -> tuple[list[Question], list[str]]:
    try:
        entries = {str(e["id"]): e for e in json.loads(raw)["phrasings"]}
    except (ValueError, KeyError, TypeError):
        return [], ["the answer is not the JSON object of the schema"]
    issues = []
    ids = [q["id"] for q in questions]
    if sorted(entries) != sorted(ids):
        issues.append(f"the answer needs one entry for each id: {', '.join(ids)}")
    result = []
    for q in questions:
        e = entries.get(q["id"])
        if e is None:
            continue
        for name in ("rewrite", "step_back"):
            if not str(e[name]).strip():
                issues.append(f"{q['id']}: {name} is empty")
        got = [x["language"] for x in e["translations"]]
        if len(set(got)) != len(got) or any(not x["text"].strip() for x in e["translations"]):
            issues.append(f"{q['id']}: translations need at most one non-empty text per language")
        got = [x["language"] for x in e["hyde"]]
        if sorted(got) != languages or any(not x["text"].strip() for x in e["hyde"]):
            issues.append(f"{q['id']}: hyde needs one non-empty passage for each of {', '.join(languages)}")
        result.append(Question(q["id"], q["text"], (
            Phrasing("question", q["text"]),
            Phrasing("rewrite", e["rewrite"].strip()),
            *(Phrasing("translation", x["text"].strip(), x["language"]) for x in e["translations"]),
            *(Phrasing("hyde", x["text"].strip(), x["language"]) for x in e["hyde"]),
            Phrasing("step_back", e["step_back"].strip()))))
    return result, issues


class Planner:
    def __init__(self, llm: StructuredLLM, cache: Any | None):
        self.llm = llm
        self.cache = cache   # rag.ingest.tables.llm_tables.LLMCache

    def plan(self, question: str, languages: list[str], decompose: bool, codes: list[str] = (),
             generation: int = 0) -> Plan:
        """`languages`: the languages of the documents in scope; `codes`: the identifier codes the
        scope was found by (rag/query/scope.py). `generation` > 0: an independent generation,
        cached apart from generation 0."""
        languages, codes = sorted(set(languages)), sorted(set(codes))
        degraded: list[str] = []
        texts = [question]
        decomposition = NO_COST
        if decompose:
            got, issues, made = self._ask(decompose_messages(question), "decomposition", DECOMPOSE_SCHEMA,
                                          check_decomposition, generation)
            decomposition = _cost(made)
            if issues:
                degraded.append("decomposition: " + "; ".join(issues))
            else:
                texts = list(dict.fromkeys(got))
        listed = question_list(texts)
        phrased, issues, made = self._ask(phrase_messages(listed, languages, codes), "phrasings",
                                          phrase_schema(languages),
                                          lambda raw: check_phrasings(raw, listed, languages), generation)
        if issues:
            degraded.append("phrasings: " + "; ".join(issues))
            phrased = [Question(q["id"], q["text"], (Phrasing("question", q["text"]),)) for q in listed]
        return Plan(question, decompose, tuple(phrased), decomposition, _cost(made), tuple(degraded))

    def _ask(self, msgs: list[dict[str, str]], name: str, sch: dict[str, Any],
             check: Callable[[str], tuple[Any, list[str]]], generation: int):
        """Calls, checks, retries once with the failed checks; returns (result, issues, calls)."""
        made = []
        for _ in range(2):
            hit = self._call(msgs, name, sch, generation)
            result, issues = check(hit["text"])
            made.append(hit)
            if not issues:
                break
            msgs = msgs + [{"role": "assistant", "content": hit["text"]},
                           {"role": "user", "content": RETRY_TEMPLATE.format(
                               issues="\n".join(f"- {i}" for i in issues))}]
        return result, issues, made

    def _call(self, msgs, name, sch, generation) -> dict[str, Any]:
        extra = {"generation": generation} if generation else {}
        key = (self.cache.key(self.llm.model, msgs, sch, REASONING_EFFORT, PROMPT_VERSIONS[name], **extra)
               if self.cache else "")
        hit = self.cache.get(key) if self.cache else None
        if hit is not None:
            return {**hit, "cached": True}
        started = time.perf_counter()
        done = self.llm.complete_structured(msgs, name, sch, REASONING_EFFORT)
        hit = {"text": done.text, "reasoning": done.reasoning, "prompt_tokens": done.prompt_tokens,
               "completion_tokens": done.completion_tokens, "reasoning_tokens": done.reasoning_tokens,
               "cost_usd": done.cost_usd, "seconds": round(time.perf_counter() - started, 2)}
        if self.cache:
            self.cache.put(key, hit)
        return {**hit, "cached": False}


def _cost(calls: list[dict[str, Any]]) -> PlanCost:
    """The calls' cost as recorded when each was made, cached or not."""
    return PlanCost(len(calls), sum(c.get("seconds") or 0 for c in calls), sum(c.get("cost_usd") or 0 for c in calls))


def document_languages(index_dir: Path) -> dict[str, str]:
    """document_id → language judged at ingest (the index's intermediate results)."""
    from rag.jsonio import read_json
    found = {}
    for path in sorted((index_dir / "documents").glob("*/parsed.json")):
        parsed = read_json(path)
        found[parsed["document_id"]] = parsed["language"]
    return found
