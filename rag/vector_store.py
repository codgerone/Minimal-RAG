"""Chroma persistence adapter using only application-provided embeddings."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chromadb
import json
from chromadb.errors import NotFoundError

from rag.errors import VectorStoreError
from rag.models import ChunkSource, DocumentBuildResult, RetrievalHit, TextChunk, V2DocumentChunk, V2RetrievalHit
from rag.v2.common import BoundingBox, PageSpan


WRITE_BATCH_SIZE = 100


@dataclass(frozen=True)
class StoredRecord:
    record_id: str
    document: str
    metadata: dict[str, Any]


class ChromaVectorStore:
    def __init__(self, db_path: Path, collection_name: str) -> None:
        self.db_path = db_path
        self.collection_name = collection_name
        self._client = None

    def _get_client(self, *, create: bool):
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
            raise VectorStoreError(
                f"无法打开 Chroma 数据库：{self.db_path}",
                "请检查目录权限和磁盘空间。",
                cause=exc,
            ) from exc

    def collection_exists(self) -> bool:
        client = self._get_client(create=False)
        if client is None:
            return False
        try:
            client.get_collection(self.collection_name)
            return True
        except NotFoundError:
            return False
        except Exception as exc:
            raise VectorStoreError(
                f"无法读取 Collection {self.collection_name!r}。",
                "请检查 Chroma 数据目录权限和完整性。",
                cause=exc,
            ) from exc

    def get_or_create_collection(self):
        try:
            return self._get_client(create=True).get_or_create_collection(
                name=self.collection_name,
                configuration={"hnsw": {"space": "cosine"}},
            )
        except Exception as exc:
            raise VectorStoreError(
                f"无法创建或读取 Collection {self.collection_name!r}。",
                "请检查 Chroma 数据目录；必要时执行 ingest --force。",
                cause=exc,
            ) from exc

    def _get_collection(self):
        client = self._get_client(create=False)
        if client is None:
            raise VectorStoreError(
                f"Collection {self.collection_name!r} 不存在或不可读。",
                "请先执行 python -m rag ingest。",
            )
        try:
            return client.get_collection(self.collection_name)
        except Exception as exc:
            raise VectorStoreError(
                f"Collection {self.collection_name!r} 不存在或不可读。",
                "请先执行 python -m rag ingest。",
                cause=exc,
            ) from exc

    def recreate_collection(self):
        try:
            client = self._get_client(create=True)
            if self.collection_exists():
                client.delete_collection(self.collection_name)
            return self.get_or_create_collection()
        except VectorStoreError:
            raise
        except Exception as exc:
            raise VectorStoreError(
                f"无法重建 Collection {self.collection_name!r}。",
                "请检查 Chroma 数据目录后重试 ingest --force。",
                cause=exc,
            ) from exc

    def add_chunks(
        self,
        chunks: Sequence[TextChunk],
        embeddings: Sequence[Sequence[float]],
    ) -> None:
        if len(chunks) != len(embeddings):
            raise VectorStoreError("chunks 与 embeddings 数量不一致，拒绝写入。")
        if not chunks:
            raise VectorStoreError("没有可写入的 chunk。")
        collection = self.get_or_create_collection()
        try:
            for start in range(0, len(chunks), WRITE_BATCH_SIZE):
                batch_chunks = chunks[start : start + WRITE_BATCH_SIZE]
                batch_embeddings = embeddings[start : start + WRITE_BATCH_SIZE]
                collection.add(
                    ids=[chunk.chunk_id for chunk in batch_chunks],
                    documents=[chunk.text for chunk in batch_chunks],
                    embeddings=[list(vector) for vector in batch_embeddings],
                    metadatas=[
                        {
                            "document_id": chunk.document_id,
                            "document_name": chunk.document_name,
                            "relative_path": chunk.relative_path,
                            "page_number": chunk.page_number,
                            "chunk_index": chunk.chunk_index,
                            "file_hash": chunk.file_hash,
                        }
                        for chunk in batch_chunks
                    ],
                )
        except Exception as exc:
            raise VectorStoreError(
                "写入 Chroma 失败。",
                "请重新执行 ingest；系统不会把本次写入记录为成功。",
                cause=exc,
            ) from exc

    def add_v2_build(
        self,
        result: DocumentBuildResult,
        embeddings: Sequence[Sequence[float]],
        build_config_fingerprint: str,
    ) -> None:
        chunks = result.chunks
        if result.pipeline_id != "v2" or any(not isinstance(item, V2DocumentChunk) for item in chunks):
            raise VectorStoreError("add_v2_build 只接受 V2 构建结果。")
        if len(chunks) != len(embeddings) or not chunks:
            raise VectorStoreError("V2 chunks 与 embeddings 数量不一致或为空。")
        collection = self.get_or_create_collection()
        try:
            for start in range(0, len(chunks), WRITE_BATCH_SIZE):
                batch = chunks[start:start + WRITE_BATCH_SIZE]
                vectors = embeddings[start:start + WRITE_BATCH_SIZE]
                metadatas = []
                for chunk in batch:
                    page_numbers = sorted({span.page_number for source in chunk.sources for span in source.page_spans})
                    node_ids = list(dict.fromkeys(source.node_id for source in chunk.sources))
                    sources = [
                        {
                            "node_id": source.node_id,
                            "page_spans": [
                                {"page_number": span.page_number,
                                 "bbox": None if span.bbox is None else {
                                     "x0": span.bbox.x0, "y0": span.bbox.y0,
                                     "x1": span.bbox.x1, "y1": span.bbox.y1,
                                     "coordinate_space": span.bbox.coordinate_space,
                                 }, "source_ref": span.source_ref}
                                for span in source.page_spans
                            ],
                            "source_text_start": source.source_text_start,
                            "source_text_end": source.source_text_end,
                            "repeated_context": source.repeated_context,
                            "context_kind": source.context_kind,
                        } for source in chunk.sources
                    ]
                    canonical = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    metadatas.append({
                        "schema_version": "vector_metadata_v2", "pipeline_id": "v2",
                        "document_id": result.source.document_id,
                        "document_name": result.source.document_name,
                        "relative_path": result.source.relative_path,
                        "file_hash": result.source.file_hash, "build_id": result.build_id,
                        "build_config_fingerprint": build_config_fingerprint,
                        "chunk_index": chunk.chunk_index, "chunk_kind": chunk.kind,
                        "token_count": chunk.token_count,
                        "page_numbers_json": canonical(page_numbers),
                        "node_ids_json": canonical(node_ids), "sources_json": canonical(sources),
                        "parent_unit_id": chunk.parent_unit_id or "",
                        "fragment_index": chunk.fragment_index, "fragment_count": chunk.fragment_count,
                    })
                collection.add(ids=[item.chunk_id for item in batch],
                               documents=[item.text for item in batch],
                               embeddings=[list(item) for item in vectors], metadatas=metadatas)
        except Exception as exc:
            raise VectorStoreError("写入 V2 Chroma 记录失败。", cause=exc) from exc

    def delete_document(self, document_id: str) -> None:
        if not self.collection_exists():
            return
        try:
            collection = self._get_collection()
            collection.delete(where={"document_id": document_id})
            if self.count_document(document_id) != 0:
                raise VectorStoreError(f"文档 {document_id} 的向量记录未完全删除。")
        except VectorStoreError:
            raise
        except Exception as exc:
            raise VectorStoreError(
                f"删除文档 {document_id} 的向量记录失败。",
                "请重试 ingest；若持续失败，请执行 ingest --force。",
                cause=exc,
            ) from exc

    def count_document(self, document_id: str) -> int:
        if not self.collection_exists():
            return 0
        try:
            result = self._get_collection().get(
                where={"document_id": document_id}, include=[]
            )
            return len(result["ids"])
        except Exception as exc:
            raise VectorStoreError(
                f"无法统计文档 {document_id} 的向量记录。", cause=exc
            ) from exc

    def count_all(self) -> int:
        if not self.collection_exists():
            return 0
        try:
            return int(self._get_collection().count())
        except Exception as exc:
            raise VectorStoreError("无法统计 Collection 记录数。", cause=exc) from exc

    def get_metadatas(self, document_id: str | None = None) -> list[dict[str, Any]]:
        if not self.collection_exists():
            return []
        try:
            kwargs: dict[str, Any] = {"include": ["metadatas"]}
            if document_id is not None:
                kwargs["where"] = {"document_id": document_id}
            result = self._get_collection().get(**kwargs)
            return [dict(item) for item in result["metadatas"] or []]
        except Exception as exc:
            raise VectorStoreError("无法读取 Chroma metadata。", cause=exc) from exc

    def list_records(self, document_id: str | None = None) -> list[StoredRecord]:
        """Read human-facing record fields without loading embedding vectors."""
        if not self.collection_exists():
            return []
        try:
            kwargs: dict[str, Any] = {"include": ["documents", "metadatas"]}
            if document_id is not None:
                kwargs["where"] = {"document_id": document_id}
            result = self._get_collection().get(**kwargs)
            ids = list(result.get("ids") or [])
            documents = list(result.get("documents") or [])
            metadatas = list(result.get("metadatas") or [])
            if len({len(ids), len(documents), len(metadatas)}) != 1:
                raise VectorStoreError("Chroma records 数组长度不一致。")
            records = [
                StoredRecord(str(record_id), str(document), dict(metadata))
                for record_id, document, metadata in zip(ids, documents, metadatas)
            ]
            return sorted(
                records,
                key=lambda item: (
                    str(item.metadata.get("relative_path", "")).casefold(),
                    int(item.metadata.get("chunk_index", 0)),
                    item.record_id,
                ),
            )
        except VectorStoreError:
            raise
        except Exception as exc:
            raise VectorStoreError("无法读取 Chroma records。", cause=exc) from exc

    def snapshot(self, document_id: str | None = None) -> dict[str, Any]:
        if not self.collection_exists():
            return {"ids": [], "documents": [], "embeddings": [], "metadatas": []}
        try:
            kwargs: dict[str, Any] = {"include": ["documents", "embeddings", "metadatas"]}
            if document_id is not None:
                kwargs["where"] = {"document_id": document_id}
            result = self._get_collection().get(**kwargs)
            values = {
                "ids": list(result.get("ids") or []),
                "documents": list(result.get("documents") or []),
                "embeddings": result.get("embeddings"),
                "metadatas": list(result.get("metadatas") or []),
            }
            embeddings = values["embeddings"]
            values["embeddings"] = embeddings.tolist() if hasattr(embeddings, "tolist") else list(embeddings or [])
            lengths = {len(values[key]) for key in values}
            if len(lengths) != 1 or len(values["ids"]) != len(set(values["ids"])):
                raise VectorStoreError("Chroma snapshot 数组长度或 ID 唯一性校验失败。")
            dimensions = {len(item) for item in values["embeddings"]}
            if len(dimensions) > 1:
                raise VectorStoreError("Chroma snapshot embedding 维度不一致。")
            return values
        except VectorStoreError:
            raise
        except Exception as exc:
            raise VectorStoreError("无法创建 Chroma snapshot。", cause=exc) from exc

    def restore_snapshot(self, snapshot: dict[str, Any], *, recreate: bool = False) -> None:
        try:
            collection = self.recreate_collection() if recreate else self.get_or_create_collection()
            if not recreate and snapshot["metadatas"]:
                document_ids = {str(item["document_id"]) for item in snapshot["metadatas"]}
                for document_id in document_ids:
                    collection.delete(where={"document_id": document_id})
            if snapshot["ids"]:
                collection.add(ids=snapshot["ids"], documents=snapshot["documents"],
                               embeddings=snapshot["embeddings"], metadatas=snapshot["metadatas"])
            restored = self.snapshot(None if recreate else (
                str(snapshot["metadatas"][0]["document_id"]) if snapshot["metadatas"] else None
            ))
            if not recreate and not snapshot["ids"]:
                return
            if set(restored["ids"]) != set(snapshot["ids"]):
                raise VectorStoreError("Chroma snapshot 恢复后 ID 校验失败。")
        except VectorStoreError:
            raise
        except Exception as exc:
            raise VectorStoreError("恢复 Chroma snapshot 失败。", cause=exc) from exc

    def list_chunks(
        self,
        document_id: str,
        page: int | None = None,
    ) -> list[TextChunk | V2DocumentChunk]:
        if not self.collection_exists():
            return []
        where: dict[str, Any] = {"document_id": document_id}
        try:
            result = self._get_collection().get(
                where=where, include=["documents", "metadatas"]
            )
            chunks = [
                self._chunk_from_any_result(chunk_id, document, metadata)
                for chunk_id, document, metadata in zip(
                    result["ids"],
                    result["documents"] or [],
                    result["metadatas"] or [],
                )
            ]
            if page is not None:
                chunks = [item for item in chunks if (
                    item.page_number == page if isinstance(item, TextChunk) else
                    page in {span.page_number for source in item.sources for span in source.page_spans}
                )]
            return sorted(chunks, key=lambda item: item.chunk_index)
        except Exception as exc:
            raise VectorStoreError("读取已索引 chunks 失败。", cause=exc) from exc

    @staticmethod
    def _chunk_from_result(
        chunk_id: str,
        document: str,
        metadata: dict[str, Any],
    ) -> TextChunk:
        return TextChunk(
            chunk_id=chunk_id,
            document_id=str(metadata["document_id"]),
            document_name=str(metadata["document_name"]),
            relative_path=str(metadata["relative_path"]),
            page_number=int(metadata["page_number"]),
            chunk_index=int(metadata["chunk_index"]),
            text=document,
            file_hash=str(metadata["file_hash"]),
        )

    @classmethod
    def _chunk_from_any_result(cls, chunk_id: str, document: str, metadata: dict[str, Any]):
        if metadata.get("schema_version") != "vector_metadata_v2":
            return cls._chunk_from_result(chunk_id, document, metadata)
        try:
            sources = tuple(ChunkSource(
                str(item["node_id"]),
                tuple(PageSpan(
                    int(span["page_number"]),
                    None if span["bbox"] is None else BoundingBox(**span["bbox"]),
                    str(span["source_ref"]),
                ) for span in item["page_spans"]),
                item["source_text_start"], item["source_text_end"],
                bool(item["repeated_context"]), str(item["context_kind"]),
            ) for item in json.loads(str(metadata["sources_json"])))
            return V2DocumentChunk(
                chunk_id, str(metadata["document_id"]), "v2", int(metadata["chunk_index"]),
                str(metadata["chunk_kind"]), document, int(metadata["token_count"]), sources,
                str(metadata["parent_unit_id"]) or None, int(metadata["fragment_index"]),
                int(metadata["fragment_count"]),
            )
        except Exception as exc:
            raise VectorStoreError("V2 vector metadata 无法解码。", cause=exc) from exc

    def query(
        self,
        query_embedding: Sequence[float],
        top_k: int,
        document_id: str | None = None,
    ) -> list[RetrievalHit | V2RetrievalHit]:
        available = (
            self.count_document(document_id)
            if document_id is not None
            else self.count_all()
        )
        if available == 0:
            return []
        kwargs: dict[str, Any] = {
            "query_embeddings": [list(query_embedding)],
            "n_results": min(top_k, available),
            "include": ["documents", "metadatas", "distances"],
        }
        if document_id is not None:
            kwargs["where"] = {"document_id": document_id}
        try:
            result = self._get_collection().query(**kwargs)
            ids = result["ids"][0]
            documents = (result["documents"] or [[]])[0]
            metadatas = (result["metadatas"] or [[]])[0]
            distances = (result["distances"] or [[]])[0]
            hits = [
                self._hit_from_result(chunk_id, document, metadata, float(distance))
                for chunk_id, document, metadata, distance in zip(
                    ids, documents, metadatas, distances
                )
            ]
            return sorted(hits, key=lambda item: (item.distance, item.chunk_id))
        except Exception as exc:
            raise VectorStoreError("Chroma 向量检索失败。", cause=exc) from exc

    @classmethod
    def _hit_from_result(cls, chunk_id: str, document: str, metadata: dict[str, Any], distance: float):
        chunk = cls._chunk_from_any_result(chunk_id, document, metadata)
        if isinstance(chunk, TextChunk):
            return RetrievalHit(chunk.chunk_id, chunk.document_id, chunk.document_name,
                                chunk.relative_path, chunk.page_number, chunk.chunk_index,
                                chunk.text, distance)
        return V2RetrievalHit(
            chunk.chunk_id, chunk.document_id, str(metadata["document_name"]),
            str(metadata["relative_path"]), chunk.chunk_index, chunk.kind,
            chunk.text, distance,
            tuple(int(value) for value in json.loads(str(metadata["page_numbers_json"]))),
            chunk.sources,
        )

