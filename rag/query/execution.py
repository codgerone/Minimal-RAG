"""Query execution: one question → its return order.

The return order is every unit the query would hand back, in the order the units are
selected, each with where it came from and the round it came in. A budget of B returns the
first B units. The plain execution retrieves the question once; its return order is the full
ranking of the question. The planned execution runs a plan (rag/query/planning.py): each
question's text and enabled phrasings are retrieved once each and the unit rankings of one
question are fused by RRF, or, unfused, one phrasing is retrieved in place of the question's
text; a selector (round robin or global RRF) merges the questions' rankings into the return
order. Unmerged (evaluation only), a decomposed plan has its rankings and no return order.
With codes stripped (P3.2.0) every text a question is retrieved by has the identifier codes removed
first; the original-question route keeps them. Rules: docs/rules/query-planning.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Collection, Protocol, Sequence

from rag.query.planning import NO_COST, Phrasing, Plan
from rag.query.retriever import RRF_K
from rag.query.scope import strip_codes
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
class Fused:
    """One question's ranking: its units, best first, each with its rank per phrasing."""
    question_id: str
    text: str
    units: tuple[ResultUnit, ...]
    ranks: tuple[tuple[tuple[str, int], ...], ...]   # per unit


@dataclass(frozen=True)
class QueryRun:
    order: tuple[Selected, ...]    # empty when unmerged
    chunk_order: tuple[str, ...]   # plain only: the retriever's chunk ranking
    cost: QueryCost
    routes: tuple[Fused, ...] = ()   # planned only: each question's ranking, the original route last

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
class Pick:
    route: int       # index of the ranking the unit was taken from
    unit: ResultUnit
    round: int       # round robin: the round; global RRF: 0


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


class GlobalRRF:
    """One ranking by RRF over all rankings (fuse); a unit is taken from the ranking where it
    ranks best, the earlier ranking on ties."""

    def select(self, rankings: Sequence[Sequence[ResultUnit]]) -> list[Pick]:
        units, ranks = fuse([(str(route), ranking) for route, ranking in enumerate(rankings)])
        return [Pick(int(min(r, key=lambda x: (x[1], int(x[0])))[0]), u, 0) for u, r in zip(units, ranks)]


SELECTORS: dict[str, Selector] = {"round_robin": RoundRobin(), "rrf": GlobalRRF()}


class Unavailable(ValueError):
    """The settings cannot run on this plan; the message says why."""


@dataclass(frozen=True)
class QuerySettings:
    phrasings: frozenset[str] = frozenset()   # rewrite, translation, hyde, step_back
    fuse_phrasings: bool = True               # RRF over the question and its phrasings; False: the one phrasing in place of the question
    merge: str | None = None                  # with decomposition: round_robin | rrf; None: unmerged (evaluation only)
    original_route: bool = False              # with a merge: the original question as one more ranking (round robin: last in each round)
    strip_codes: bool = False                 # P3.2.0: the identifier codes removed from every text of every question

    def __post_init__(self):
        if not self.fuse_phrasings and len(self.phrasings) != 1:
            raise ValueError("不融合时只能选一种写法")
        if self.merge is not None and self.merge not in SELECTORS:
            raise ValueError(f"未知合并方式：{self.merge}")
        if self.original_route and self.merge is None:
            raise ValueError("原问题一路只在选了合并方式时使用")


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
            document_ids: Collection[str] | None = None, codes: Collection[str] = ()) -> QueryRun:
    """Runs a plan: every question's own text and enabled phrasings retrieved once each and
    fused per question (unfused: its one phrasing alone); with decomposition, the merge selects
    the return order from the questions' rankings, the original question's ranking last when
    the original route is on, and without a merge there is no return order. Raises Unavailable
    when an unfused phrasing does not have exactly one text for a question. `codes`: the identifier
    codes the scope was found by, removed when settings.strip_codes."""
    if not plan.decomposed and (settings.merge or settings.original_route):
        raise ValueError("合并方式与原问题一路只在分解时使用")
    retrievals, seconds = 0, 0.0
    routes: list[Fused] = []
    kinds = {"question", *settings.phrasings} if settings.fuse_phrasings else set(settings.phrasings)
    for question in plan.questions:
        texts = question.of(kinds)
        if not settings.fuse_phrasings and len(texts) != 1:
            raise Unavailable(f"{question.id} 的写法 {next(iter(settings.phrasings))} 有 {len(texts)} 段文字，"
                              "不融合时须恰好一段")
        rankings = []
        for phrasing in texts:
            text = strip_codes(phrasing.text, codes)[0] if settings.strip_codes else phrasing.text
            ranking = searcher.rank(text, document_ids)
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
    if plan.decomposed and settings.merge is None:
        picks: list[Pick] = []
    else:   # without decomposition the one ranking is handed back as it is
        picks = SELECTORS[settings.merge or "round_robin"].select([r.units for r in routes])
    position = [{u.key: n for n, u in enumerate(r.units)} for r in routes]
    order = tuple(Selected(p.unit, routes[p.route].text, p.round, routes[p.route].question_id,
                           routes[p.route].ranks[position[p.route][p.unit.key]]) for p in picks)
    # LLM cost: only the planning steps this configuration uses
    llm = (plan.decomposition if plan.decomposed else NO_COST) + (plan.phrasing if settings.phrasings else NO_COST)
    return QueryRun(order, (), QueryCost(retrievals, seconds, llm.calls, llm.seconds, llm.cost_usd),
                    tuple(routes))
