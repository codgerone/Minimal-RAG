"""Query execution: one question → its return order.

The return order is every unit the query would hand back, in the order the units are
selected, each with where it came from and the round it came in. A budget of B returns the
first B units. The plain execution retrieves the question once; its return order is the full
ranking of the question. The planned execution runs a plan (rag/query/planning.py): each
question's text and enabled phrasings are retrieved once each, the unit rankings of one
question are fused by RRF, and a selector takes units from the questions' rankings.
Rules: docs/changes/v3.14.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Collection, Protocol, Sequence

from rag.query.planning import NO_COST, Phrasing, Plan
from rag.query.retriever import RRF_K
from rag.query.units import ResultUnit, UnitSearch

ORIGINAL = "original"   # question id of the original-question route


@dataclass(frozen=True)
class Selected:
    unit: ResultUnit
    query: str          # the text of the question whose ranking the unit was taken from
    round: int = 1
    question_id: str = ""
    ranks: tuple[tuple[str, int], ...] = ()   # (phrasing, the unit's rank in its ranking)


@dataclass(frozen=True)
class QueryCost:
    retrievals: int             # retriever calls
    retrieval_seconds: float    # wall time of those calls, summed
    llm_calls: int = 0          # query-side LLM calls (planning)
    llm_seconds: float = 0.0
    llm_cost_usd: float = 0.0
    rerank_seconds: float = 0.0


@dataclass(frozen=True)
class QueryRun:
    order: tuple[Selected, ...]
    chunk_order: tuple[str, ...]   # plain only: the retriever's chunk ranking
    cost: QueryCost

    def first(self, budget: int) -> list[ResultUnit]:
        return [s.unit for s in self.order[:budget]]

    @property
    def units(self) -> list[ResultUnit]:
        return [s.unit for s in self.order]


def plain(searcher: UnitSearch, question: str,
          document_ids: Collection[str] | None = None) -> QueryRun:
    """The question retrieved once; the return order is its full ranking."""
    ranking = searcher.rank(question, document_ids)
    return QueryRun(tuple(Selected(u, question) for u in ranking.units),
                    ranking.chunk_ids, QueryCost(1, ranking.seconds))


@dataclass(frozen=True)
class Fused:
    """One question's ranking: its units, best first, each with its rank per phrasing."""
    question_id: str
    text: str
    units: tuple[ResultUnit, ...]
    ranks: tuple[tuple[tuple[str, int], ...], ...]   # per unit


@dataclass(frozen=True)
class Pick:
    route: int       # index of the ranking the unit was taken from
    unit: ResultUnit
    round: int


class Selector(Protocol):
    def select(self, rankings: Sequence[Sequence[ResultUnit]]) -> list[Pick]:
        """Every unit of the rankings once, in the order they are handed back."""
        ...


class RoundRobin:
    """Each round takes the next not yet selected unit of every ranking, in ranking order."""

    def select(self, rankings: Sequence[Sequence[ResultUnit]]) -> list[Pick]:
        picks: list[Pick] = []
        seen: set[str] = set()
        positions = [0] * len(rankings)
        round_ = 0
        while any(p < len(r) for p, r in zip(positions, rankings)):
            round_ += 1
            for route, ranking in enumerate(rankings):
                while positions[route] < len(ranking) and ranking[positions[route]].key in seen:
                    positions[route] += 1
                if positions[route] < len(ranking):
                    unit = ranking[positions[route]]
                    positions[route] += 1
                    seen.add(unit.key)
                    picks.append(Pick(route, unit, round_))
        return picks


@dataclass(frozen=True)
class QuerySettings:
    phrasings: frozenset[str] = frozenset()   # enabled beside the question itself: rewrite, translation, hyde, step_back
    original_route: bool = False              # with decomposition: the original question as the last route


def label(phrasing: Phrasing) -> str:
    return f"{phrasing.kind}:{phrasing.language}" if phrasing.language else phrasing.kind


def fuse(rankings: Sequence[tuple[str, Sequence[ResultUnit]]]) -> tuple[list[ResultUnit], list[tuple[tuple[str, int], ...]]]:
    """RRF over (phrasing, unit ranking) pairs: score = Σ 1 / (RRF_K + rank), ties by unit key.
    A unit is represented by its occurrence with the best rank (the first phrasing on ties)."""
    scores: dict[str, float] = {}
    best: dict[str, tuple[int, ResultUnit]] = {}
    ranks: dict[str, list[tuple[str, int]]] = {}
    for name, units in rankings:
        for rank, unit in enumerate(units, start=1):
            scores[unit.key] = scores.get(unit.key, 0.0) + 1 / (RRF_K + rank)
            ranks.setdefault(unit.key, []).append((name, rank))
            if unit.key not in best or rank < best[unit.key][0]:
                best[unit.key] = (rank, unit)
    keys = sorted(scores, key=lambda k: (-scores[k], k))
    return [best[k][1] for k in keys], [tuple(ranks[k]) for k in keys]


def planned(searcher: UnitSearch, plan: Plan, settings: QuerySettings,
            document_ids: Collection[str] | None = None,
            selector: Selector | None = None) -> QueryRun:
    """Runs a plan: every question's own text and enabled phrasings retrieved once each and
    fused per question; with decomposition and the original route, the original question
    retrieved once more as the last route; the selector merges the routes."""
    if settings.original_route and not plan.decomposed:
        raise ValueError("原问题一路只在分解时使用")
    retrievals, seconds = 0, 0.0
    routes: list[Fused] = []
    kinds = {"question", *settings.phrasings}
    for question in plan.questions:
        rankings = []
        for phrasing in question.of(kinds):
            ranking = searcher.rank(phrasing.text, document_ids)
            retrievals += 1
            seconds += ranking.seconds
            rankings.append((label(phrasing), ranking.units))
        units, ranks = fuse(rankings)
        routes.append(Fused(question.id, question.text, tuple(units), tuple(ranks)))
    if settings.original_route:
        ranking = searcher.rank(plan.question, document_ids)
        retrievals += 1
        seconds += ranking.seconds
        routes.append(Fused(ORIGINAL, plan.question, ranking.units,
                            tuple((("question", r),) for r in range(1, len(ranking.units) + 1))))
    picks = (selector or RoundRobin()).select([r.units for r in routes])
    position = [{u.key: n for n, u in enumerate(r.units)} for r in routes]
    order = tuple(Selected(p.unit, routes[p.route].text, p.round, routes[p.route].question_id,
                           routes[p.route].ranks[position[p.route][p.unit.key]]) for p in picks)
    # LLM cost: only the planning steps this configuration uses
    llm = (plan.decomposition if plan.decomposed else NO_COST) + (plan.phrasing if settings.phrasings else NO_COST)
    return QueryRun(order, (), QueryCost(retrievals, seconds, llm.calls, llm.seconds, llm.cost_usd))
