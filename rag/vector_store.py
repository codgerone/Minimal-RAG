"""Chroma persistence adapter using only application-provided embeddings."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import chromadb
from chromadb.errors import NotFoundError

from rag.errors import VectorStoreError
from rag.models import RetrievalHit, TextChunk


WRITE_BATCH_SIZE = 100


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

    def list_chunks(
        self,
        document_id: str,
        page: int | None = None,
    ) -> list[TextChunk]:
        if not self.collection_exists():
            return []
        where: dict[str, Any]
        if page is None:
            where = {"document_id": document_id}
        else:
            where = {
                "$and": [
                    {"document_id": document_id},
                    {"page_number": page},
                ]
            }
        try:
            result = self._get_collection().get(
                where=where, include=["documents", "metadatas"]
            )
            chunks = [
                self._chunk_from_result(chunk_id, document, metadata)
                for chunk_id, document, metadata in zip(
                    result["ids"],
                    result["documents"] or [],
                    result["metadatas"] or [],
                )
            ]
            return sorted(chunks, key=lambda item: (item.page_number, item.chunk_index))
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

    def query(
        self,
        query_embedding: Sequence[float],
        top_k: int,
        document_id: str | None = None,
    ) -> list[RetrievalHit]:
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
                RetrievalHit(
                    chunk_id=chunk_id,
                    document_id=str(metadata["document_id"]),
                    document_name=str(metadata["document_name"]),
                    relative_path=str(metadata["relative_path"]),
                    page_number=int(metadata["page_number"]),
                    chunk_index=int(metadata["chunk_index"]),
                    text=document,
                    distance=float(distance),
                )
                for chunk_id, document, metadata, distance in zip(
                    ids, documents, metadatas, distances
                )
            ]
            return sorted(hits, key=lambda item: item.distance)
        except Exception as exc:
            raise VectorStoreError("Chroma 向量检索失败。", cause=exc) from exc

