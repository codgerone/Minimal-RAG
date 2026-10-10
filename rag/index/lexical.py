"""BM25 inverted index for one index directory (SQLite). Rules: docs/rules/lexical.md.

Stores, per chunk, its document, language and length (number of terms), and per term the
chunks it occurs in with their frequency. Statistics (chunk count, average length,
document frequency) are taken over the whole index, so a question's score for a chunk
does not depend on the search scope.
"""

from __future__ import annotations

import math
import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Collection, Iterator

from rag.index.analyzer import Language

K1 = 1.2
B = 0.75

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, language TEXT NOT NULL,
    length INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS postings (
    term TEXT NOT NULL, chunk_id TEXT NOT NULL, tf INTEGER NOT NULL,
    PRIMARY KEY (term, chunk_id));
CREATE INDEX IF NOT EXISTS chunks_by_document ON chunks (document_id);
CREATE INDEX IF NOT EXISTS postings_by_chunk ON postings (chunk_id);
"""


@dataclass(frozen=True)
class LexicalChunk:
    chunk_id: str
    language: Language
    terms: Counter[str]


def idf(chunks: int, frequency: int) -> float:
    return math.log(1 + (chunks - frequency + 0.5) / (frequency + 0.5))


class LexicalStore:
    def __init__(self, path: Path):
        self.path = path

    def _connect(self, create: bool) -> sqlite3.Connection | None:
        if not create and not self.path.exists():
            return None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.executescript(_SCHEMA)
        return connection

    def replace_document(self, document_id: str, chunks: list[LexicalChunk]) -> None:
        connection = self._connect(create=True)
        assert connection is not None
        with closing(connection), connection:
            self._delete(connection, document_id)
            connection.executemany(
                "INSERT INTO chunks VALUES (?, ?, ?, ?)",
                [(c.chunk_id, document_id, c.language, sum(c.terms.values())) for c in chunks])
            connection.executemany(
                "INSERT INTO postings VALUES (?, ?, ?)",
                [(term, c.chunk_id, tf) for c in chunks for term, tf in c.terms.items()])

    @staticmethod
    def _delete(connection: sqlite3.Connection, document_id: str) -> None:
        connection.execute("DELETE FROM postings WHERE chunk_id IN "
                           "(SELECT chunk_id FROM chunks WHERE document_id = ?)", (document_id,))
        connection.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))

    def delete_document(self, document_id: str) -> None:
        connection = self._connect(create=False)
        if connection is not None:
            with closing(connection), connection:
                self._delete(connection, document_id)

    def clear(self) -> None:
        connection = self._connect(create=False)
        if connection is not None:
            with closing(connection), connection:
                connection.execute("DELETE FROM postings")
                connection.execute("DELETE FROM chunks")

    def ids_by_document(self) -> dict[str, set[str]]:
        connection = self._connect(create=False)
        result: dict[str, set[str]] = {}
        if connection is None:
            return result
        with closing(connection):
            for chunk_id, document_id in connection.execute("SELECT chunk_id, document_id FROM chunks"):
                result.setdefault(document_id, set()).add(chunk_id)
        return result

    def score(self, terms: dict[Language, frozenset[str]],
              document_ids: Collection[str] | None = None) -> dict[str, float]:
        """BM25 score of every chunk in scope that contains at least one query term.
        Each chunk is scored with the query terms analysed in its own language."""
        connection = self._connect(create=False)
        wanted = sorted(set().union(*terms.values())) if terms else []
        if connection is None or not wanted or (document_ids is not None and not document_ids):
            return {}
        with closing(connection):
            total, average = connection.execute(
                "SELECT COUNT(*), AVG(length) FROM chunks").fetchone()
            rows = list(_postings(connection, wanted))
        frequency = Counter(term for term, *_ in rows)
        scope = None if document_ids is None else set(document_ids)
        scores: dict[str, float] = {}
        for term, chunk_id, tf, document_id, language, length in rows:
            if scope is not None and document_id not in scope:
                continue
            if term not in terms.get(language, frozenset()):
                continue
            norm = tf + K1 * (1 - B + B * length / (average or 1))
            scores[chunk_id] = scores.get(chunk_id, 0.0) + idf(total, frequency[term]) * tf * (K1 + 1) / norm
        return scores


def _postings(connection: sqlite3.Connection, terms: list[str]) -> Iterator[tuple]:
    batch = 500    # stay below SQLite's bound-parameter limit
    for start in range(0, len(terms), batch):
        part = terms[start:start + batch]
        yield from connection.execute(
            "SELECT p.term, p.chunk_id, p.tf, c.document_id, c.language, c.length "
            "FROM postings p JOIN chunks c ON c.chunk_id = p.chunk_id "
            f"WHERE p.term IN ({','.join('?' * len(part))})", part)
