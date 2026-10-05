"""Explicit-vector Chroma reader/writer with full V3 record readback."""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any

import chromadb
from chromadb.errors import NotFoundError

from rag.v3.adapters.vector_codec import decode_metadata, encode_metadata
from rag.v3.application.assembly import canonical_json_bytes
from rag.v3.application.vector_compare import (
    records_equal as _records_equal, snapshots_equal as _snapshots_equal,
)
from rag.v3.contracts.storage import (
    BrowseRecord, ChunkMetadata, IndexIdentity, VectorQueryHit, VectorRecord, VectorSnapshot,
)

WRITE_BATCH_SIZE = 100


class ChromaStoreError(RuntimeError):
    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(detail)


def _snapshot_digest(snapshot: VectorSnapshot) -> str:
    value = asdict(snapshot)
    del value["sha256"]
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


class ChromaV3Store:
    def __init__(self, workspace_root: Path):
        self.db_path = workspace_root.resolve() / ".rag" / "system-v3" / "chroma"
        self._client: Any | None = None

    def _client_for(self, *, create: bool) -> Any | None:
        if self._client is not None:
            return self._client
        if not create and not self.db_path.exists():
            return None
        try:
            if create:
                self.db_path.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(self.db_path))
            return self._client
        except Exception as exc:
            raise ChromaStoreError("vector_store_failed", "cannot open V3 Chroma") from exc

    def _collection(self, index: IndexIdentity, *, create: bool) -> Any | None:
        client = self._client_for(create=create)
        if client is None:
            return None
        try:
            if create:
                collection = client.get_or_create_collection(
                    index.collection_name,
                    configuration={"hnsw": {"space": "cosine"}},
                    metadata={"build_fingerprint": index.build_fingerprint},
                )
            else:
                collection = client.get_collection(index.collection_name)
            if (collection.metadata or {}).get("build_fingerprint") != index.build_fingerprint:
                raise ChromaStoreError("vector_record_invalid", "collection fingerprint mismatch")
            return collection
        except NotFoundError:
            return None
        except ChromaStoreError:
            raise
        except Exception as exc:
            raise ChromaStoreError("vector_store_failed", "cannot open V3 collection") from exc

    @staticmethod
    def _record(index: IndexIdentity, record_id: str, document: object,
                metadata_raw: object, vector_raw: object) -> VectorRecord:
        try:
            if not isinstance(document, str) or not isinstance(metadata_raw, dict):
                raise ValueError("document or metadata missing")
            metadata = decode_metadata(metadata_raw)
            vector = tuple(float(value) for value in vector_raw)
            if metadata.build_fingerprint != index.build_fingerprint:
                raise ValueError("record index identity mismatch")
            return VectorRecord(record_id, metadata.document_id, metadata.build_id,
                                metadata.file_hash, document, metadata, vector)
        except Exception as exc:
            raise ChromaStoreError("vector_record_invalid", "invalid V3 vector record") from exc

    def _read(self, index: IndexIdentity, document_id: str | None = None) -> tuple[VectorRecord, ...]:
        collection = self._collection(index, create=False)
        if collection is None:
            return ()
        records = []
        offset = 0
        try:
            while True:
                raw = collection.get(
                    where={"document_id": document_id} if document_id is not None else None,
                    limit=WRITE_BATCH_SIZE, offset=offset,
                    include=["documents", "metadatas", "embeddings"],
                )
                ids = raw["ids"]
                if not ids:
                    break
                if raw["documents"] is None or raw["metadatas"] is None or raw["embeddings"] is None:
                    raise ValueError("Chroma omitted required record fields")
                for record_id, document, metadata, vector in zip(
                    ids, raw["documents"], raw["metadatas"], raw["embeddings"]
                ):
                    item = self._record(index, record_id, document, metadata, vector)
                    if document_id is not None and item.document_id != document_id:
                        raise ChromaStoreError("vector_record_invalid", "document scope mismatch")
                    records.append(item)
                offset += len(ids)
                if len(ids) < WRITE_BATCH_SIZE:
                    break
            return tuple(sorted(records, key=lambda item: item.chunk_id))
        except ChromaStoreError:
            raise
        except Exception as exc:
            raise ChromaStoreError("vector_store_failed", "cannot read V3 records") from exc

    def count(self, index: IndexIdentity, document_id: str | None = None) -> int:
        return len(self._read(index, document_id))

    def collection_exists(self, index: IndexIdentity) -> bool:
        return self._collection(index, create=False) is not None

    def list_records(self, index: IndexIdentity,
                     document_id: str | None = None) -> tuple[VectorRecord, ...]:
        return self._read(index, document_id)

    def list_text_metadata(self, index: IndexIdentity,
                           document_id: str | None = None) -> tuple[BrowseRecord, ...]:
        collection = self._collection(index, create=False)
        if collection is None:
            return ()
        results: list[BrowseRecord] = []
        offset = 0
        try:
            while True:
                raw = collection.get(
                    where={"document_id": document_id} if document_id is not None else None,
                    limit=WRITE_BATCH_SIZE, offset=offset,
                    include=["documents", "metadatas"],
                )
                ids = raw["ids"]
                if not ids:
                    break
                if raw["documents"] is None or raw["metadatas"] is None:
                    raise ValueError("Chroma omitted browse fields")
                for record_id, document, raw_metadata in zip(
                        ids, raw["documents"], raw["metadatas"]):
                    if not isinstance(document, str) or not isinstance(raw_metadata, dict):
                        raise ValueError("invalid browse record scalar fields")
                    metadata = decode_metadata(raw_metadata)
                    if metadata.build_fingerprint != index.build_fingerprint or (
                            document_id is not None and metadata.document_id != document_id):
                        raise ValueError("browse record identity differs")
                    results.append(BrowseRecord(record_id, document, metadata))
                offset += len(ids)
                if len(ids) < WRITE_BATCH_SIZE:
                    break
            return tuple(sorted(results, key=lambda item: item.chunk_id))
        except Exception as exc:
            raise ChromaStoreError("vector_record_invalid", "invalid V3 browse record") from exc

    def query(self, index: IndexIdentity, query_vector: tuple[float, ...], k: int,
              document_id: str | None = None) -> tuple[VectorQueryHit, ...]:
        if k <= 0 or not query_vector or not all(math.isfinite(value) for value in query_vector):
            raise ChromaStoreError("vector_record_invalid", "invalid query vector or K")
        collection = self._collection(index, create=False)
        if collection is None:
            return ()
        try:
            raw = collection.query(
                query_embeddings=[list(query_vector)], n_results=k,
                where={"document_id": document_id} if document_id is not None else None,
                include=["documents", "metadatas", "embeddings", "distances"],
            )
            return tuple(VectorQueryHit(
                self._record(index, record_id, document, metadata, vector), float(distance))
                for record_id, document, metadata, vector, distance in zip(
                    raw["ids"][0], raw["documents"][0], raw["metadatas"][0],
                    raw["embeddings"][0], raw["distances"][0]
                )
            )
        except ChromaStoreError:
            raise
        except Exception as exc:
            raise ChromaStoreError("vector_store_failed", "V3 vector query failed") from exc

    def snapshot(self, index: IndexIdentity,
                 document_ids: tuple[str, ...] | None = None) -> VectorSnapshot:
        exists = self._collection(index, create=False) is not None
        if document_ids is None:
            records = self._read(index)
            scope = ()
        else:
            scope = tuple(sorted(set(document_ids)))
            records = tuple(sorted((record for document_id in scope
                                    for record in self._read(index, document_id)),
                                   key=lambda item: item.chunk_id))
        provisional = VectorSnapshot("vector_snapshot_v3", index, exists, scope,
                                     tuple(record.chunk_id for record in records),
                                     tuple(record.text for record in records),
                                     tuple(record.embedding for record in records),
                                     tuple(record.metadata for record in records), "0" * 64)
        return VectorSnapshot("vector_snapshot_v3", index, exists, scope,
                              provisional.ids, provisional.documents,
                              provisional.embeddings, provisional.metadatas,
                              _snapshot_digest(provisional))

    def _add(self, index: IndexIdentity, records: tuple[VectorRecord, ...]) -> None:
        if not records:
            return
        collection = self._collection(index, create=True)
        try:
            for start in range(0, len(records), WRITE_BATCH_SIZE):
                batch = records[start:start + WRITE_BATCH_SIZE]
                collection.add(ids=[item.chunk_id for item in batch],
                               documents=[item.text for item in batch],
                               embeddings=[list(item.embedding) for item in batch],
                               metadatas=[encode_metadata(item.metadata) for item in batch])
        except Exception as exc:
            raise ChromaStoreError("vector_store_failed", "V3 vector write failed") from exc

    def replace_document(self, index: IndexIdentity, document_id: str,
                         records: tuple[VectorRecord, ...]) -> None:
        if any(item.document_id != document_id or
               item.metadata.build_fingerprint != index.build_fingerprint for item in records):
            raise ChromaStoreError("vector_record_invalid", "document replacement identity mismatch")
        existing = self._collection(index, create=False)
        if existing is not None:
            try:
                existing.delete(where={"document_id": document_id})
            except Exception as exc:
                raise ChromaStoreError("vector_store_failed", "V3 document deletion failed") from exc
        self._add(index, records)
        if not _records_equal(tuple(sorted(records, key=lambda item: item.chunk_id)),
                              self._read(index, document_id)):
            raise ChromaStoreError("vector_store_failed", "document vector readback differs")

    def replace_collection(self, index: IndexIdentity,
                           records: tuple[VectorRecord, ...]) -> None:
        if any(item.metadata.build_fingerprint != index.build_fingerprint for item in records):
            raise ChromaStoreError("vector_record_invalid", "collection replacement identity mismatch")
        client = self._client_for(create=True)
        if self._collection(index, create=False) is not None:
            try:
                client.delete_collection(index.collection_name)
            except Exception as exc:
                raise ChromaStoreError("vector_store_failed", "V3 collection deletion failed") from exc
        self._collection(index, create=True)
        self._add(index, records)
        if not _records_equal(tuple(sorted(records, key=lambda item: item.chunk_id)),
                              self._read(index)):
            raise ChromaStoreError("vector_store_failed", "collection vector readback differs")

    def delete_document(self, index: IndexIdentity, document_id: str) -> None:
        collection = self._collection(index, create=False)
        if collection is None:
            return
        try:
            collection.delete(where={"document_id": document_id})
        except Exception as exc:
            raise ChromaStoreError("vector_store_failed", "V3 document prune failed") from exc
        if self._read(index, document_id):
            raise ChromaStoreError("vector_store_failed", "pruned document still has vectors")

    def restore(self, index: IndexIdentity, snapshot: VectorSnapshot) -> None:
        if snapshot.index_identity != index or _snapshot_digest(snapshot) != snapshot.sha256:
            raise ChromaStoreError("vector_record_invalid", "invalid recovery vector snapshot")
        records = tuple(self._record(index, record_id, text, encode_metadata(metadata), vector)
                        for record_id, text, metadata, vector in zip(
                            snapshot.ids, snapshot.documents, snapshot.metadatas, snapshot.embeddings))
        if not snapshot.collection_existed:
            client = self._client_for(create=False)
            if client is not None and self._collection(index, create=False) is not None:
                client.delete_collection(index.collection_name)
            if self._collection(index, create=False) is not None:
                raise ChromaStoreError("vector_store_failed", "nonexistent collection restore failed")
            return
        if snapshot.document_scope:
            for document_id in snapshot.document_scope:
                self.replace_document(index, document_id,
                                      tuple(item for item in records if item.document_id == document_id))
        else:
            self.replace_collection(index, records)
        if not _snapshots_equal(snapshot, self.snapshot(index, snapshot.document_scope or None)):
            raise ChromaStoreError("vector_store_failed", "vector restore readback differs")
