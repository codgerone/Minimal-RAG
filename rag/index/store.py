"""Chroma vector store for one index directory (one collection, cosine distance)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Collection

COLLECTION = "chunks"
WRITE_BATCH = 100


@dataclass(frozen=True)
class StoredChunk:
    chunk_id: str
    document_id: str
    document_name: str
    relative_path: str
    chunk_index: int
    kind: str
    pages: tuple[int, ...]
    token_count: int | None
    text: str


@dataclass(frozen=True)
class VectorHit:
    chunk: StoredChunk
    distance: float


def _metadata(chunk: StoredChunk) -> dict[str, object]:
    return {"document_id": chunk.document_id, "document_name": chunk.document_name,
            "relative_path": chunk.relative_path, "chunk_index": chunk.chunk_index,
            "kind": chunk.kind, "pages": json.dumps(list(chunk.pages)),
            "token_count": -1 if chunk.token_count is None else chunk.token_count}


def _chunk(chunk_id: str, text: str, meta: dict[str, Any]) -> StoredChunk:
    return StoredChunk(chunk_id, str(meta["document_id"]), str(meta["document_name"]),
                       str(meta["relative_path"]), int(meta["chunk_index"]), str(meta["kind"]),
                       tuple(json.loads(str(meta["pages"]))),
                       None if int(meta["token_count"]) < 0 else int(meta["token_count"]), text)


def _where(document_ids: Collection[str] | None) -> dict[str, Any] | None:
    if document_ids is None:
        return None
    ids = sorted(document_ids)
    return {"document_id": ids[0]} if len(ids) == 1 else {"document_id": {"$in": ids}}


class ChromaStore:
    def __init__(self, path: Path):
        self.path = path
        self._collection: Any = None

    def _open(self, create: bool) -> Any:
        if self._collection is None:
            if not create and not self.path.exists():
                return None
            import chromadb
            self.path.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(path=str(self.path))
            self._collection = client.get_or_create_collection(
                COLLECTION, configuration={"hnsw": {"space": "cosine"}})
        return self._collection

    def count(self, document_ids: Collection[str] | None = None) -> int:
        """Chunks in the given documents (None = whole index)."""
        collection = self._open(create=False)
        if collection is None or (document_ids is not None and not document_ids):
            return 0
        if document_ids is None:
            return collection.count()
        return len(collection.get(where=_where(document_ids), include=[])["ids"])

    def ids_by_document(self) -> dict[str, set[str]]:
        collection = self._open(create=False)
        result: dict[str, set[str]] = {}
        if collection is None:
            return result
        raw = collection.get(include=["metadatas"])
        for chunk_id, meta in zip(raw["ids"], raw["metadatas"]):
            result.setdefault(str(meta["document_id"]), set()).add(chunk_id)
        return result

    def replace_document(self, document_id: str, chunks: list[StoredChunk],
                         vectors: list[list[float]]) -> None:
        collection = self._open(create=True)
        collection.delete(where={"document_id": document_id})
        for start in range(0, len(chunks), WRITE_BATCH):
            batch = chunks[start:start + WRITE_BATCH]
            collection.add(ids=[c.chunk_id for c in batch],
                           embeddings=vectors[start:start + WRITE_BATCH],
                           documents=[c.text for c in batch],
                           metadatas=[_metadata(c) for c in batch])

    def delete_document(self, document_id: str) -> None:
        collection = self._open(create=False)
        if collection is not None:
            collection.delete(where={"document_id": document_id})

    def list_chunks(self, document_id: str | None = None) -> list[StoredChunk]:
        collection = self._open(create=False)
        if collection is None:
            return []
        where = {"document_id": document_id} if document_id else None
        raw = collection.get(where=where, include=["documents", "metadatas"])
        chunks = [_chunk(i, t, m) for i, t, m in zip(raw["ids"], raw["documents"], raw["metadatas"])]
        return sorted(chunks, key=lambda c: (c.relative_path.casefold(), c.chunk_index))

    def query(self, vector: list[float], n: int,
              document_ids: Collection[str] | None = None) -> list[VectorHit]:
        collection = self._open(create=False)
        if collection is None or n <= 0 or (document_ids is not None and not document_ids):
            return []
        raw = collection.query(query_embeddings=[vector], n_results=n, where=_where(document_ids),
                               include=["documents", "metadatas", "distances"])
        return [VectorHit(_chunk(i, t, m), float(d)) for i, t, m, d in
                zip(raw["ids"][0], raw["documents"][0], raw["metadatas"][0], raw["distances"][0])]
