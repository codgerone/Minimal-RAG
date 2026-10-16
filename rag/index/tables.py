"""Whole-table store for one index directory (SQLite): the full serialized text of every table
and the chunks it was split into, so a query that hits any of them can return the whole table.

Not vectorized: tables are looked up by the chunks that hit, never ranked themselves.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Collection

from rag.models import ChunkBatch, ParsedDocument, TableNode

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tables (
    table_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS table_chunks (
    chunk_id TEXT PRIMARY KEY, table_id TEXT NOT NULL, position INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS tables_by_document ON tables (document_id);
CREATE INDEX IF NOT EXISTS table_chunks_by_table ON table_chunks (table_id);
"""


@dataclass(frozen=True)
class StoredTable:
    table_id: str
    document_id: str
    text: str
    chunk_ids: tuple[str, ...]     # in chunk order


def table_id(document_id: str, node_id: str) -> str:
    return f"{document_id}-{node_id}"


def collect_tables(document: ParsedDocument, batch: ChunkBatch) -> list[StoredTable]:
    """Each table node with its serialized text and the table chunks drawn from it.
    Table chunks hold exactly one table (the chunker never mixes a table with other content)."""
    nodes = {node.node_id: node for node in document.nodes if isinstance(node, TableNode)}
    members: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    for chunk in batch.chunks:
        if chunk.kind != "table":
            continue
        owners = {source.node_id for source in chunk.sources if source.node_id in nodes}
        if len(owners) != 1:
            raise ValueError(f"表格 chunk {chunk.chunk_id} 应恰好来自一张表，实际 {sorted(owners)}")
        members[owners.pop()].append(chunk.chunk_id)
    empty = [node_id for node_id, ids in members.items() if not ids]
    if empty:
        raise ValueError(f"表格没有对应的 chunk：{empty}")
    return [StoredTable(table_id(document.document_id, node_id), document.document_id,
                        nodes[node_id].serialized.text, tuple(ids))
            for node_id, ids in members.items()]


class TableStore:
    def __init__(self, path: Path):
        self.path = path

    def _connect(self, create: bool) -> sqlite3.Connection | None:
        if not create and not self.path.exists():
            return None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.executescript(_SCHEMA)
        return connection

    def replace_document(self, document_id: str, tables: list[StoredTable]) -> None:
        connection = self._connect(create=True)
        assert connection is not None
        with closing(connection), connection:
            self._delete(connection, document_id)
            connection.executemany("INSERT INTO tables VALUES (?, ?, ?)",
                                   [(t.table_id, document_id, t.text) for t in tables])
            connection.executemany("INSERT INTO table_chunks VALUES (?, ?, ?)",
                                   [(chunk_id, t.table_id, i) for t in tables
                                    for i, chunk_id in enumerate(t.chunk_ids)])

    @staticmethod
    def _delete(connection: sqlite3.Connection, document_id: str) -> None:
        connection.execute("DELETE FROM table_chunks WHERE table_id IN "
                           "(SELECT table_id FROM tables WHERE document_id = ?)", (document_id,))
        connection.execute("DELETE FROM tables WHERE document_id = ?", (document_id,))

    def delete_document(self, document_id: str) -> None:
        connection = self._connect(create=False)
        if connection is not None:
            with closing(connection), connection:
                self._delete(connection, document_id)

    def clear(self) -> None:
        connection = self._connect(create=False)
        if connection is not None:
            with closing(connection), connection:
                connection.execute("DELETE FROM table_chunks")
                connection.execute("DELETE FROM tables")

    def counts_by_document(self) -> dict[str, int]:
        connection = self._connect(create=False)
        if connection is None:
            return {}
        with closing(connection):
            return dict(connection.execute(
                "SELECT document_id, COUNT(*) FROM tables GROUP BY document_id"))

    def tables_for_chunks(self, chunk_ids: Collection[str]) -> dict[str, StoredTable]:
        """chunk_id → the table it belongs to, for the given chunks that are table chunks."""
        connection = self._connect(create=False)
        if connection is None or not chunk_ids:
            return {}
        with closing(connection):
            owners: dict[str, str] = {}
            ids = list(chunk_ids)
            for start in range(0, len(ids), 500):     # stay below SQLite's bound-parameter limit
                part = ids[start:start + 500]
                owners.update(connection.execute(
                    f"SELECT chunk_id, table_id FROM table_chunks WHERE chunk_id IN ({','.join('?' * len(part))})",
                    part))
            tables: dict[str, StoredTable] = {}
            for tid in set(owners.values()):
                document_id, text = connection.execute(
                    "SELECT document_id, text FROM tables WHERE table_id = ?", (tid,)).fetchone()
                members = tuple(row[0] for row in connection.execute(
                    "SELECT chunk_id FROM table_chunks WHERE table_id = ? ORDER BY position", (tid,)))
                tables[tid] = StoredTable(tid, document_id, text, members)
        return {chunk_id: tables[tid] for chunk_id, tid in owners.items()}
