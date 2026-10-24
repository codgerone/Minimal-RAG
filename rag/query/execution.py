"""Query execution: one question → its return order.

The return order is every unit the query would hand back, in the order the units are
selected, each with the query text that brought it in and the round it came in. A budget of
B returns the first B units. The plain execution retrieves the question once; its return order
is the full ranking of the question.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Collection

from rag.query.units import ResultUnit, UnitSearch


@dataclass(frozen=True)
class Selected:
    unit: ResultUnit
    query: str      # the query text whose ranking brought the unit in
    round: int = 1


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
    chunk_order: tuple[str, ...]   # chunks in the order the retrievals first ranked them
    cost: QueryCost

    def first(self, budget: int) -> list[ResultUnit]:
        return [s.unit for s in self.order[:budget]]

    @property
    def units(self) -> list[ResultUnit]:
        return [s.unit for s in self.order]


def plain(searcher: UnitSearch, question: str,
          document_ids: Collection[str] | None = None) -> QueryRun:
    """The question retrieved once; the return order is its full ranking."""
    start = time.perf_counter()
    hits = searcher.rank_hits(question, document_ids)
    units = searcher.units_for(hits)
    seconds = time.perf_counter() - start
    return QueryRun(tuple(Selected(u, question) for u in units),
                    tuple(h.chunk.chunk_id for h in hits), QueryCost(1, seconds))
