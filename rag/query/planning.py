"""Query planning by LLM, in two separate steps.

1. Decomposition (P3.1, optional): one call splits the question into questions, one information
   item each. Without it the question itself is the only question.
2. Phrasing (P3.2.x): one call per question writes its phrasings: rewrite, a translation into each
   document language that differs from the question's, a HyDE passage in each document language,
   and a step-back question. A phrasing depends only on the question text and the document
   languages, so the same question is phrased once (cached) whatever produced it.

Rules: docs/changes/v3.14.md.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

PROMPT_VERSION = "query_plan_v5"
REASONING_EFFORT = "medium"

DECOMPOSE_PROMPT = """You split a question into separate questions for document retrieval. You never answer the question.

Rules:
- Write one question per information item the question explicitly asks for. Items asked for together, joined by a conjunction or listed one after another, are separate items. When the question asks for something and then spells out what it should include, the spelled-out parts are the items, not the general request. A question asking for one item gives one question.
- Each question must stand alone: repeat every qualifier of the original question that applies to it (the entities, conditions, time ranges and the document it refers to), word for word. A qualifier stated once and left out of later parts of the original question still applies to those parts: write it into every question it applies to.
- Ask for what can be read from the documents, not for conclusions computed from it.
- Do not add questions about anything the original question does not ask for.
- Write the questions in the language of the original question.

The input is the original question. Answer with the list of questions."""

PHRASE_PROMPT = """You write alternative phrasings of a question for document retrieval. Passages are retrieved from documents written in the languages listed in the input; the input may also list codes that identify those documents. You never answer the question.

Write each phrasing from the question itself; none is derived from another phrasing:
- question_language: the language of the question, named in lowercase English.
- rewrite: in the language of the question, the question as a statement or noun phrase. Drop question words, filler and the listed document codes; keep names, other codes, numbers and quoted terms verbatim.
- translations: for each listed document language that differs from the language of the question, the question translated into that language as a question, in the terms documents in that language would use. Keep names, other codes and numbers as written; drop the listed document codes. Write no translation into the language the question is already in.
- hyde: for each listed document language, a short passage in that language written as the answer would appear in such a document. Write unknown values (numbers, amounts, dates, names not given in the question) as placeholders in square brackets; keep the names given in the question verbatim; leave out the listed document codes.
- step_back: in the language of the question, a broader but still concrete question about the same documents: drop the qualifiers that narrow the question to one instance (one entity, item or period) when the answer for every instance would be written in the same place. Leave out the listed document codes. Never ask for general knowledge or what such documents usually contain.

The input gives the document languages, the document codes when there are any, and the question."""

RETRY_TEMPLATE = """Your answer did not pass these checks:
{issues}
Answer again in full."""


@dataclass(frozen=True)
class Phrasing:
    kind: str            # question | rewrite | translation | hyde | step_back
    text: str
    language: str = ""   # target language, for translation and hyde


@dataclass(frozen=True)
class Question:
    text: str
    phrasings: tuple[Phrasing, ...]   # the question itself first
    degraded: str | None = None       # why only the question itself is used

    def of(self, kinds: set[str]) -> list[Phrasing]:
        return [p for p in self.phrasings if p.kind in kinds]


@dataclass(frozen=True)
class PlanCost:
    calls: int
    seconds: float      # LLM time of the calls made now (cached calls count 0)
    cost_usd: float


@dataclass(frozen=True)
class Plan:
    question: str
    decomposed: bool
    questions: tuple[Question, ...]
    cost: PlanCost
    degraded: str | None = None   # why decomposition fell back to the question itself


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
    return {"type": "object", "additionalProperties": False,
            "required": ["question_language", "rewrite", "translations", "hyde", "step_back"],
            "properties": {"question_language": {"type": "string"}, "rewrite": {"type": "string"},
                           "translations": by_language, "hyde": by_language, "step_back": {"type": "string"}}}


def decompose_messages(question: str) -> list[dict[str, str]]:
    return [{"role": "system", "content": DECOMPOSE_PROMPT},
            {"role": "user", "content": f"Question: {question}"}]


def phrase_messages(question: str, languages: list[str], codes: list[str]) -> list[dict[str, str]]:
    lines = [f"Document languages: {', '.join(languages)}"]
    if codes:
        lines.append(f"Document codes: {', '.join(codes)}")
    return [{"role": "system", "content": PHRASE_PROMPT},
            {"role": "user", "content": "\n".join(lines + [f"Question: {question}"])}]


def check_decomposition(raw: str) -> tuple[list[str], list[str]]:
    """The questions, and the checks the answer fails (empty when it passes)."""
    try:
        questions = [str(q).strip() for q in json.loads(raw)["questions"]]
    except (ValueError, KeyError, TypeError):
        return [], ["the answer is not the JSON object of the schema"]
    issues = (["there is no question"] if not questions else []) + \
             (["a question is empty"] if any(not q for q in questions) else [])
    return questions, issues


def check_phrasings(raw: str, question: str, languages: list[str]) -> tuple[Question, list[str]]:
    try:
        data = json.loads(raw)
        own = str(data["question_language"]).strip().lower()
        rewrite, step_back = str(data["rewrite"]).strip(), str(data["step_back"]).strip()
        translations, hyde = data["translations"], data["hyde"]
    except (ValueError, KeyError, TypeError):
        return Question(question, (Phrasing("question", question),)), ["the answer is not the JSON object of the schema"]
    issues = []
    for name, value in (("question_language", own), ("rewrite", rewrite), ("step_back", step_back)):
        if not value:
            issues.append(f"{name} is empty")
    wanted = sorted(l for l in languages if l != own)
    for name, items, expected in (("translations", translations, wanted), ("hyde", hyde, sorted(languages))):
        got = [x["language"] for x in items]
        if sorted(got) != expected or any(not x["text"].strip() for x in items):
            issues.append(f"{name} needs one non-empty text for each of: {', '.join(expected) or 'none'}; "
                          f"got {', '.join(got) or 'none'}")
    return Question(question, (
        Phrasing("question", question),
        Phrasing("rewrite", rewrite),
        *(Phrasing("translation", x["text"].strip(), x["language"]) for x in translations),
        *(Phrasing("hyde", x["text"].strip(), x["language"]) for x in hyde),
        Phrasing("step_back", step_back))), issues


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
        calls: list[dict[str, Any]] = []
        texts, degraded = [question], None
        if decompose:
            got, issues, made = self._ask(decompose_messages(question), "decomposition", DECOMPOSE_SCHEMA,
                                          check_decomposition, generation)
            calls += made
            if issues:
                degraded = "; ".join(issues)
            else:
                texts = list(dict.fromkeys(got))
        questions = []
        for text in texts:
            phrased, issues, made = self._ask(phrase_messages(text, languages, codes), "phrasings",
                                              phrase_schema(languages),
                                              lambda raw, t=text: check_phrasings(raw, t, languages), generation)
            calls += made
            questions.append(phrased if not issues else
                             Question(text, (Phrasing("question", text),), "; ".join(issues)))
        fresh = [c for c in calls if not c["cached"]]
        cost = PlanCost(len(calls), sum(c.get("seconds") or 0 for c in fresh),
                        sum(c.get("cost_usd") or 0 for c in fresh))
        return Plan(question, decompose, tuple(questions), cost, degraded)

    def _ask(self, msgs, name, sch, check, generation):
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
        key = (self.cache.key(self.llm.model, msgs, sch, REASONING_EFFORT, PROMPT_VERSION, **extra)
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


def document_languages(index_dir: Path) -> dict[str, str]:
    """document_id → language judged at ingest (the index's intermediate results)."""
    from rag.jsonio import read_json
    found = {}
    for path in sorted((index_dir / "documents").glob("*/parsed.json")):
        parsed = read_json(path)
        found[parsed["document_id"]] = parsed["language"]
    return found
