"""Return units: what a query hands back for its ranked chunks.

The retriever ranks chunks; this step, run after it, maps each chunk to what is returned.
With table_return_unit = "table", a table chunk is returned as its whole table, which takes
the place of the best-ranked of its chunks; the table's other chunks are skipped and later
results move up. Other chunks, and every chunk with "chunk", are returned as they are.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Collection

from rag.index.store import ChromaStore
from rag.index.tables import StoredTable, TableStore
from rag.query.retriever import Hit, HybridRetriever, Retriever


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

    @property
    def key(self) -> str:
        """The unit's identity: the table for a whole table, otherwise the chunk."""
        return self.table.table_id if self.table else self.hit.chunk.chunk_id


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


@dataclass(frozen=True)
class UnitRanking:
    """One retrieval: every return unit in scope, best first."""
    units: tuple[ResultUnit, ...]
    chunk_ids: tuple[str, ...]   # provenance only: the retriever's chunk ranking the units were mapped from
    seconds: float               # retrieval time (for a cached ranking, the time it took when made)


class RetrievalCache:
    """The retriever's full ranking per (index, retriever, scope, text), on disk. The index is
    identified by its manifest, which every ingest rewrites. A cached ranking keeps the time the
    retriever took, so costs read the same on every rerun; the return-unit step runs again. A hybrid
    ranking is stored with both channels' ranks and fused again with the current BM25 weight when
    read, so the weight is not part of the key."""

    def __init__(self, folder: Path, index_dir: Path, retriever: str):
        self.folder = folder
        manifest = (index_dir / "manifest.json").read_bytes()
        self.index = f"{index_dir.name}:{hashlib.sha256(manifest).hexdigest()}:{retriever}"

    def _path(self, text: str, document_ids: Collection[str] | None) -> Path:
        blob = json.dumps({"index": self.index, "text": text,
                           "documents": None if document_ids is None else sorted(document_ids)},
                          ensure_ascii=False, sort_keys=True)
        return self.folder / f"{hashlib.sha256(blob.encode('utf-8')).hexdigest()}.json"

    def get(self, text: str, document_ids: Collection[str] | None) -> dict | None:
        path = self._path(text, document_ids)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def put(self, text: str, document_ids: Collection[str] | None, hits: list[Hit], seconds: float) -> None:
        from rag.jsonio import write_atomic
        rows = [{"chunk_id": h.chunk.chunk_id, "score": h.score, "semantic_rank": h.semantic_rank,
                 "similarity": h.similarity, "lexical_rank": h.lexical_rank, "bm25": h.bm25} for h in hits]
        write_atomic(self._path(text, document_ids),
                     json.dumps({"seconds": seconds, "hits": rows}, ensure_ascii=False))


class UnitSearch:
    """One retrieval: the retriever ranks the chunks, the return-unit step maps them to units."""

    def __init__(self, retriever: Retriever, store: ChromaStore, tables: TableStore,
                 table_return_unit: str, cache: RetrievalCache | None = None):
        self.retriever = retriever
        self.store = store
        self.tables = tables
        self.whole_tables = table_return_unit == "table"
        self.cache = cache

    def rank(self, text: str, document_ids: Collection[str] | None = None) -> UnitRanking:
        """The full ranking of the units in scope."""
        cached = self.cache.get(text, document_ids) if self.cache else None
        if cached is None:
            start = time.perf_counter()
            hits = self._rank_chunks(text, document_ids)
            retrieval = time.perf_counter() - start
            if self.cache:
                self.cache.put(text, document_ids, hits, retrieval)
        else:
            chunks = self.store.get_chunks([row["chunk_id"] for row in cached["hits"]])
            hits = [Hit(chunks[row.pop("chunk_id")], **row) for row in cached["hits"]]
            if isinstance(self.retriever, HybridRetriever):
                hits = self.retriever.refuse(hits)
            retrieval = cached["seconds"]
        start = time.perf_counter()
        units = tuple(self._units(hits))
        return UnitRanking(units, tuple(h.chunk.chunk_id for h in hits),
                           retrieval + time.perf_counter() - start)

    def warm_up(self, text: str, document_ids: Collection[str] | None = None) -> None:
        """Loads the encoder outside any timed or cached retrieval."""
        self._rank_chunks(text, document_ids)

    def _rank_chunks(self, text: str, document_ids: Collection[str] | None) -> list[Hit]:
        total = self.store.count(document_ids)
        return self.retriever.retrieve(text, total, document_ids) if total else []

    def search(self, text: str, top_k: int,
               document_ids: Collection[str] | None = None) -> list[ResultUnit]:
        """The first top_k units of the ranking."""
        if not self.whole_tables:
            return [ResultUnit(hit) for hit in self.retriever.retrieve(text, top_k, document_ids)]
        return list(self.rank(text, document_ids).units[:top_k])

    def _units(self, hits: list[Hit]) -> list[ResultUnit]:
        if not self.whole_tables:
            return [ResultUnit(hit) for hit in hits]
        tables = self.tables.tables_for_chunks([h.chunk.chunk_id for h in hits])
        chunks = self.store.get_chunks(sorted({cid for t in tables.values() for cid in t.chunk_ids}))
        pages = {t.table_id: tuple(sorted({p for cid in t.chunk_ids for p in chunks[cid].pages}))
                 for t in tables.values()}
        return to_units(hits, tables, pages)
