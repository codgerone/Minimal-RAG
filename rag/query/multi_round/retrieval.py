"""One retrieval round of a multi-round run, and the run's unit table.

Every pending question is retrieved by the modes its attempt number calls for (state.Schedule). A mode's
texts each have the identifier codes of Q0 removed and are retrieved once; several texts (languages) are
fused by RRF into the mode's ranking. Each mode returns K units, skipping units handed to the LLM in an
earlier round; a unit two questions or modes return in the same round is handed once, with both sources.
"continue" goes on down the question's first ranking from where its last take stopped.
Rules: docs/changes/v3.15.md "检索方式".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Collection, Protocol

from rag.query.execution import fuse
from rag.query.multi_round.state import FIRST, ModeRun, Attempt, Handed, Question, QuestionList, Q0
from rag.query.scope import strip_codes
from rag.query.units import ResultUnit


class Searcher(Protocol):
    def rank(self, text: str, document_ids: Collection[str] | None = None): ...


@dataclass
class UnitEntry:
    unit: ResultUnit
    call: int                                                  # the call it was first handed to the LLM in
    sources: list[tuple[str, int, str, int]] = field(default_factory=list)   # (question, attempt number, mode, rank)
    accepted_by: list[str] = field(default_factory=list)


class UnitTable:
    """Unit id → the unit, when it was first handed to the LLM, which retrievals returned it, which questions
    accepted it. Questions keep only unit ids; inputs are assembled from here."""

    def __init__(self):
        self.entries: dict[str, UnitEntry] = {}
        self.order: list[str] = []      # the display sequence: units in the order first handed to the LLM

    def __contains__(self, unit_id: str) -> bool:
        return unit_id in self.entries

    def __getitem__(self, unit_id: str) -> UnitEntry:
        return self.entries[unit_id]

    def handed(self) -> set[str]:
        return set(self.entries)

    def accepted(self, ql: QuestionList) -> None:
        for e in self.entries.values():
            e.accepted_by = [q.id for q in ql if e.unit.key in q.evidence]


def question_order(qid: str) -> int:
    return int(qid[1:])


class Retrieval:
    def __init__(self, search: Searcher, document_ids: Collection[str] | None, codes: Collection[str], k: int):
        self.search = search
        self.document_ids = document_ids
        self.codes = codes
        self.k = k
        self.retrievals = 0       # retriever calls so far
        self.seconds = 0.0        # their time (a cached ranking counts the time it took when made)

    def texts(self, q: Question, mode: str) -> list[str]:
        """The texts a mode retrieves for a question, before the codes are removed."""
        if mode == FIRST:
            return [q.text] + ([] if q.id == Q0 else [q.translation[l] for l in sorted(q.translation)])
        if mode == "continue":
            return []
        return [t for _, t in q.fallback.get(mode, [])]

    def ranking(self, texts: list[str]) -> tuple[list[ResultUnit], list[tuple[tuple[int, int], ...]]]:
        """The mode's ranking: each text retrieved once, several fused by RRF; per unit its rank in each text."""
        rankings = []
        for i, t in enumerate(texts):
            got = self.search.rank(strip_codes(t, self.codes)[0], self.document_ids)
            self.retrievals += 1
            self.seconds += got.seconds
            rankings.append((str(i), got.units))
        units, ranks = fuse(rankings)
        return units, [tuple((int(name), r) for name, r in rs) for rs in ranks]

    def take(self, units: list[ResultUnit], ranks, start: int, skip: set[str]) -> tuple[list[Handed], int, list[ResultUnit]]:
        """K units from position `start` on, skipping those in `skip`; the position after the last one taken."""
        got, picked, i = [], [], start
        while i < len(units) and len(got) < self.k:
            u = units[i]
            if u.key not in skip:
                got.append(Handed(u.key, i + 1, ranks[i]))
                picked.append(u)
            i += 1
        return got, i, picked

    def round(self, ql: QuestionList, round_no: int, call_no: int, table: UnitTable) -> list[str]:
        """Retrieves every pending question; returns the units handed for the first time, in display order
        (question, then mode, then rank)."""
        before = table.handed()
        new: list[str] = []
        for q in sorted(ql.pending(), key=lambda q: question_order(q.id)):
            number = len(q.attempts) + 1
            attempt = Attempt(round_no)
            for mode in ql.next_modes(q.id):
                if mode == "continue":
                    first = [r for a in q.attempts for r in a.runs if r.mode in (FIRST, "continue")]
                    base = next(r for a in q.attempts for r in a.runs if r.mode == FIRST)
                    queries, start = base.queries, max(r.cursor for r in first)
                else:
                    queries, start = self.texts(q, mode), 0
                    if not queries:
                        continue   # e.g. Q0 written in every document language: no translation
                units, ranks = self.ranking(queries)
                handed, cursor, picked = self.take(units, ranks, start, before)
                attempt.runs.append(ModeRun(mode, [strip_codes(t, self.codes)[0] for t in queries], handed, cursor))
                for h, u in zip(handed, picked):
                    if h.unit_id not in table:
                        table.entries[h.unit_id] = UnitEntry(u, call_no)
                        table.order.append(h.unit_id)
                        new.append(h.unit_id)
                    table[h.unit_id].sources.append((q.id, number, mode, h.rank))
            ql.retrieved(q.id, attempt)
        return new
