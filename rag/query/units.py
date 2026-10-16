"""Return units: what a query hands back for its ranked chunks.

The retriever ranks chunks; this step, run after it, maps each chunk to what is returned.
With table_return_unit = "table", a table chunk is returned as its whole table, which takes
the place of the best-ranked of its chunks; the table's other chunks are skipped and later
results move up. Other chunks, and every chunk with "chunk", are returned as they are.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Collection

from rag.index.store import ChromaStore
from rag.index.tables import StoredTable, TableStore
from rag.query.retriever import Hit, Retriever


@dataclass(frozen=True)
class ResultUnit:
    hit: Hit                          # the best-ranked chunk that brought this unit in
    table: StoredTable | None = None  # the whole table returned in place of the chunk
    table_pages: tuple[int, ...] = ()  # pages of the whole table's chunks

    @property
    def pages(self) -> tuple[int, ...]:
        return self.table_pages if self.table else self.hit.chunk.pages

    @property
    def text(self) -> str:
        return self.table.text if self.table else self.hit.chunk.text

    @property
    def chunk_ids(self) -> tuple[str, ...]:
        """The chunks whose content this unit returns."""
        return self.table.chunk_ids if self.table else (self.hit.chunk.chunk_id,)

    @property
    def document_id(self) -> str:
        return self.hit.chunk.document_id


def to_units(hits: list[Hit], tables: dict[str, StoredTable],
             pages: dict[str, tuple[int, ...]] | None = None) -> list[ResultUnit]:
    """`tables`: chunk_id → whole table, for the table chunks to return as whole tables;
    `pages`: table_id → the pages its chunks come from."""
    units: list[ResultUnit] = []
    seen: set[str] = set()
    for hit in hits:
        table = tables.get(hit.chunk.chunk_id)
        if table is not None:
            if table.table_id in seen:
                continue
            seen.add(table.table_id)
        units.append(ResultUnit(hit, table, (pages or {}).get(table.table_id, ())
                                if table else ()))
    return units


class UnitSearch:
    """A retriever followed by the return-unit step."""

    def __init__(self, retriever: Retriever, store: ChromaStore, tables: TableStore,
                 table_return_unit: str):
        self.retriever = retriever
        self.store = store
        self.tables = tables
        self.whole_tables = table_return_unit == "table"

    def rank_all(self, question: str, document_ids: Collection[str] | None = None) -> list[ResultUnit]:
        """Every unit in scope, best first (the retriever's full ranking, then mapped to units)."""
        total = self.store.count(document_ids)
        if total == 0:
            return []
        hits = self.retriever.retrieve(question, total, document_ids)
        return self._units(hits)

    def search(self, question: str, top_k: int,
               document_ids: Collection[str] | None = None) -> list[ResultUnit]:
        if not self.whole_tables:
            return [ResultUnit(hit) for hit in self.retriever.retrieve(question, top_k, document_ids)]
        return self.rank_all(question, document_ids)[:top_k]

    def _units(self, hits: list[Hit]) -> list[ResultUnit]:
        if not self.whole_tables:
            return [ResultUnit(hit) for hit in hits]
        tables = self.tables.tables_for_chunks([h.chunk.chunk_id for h in hits])
        chunks = self.store.get_chunks(sorted({cid for t in tables.values() for cid in t.chunk_ids}))
        pages = {t.table_id: tuple(sorted({p for cid in t.chunk_ids for p in chunks[cid].pages}))
                 for t in tables.values()}
        return to_units(hits, tables, pages)
