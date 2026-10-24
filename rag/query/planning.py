"""Query planning: one LLM call writes the sub-questions of a question and every phrasing of each.

Two plans per question: with decomposition (P3.1 on) the question is split into sub-questions,
one information item each; without it the question itself is the only sub-question. Either way
each sub-question gets the same phrasings: rewrite, one translation and one HyDE passage per
document language in scope, and a step-back question. Every phrasing is written from the
sub-question itself, so the phrasings are independent of each other. Rules: docs/changes/v3.14.md.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

PROMPT_VERSION = "query_plan_v2"
REASONING_EFFORT = "medium"
MAX_SUB_QUESTIONS = 6

SYSTEM_PROMPT = """You plan retrieval queries for a question about business documents (contracts, purchase orders, invoices). The question is answered from passages retrieved from the documents listed in the input; you never answer it.

For each sub-question write these phrasings, each written from the sub-question itself (none is derived from another phrasing):
- question: the sub-question as a full sentence in the language of the user's question.
- rewrite: the sub-question in the language of the user's question, as a statement or noun phrase: drop document identifier codes (contract, order and invoice numbers), question words and filler; keep entity names, model numbers, company names, numbers and terms quoted from the documents verbatim.
- translations: the question field translated into each listed document language as a question, using the terms such documents use; keep entity names, model numbers, company names and numbers untranslated; drop document identifier codes.
- hyde: for each listed document language, a short passage (one or two sentences, or one table row) written as the answer would appear in such a document, in that language and the document's style; write unknown numbers, amounts and dates as placeholders such as [NUMBER], [AMOUNT], [DATE]; keep entity names from the question verbatim.
- step_back: in the language of the user's question, a broader but still concrete question about the same kind of document (contract, purchase order or invoice, as listed): drop the qualifiers that do not change where the answer is written (for a specific company, product or batch, ask about all of them); never ask what such documents usually contain.

Write one translation and one hyde passage for every listed document language, each language once."""

DECOMPOSE_PROMPT = """Split the question into sub-questions:
- one sub-question per information item the question explicitly asks for; every item listed with "and", "、" or "和" is a separate item (unit price and quantity are two items; delivery dates and the products delivered in each batch are two items); a question asking for one thing gives one sub-question;
- each sub-question stands alone: repeat every qualifier of the question that applies to it (company, product, batch, document) word for word;
- ask for facts that can be read from the documents, not for conclusions computed from them (for a total, ask for the total as written; do not ask for the parts to add up);
- do not add sub-questions for premises the question does not ask about;
- at most """ + str(MAX_SUB_QUESTIONS) + """ sub-questions."""

WHOLE_PROMPT = """Do not split the question: it is the only sub-question, and its question field repeats the user's question."""

RETRY_TEMPLATE = """Your answer did not pass these checks:
{issues}
Answer again with the whole plan."""


@dataclass(frozen=True)
class Phrasing:
    kind: str        # question | rewrite | translation | hyde | step_back
    text: str
    language: str = ""   # document language, for translation and hyde


@dataclass(frozen=True)
class SubQuestion:
    question: str
    phrasings: tuple[Phrasing, ...]   # the question itself first

    def of(self, kinds: set[str]) -> list[Phrasing]:
        return [p for p in self.phrasings if p.kind in kinds]


@dataclass(frozen=True)
class PlanCost:
    calls: int
    seconds: float
    cost_usd: float
    cached: bool


@dataclass(frozen=True)
class Plan:
    question: str
    decomposed: bool
    sub_questions: tuple[SubQuestion, ...]
    cost: PlanCost
    degraded: str | None = None   # why the plan fell back to the question alone
    attempts: tuple[dict[str, Any], ...] = field(default=(), compare=False)


class StructuredLLM(Protocol):
    model: str

    def complete_structured(self, messages: list[dict[str, str]], name: str,
                            schema: dict[str, Any], reasoning_effort: str) -> Any: ...


def _phrasings_schema(languages: list[str]) -> dict[str, Any]:
    by_language = {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["language", "text"],
        "properties": {"language": {"type": "string", "enum": languages}, "text": {"type": "string"}}}}
    return {"type": "object", "additionalProperties": False,
            "required": ["question", "rewrite", "translations", "hyde", "step_back"],
            "properties": {"question": {"type": "string"}, "rewrite": {"type": "string"},
                           "translations": by_language, "hyde": by_language, "step_back": {"type": "string"}}}


def schema(languages: list[str]) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "required": ["sub_questions"],
            "properties": {"sub_questions": {"type": "array", "items": _phrasings_schema(languages)}}}


def messages(question: str, documents: list[tuple[str, str]], decompose: bool) -> list[dict[str, str]]:
    """`documents`: (name, language) of each document in scope."""
    listed = "\n".join(f"- {name} (language: {language})" for name, language in documents)
    languages = ", ".join(sorted({language for _, language in documents}))
    return [{"role": "system", "content": SYSTEM_PROMPT + "\n\n" + (DECOMPOSE_PROMPT if decompose else WHOLE_PROMPT)},
            {"role": "user", "content": f"Documents:\n{listed}\nDocument languages: {languages}\n\nQuestion: {question}"}]


def check(raw: str, question: str, languages: list[str], decompose: bool) -> tuple[tuple[SubQuestion, ...], list[str]]:
    """The sub-questions, and the checks the answer fails (empty when it passes)."""
    try:
        data = json.loads(raw)
        items = data["sub_questions"]
    except (ValueError, KeyError, TypeError):
        return (), ["the answer is not the JSON object of the schema"]
    issues = []
    if not items:
        issues.append("there is no sub-question")
    if decompose and len(items) > MAX_SUB_QUESTIONS:
        issues.append(f"more than {MAX_SUB_QUESTIONS} sub-questions")
    if not decompose and len(items) != 1:
        issues.append("the question must be the only sub-question")
    subs = []
    for n, item in enumerate(items, start=1):
        for name in ("question", "rewrite", "step_back"):
            if not str(item.get(name, "")).strip():
                issues.append(f"sub-question {n}: {name} is empty")
        for name in ("translations", "hyde"):
            got = [x["language"] for x in item.get(name, [])]
            if sorted(got) != sorted(languages) or any(not x["text"].strip() for x in item.get(name, [])):
                issues.append(f"sub-question {n}: {name} needs one non-empty text per language "
                              f"({', '.join(languages)}), got {', '.join(got) or 'none'}")
        text = question if not decompose else item.get("question", "").strip()
        subs.append(SubQuestion(text, (
            Phrasing("question", text),
            Phrasing("rewrite", item.get("rewrite", "").strip()),
            *(Phrasing("translation", x["text"].strip(), x["language"]) for x in item.get("translations", [])),
            *(Phrasing("hyde", x["text"].strip(), x["language"]) for x in item.get("hyde", [])),
            Phrasing("step_back", item.get("step_back", "").strip()))))
    return tuple(subs), issues


def fallback(question: str) -> tuple[SubQuestion, ...]:
    return (SubQuestion(question, (Phrasing("question", question),)),)


class Planner:
    def __init__(self, llm: StructuredLLM, cache: Any | None):
        self.llm = llm
        self.cache = cache   # rag.ingest.tables.llm_tables.LLMCache

    def plan(self, question: str, documents: list[tuple[str, str]], decompose: bool,
             generation: int = 0) -> Plan:
        """`generation` > 0: an independent generation, cached apart from generation 0."""
        languages = sorted({language for _, language in documents})
        sch = schema(languages)
        msgs = messages(question, documents, decompose)
        attempts: list[dict[str, Any]] = []
        for _ in range(2):
            hit = self._call(msgs, sch, generation)
            subs, issues = check(hit["text"], question, languages, decompose)
            attempts.append({**hit, "issues": issues})
            if not issues:
                break
            msgs = msgs + [{"role": "assistant", "content": hit["text"]},
                           {"role": "user", "content": RETRY_TEMPLATE.format(
                               issues="\n".join(f"- {i}" for i in issues))}]
        cost = PlanCost(len(attempts), sum(a.get("seconds") or 0 for a in attempts if not a["cached"]),
                        sum(a.get("cost_usd") or 0 for a in attempts if not a["cached"]),
                        all(a["cached"] for a in attempts))
        if attempts[-1]["issues"]:
            return Plan(question, decompose, fallback(question), cost,
                        "; ".join(attempts[-1]["issues"]), tuple(attempts))
        return Plan(question, decompose, subs, cost, None, tuple(attempts))

    def _call(self, msgs: list[dict[str, str]], sch: dict[str, Any], generation: int) -> dict[str, Any]:
        extra = {"generation": generation} if generation else {}
        key = (self.cache.key(self.llm.model, msgs, sch, REASONING_EFFORT, PROMPT_VERSION, **extra)
               if self.cache else "")
        hit = self.cache.get(key) if self.cache else None
        if hit is not None:
            return {**hit, "cached": True}
        started = time.perf_counter()
        done = self.llm.complete_structured(msgs, "query_plan", sch, REASONING_EFFORT)
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
