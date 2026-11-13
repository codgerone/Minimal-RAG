"""Step result caches under .rag/cache/, shared by every index: a step whose inputs, settings,
rules and library versions are unchanged is read back instead of recomputed.

Caches only make ingest faster; whether a document is out of date is decided by its build record
(rag/index/build_steps.py). Every entry stores its full key and is used only when the stored key
equals the requested one, so a hash collision or a damaged entry counts as a miss.
With `refresh`, entries are recomputed and overwritten instead of read (ingest --refresh-cache).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Callable, TypeVar

import numpy as np

from rag.jsonio import read_json, write_atomic

T = TypeVar("T")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class StepCache:
    def __init__(self, root: Path, refresh: bool = False):
        self.root = root        # .rag/cache
        self.refresh = refresh
        self.log: list[tuple[str, bool]] = []    # (step, hit) of every lookup, read per document by ingest

    def get_or_compute(self, step: str, key: dict[str, Any], compute: Callable[[], T],
                       dump: Callable[[T], Any], load: Callable[[Any], T]) -> tuple[T, bool]:
        """The cached value of `step` for `key`, or the computed one (then stored); and whether it was a hit."""
        canonical = _canonical(key)
        path = self.root / step / f"{_sha(canonical)}.json"
        if not self.refresh and path.is_file():
            try:
                stored = read_json(path)
            except (OSError, ValueError):
                stored = None
            if isinstance(stored, dict) and _canonical(stored.get("key")) == canonical:
                self.log.append((step, True))
                return load(stored["value"]), True
        value = compute()
        path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(path, json.dumps({"key": key, "value": dump(value)}, ensure_ascii=False))
        self.log.append((step, False))
        return value, False

    def embeddings(self) -> "EmbeddingCache":
        return EmbeddingCache(self.root / "embeddings.sqlite", self.refresh)

    def size_bytes(self) -> int:
        if not self.root.is_dir():
            return 0
        return sum(path.stat().st_size for path in self.root.rglob("*") if path.is_file())


class EmbeddingCache:
    """Passage vectors keyed by encoder identity and exact text. A missing passage is encoded on
    its own, so its vector depends only on the identity and the text, never on other passages."""

    def __init__(self, path: Path, refresh: bool = False):
        self.path = path
        self.refresh = refresh

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.execute("CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, identity TEXT NOT NULL, "
                           "text TEXT NOT NULL, vector BLOB NOT NULL)")
        return connection

    def encode_passages(self, embedder: Any, texts: list[str]) -> tuple[list[list[float]], int]:
        """Vectors in the order of `texts`, and how many were read from the cache."""
        identity = _canonical(embedder.identity())
        keys = [_sha(identity + "\0" + text) for text in texts]
        found: dict[str, list[float]] = {}
        with self._connect() as connection:
            if not self.refresh:
                for key, text in zip(keys, texts):
                    row = connection.execute("SELECT identity, text, vector FROM vectors WHERE key = ?",
                                             (key,)).fetchone()
                    if row is not None and row[0] == identity and row[1] == text:
                        found[key] = [float(v) for v in np.frombuffer(row[2], dtype=np.float64)]
            hits = sum(1 for key in keys if key in found)
            for key, text in zip(keys, texts):
                if key in found:
                    continue
                vector = embedder.encode_passages([text])[0]
                # float64 holds any encoder's output exactly, so a read-back vector equals the computed one.
                connection.execute("INSERT OR REPLACE INTO vectors VALUES (?, ?, ?, ?)",
                                   (key, identity, text, np.asarray(vector, dtype=np.float64).tobytes()))
                found[key] = vector
        connection.close()
        return [found[key] for key in keys], hits
