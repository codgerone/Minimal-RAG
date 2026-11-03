"""One multi-round run: round 1 retrieves Q0 without an LLM; then each planning call judges and plans, code
applies it and retrieves the pending questions, until Q0 is answered, nothing is pending, or the call limit
leaves room only for the answering call. Rules: docs/changes/v3.15.md "流程".

The planning prompt does not say how many calls are left, so the state before call n of a run with limit L
is the state of a run with limit n, for every n ≤ L: one run at the highest limit gives every lower limit by
cutting it before call n and answering from there (`snapshots`).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Collection

from rag.query.multi_round.planner import Call, Caller
from rag.query.multi_round.retrieval import Retrieval, UnitTable
from rag.query.multi_round.state import Q0, Schedule, SCHEDULE_A, QuestionList, Status


@dataclass
class Snapshot:
    """The run right after planning call n is applied, before the next retrieval: what the answering call of a
    run limited to n + 1 calls reads."""
    call: int
    questions: QuestionList
    table: UnitTable


@dataclass
class Run:
    question: str
    max_calls: int
    questions: QuestionList
    table: UnitTable
    calls: list[Call] = field(default_factory=list)
    lists: list[list[dict[str, Any]]] = field(default_factory=list)   # the question list after each call
    new_units: list[list[str]] = field(default_factory=list)          # per call: the units handed to it for the first time
    snapshots: list[Snapshot] = field(default_factory=list)
    termination: str = ""         # answered | nothing_pending | limit
    answer: str = ""


def question_list(ql: QuestionList) -> list[dict[str, Any]]:
    return [{"id": q.id, "parent": q.parent_id, "text": q.text, "translation": q.translation, "status": q.status.value,
             "abandon_reason": q.abandon_reason, "note": q.note, "gap": q.gap, "evidence": list(q.evidence),
             "attempts": [{"round": a.round, "runs": [{"mode": r.mode, "queries": r.queries, "cursor": r.cursor,
                                                       "units": [[h.unit_id, h.rank, [list(x) for x in h.ranks]] for h in r.units]}
                                                      for r in a.runs]} for a in q.attempts]}
            for q in ql]


def run(question: str, search: Any, document_ids: Collection[str] | None, codes: Collection[str], languages: list[str],
        caller: Caller, max_calls: int = 5, k: int = 3, schedule: Schedule = SCHEDULE_A) -> Run:
    if max_calls < 2:
        raise ValueError("LLM 调用上限至少为 2")
    ql = QuestionList(question, schedule)
    table = UnitTable()
    retrieval = Retrieval(search, document_ids, codes, k)
    record = Run(question, max_calls, ql, table)
    new = retrieval.round(ql, 1, 1, table)
    round_no = 1
    while True:
        n = len(record.calls) + 1
        record.new_units.append(new)
        out, passed, call = caller.plan(ql, table, new, languages)
        record.calls.append(call)
        if out is not None:
            ql.apply(out, passed)
        table.accepted(ql)
        record.lists.append(question_list(ql))
        record.snapshots.append(Snapshot(n, copy.deepcopy(ql), copy.deepcopy(table)))
        if ql[Q0].status == Status.ANSWERED:
            record.termination, record.answer = "answered", out.answer
            return record
        if not ql.pending():
            record.termination = "nothing_pending"
        elif n == max_calls - 1:
            record.termination = "limit"
        if record.termination:
            record.answer, call = caller.answer(ql, table)
            record.new_units.append([])
            record.calls.append(call)
            record.lists.append(question_list(ql))
            return record
        round_no += 1
        new = retrieval.round(ql, round_no, n + 1, table)


def plan_calls(record: Run) -> int:
    return sum(c.kind == "plan" for c in record.calls)


def answer_at(record: Run, limit: int, caller: Caller) -> tuple[str, Call | None, str]:
    """The answer of the same run limited to `limit` calls: the run's own when it made at most limit − 1
    planning calls, otherwise an answering call on the state after planning call limit − 1. Returns (answer,
    the answering call made for the limit or None, termination)."""
    if plan_calls(record) <= limit - 1:
        return record.answer, None, record.termination
    snap = next(s for s in record.snapshots if s.call == limit - 1)
    text, call = caller.answer(snap.questions, snap.table)
    return text, call, "limit"
