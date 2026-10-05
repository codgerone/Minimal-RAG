"""Construct common vector records from chunk and embedding facts."""

from __future__ import annotations

import hashlib

from rag.v3.contracts.documents import ChunkBatch, SourceDocument
from rag.v3.contracts.retrieval import PassageEmbeddingBatch
from rag.v3.contracts.storage import ChunkMetadata, VectorRecord


def make_vector_records(source: SourceDocument, batch: ChunkBatch,
                        embedded: PassageEmbeddingBatch) -> tuple[VectorRecord, ...]:
    if (source.document_id != batch.document_id or source.file_hash != batch.file_hash
            or embedded.document_id != source.document_id
            or embedded.file_hash != source.file_hash
            or tuple(chunk.chunk_id for chunk in batch.chunks) != embedded.chunk_ids):
        raise ValueError("chunk and embedding identity differs")
    records = []
    for chunk, vector in zip(batch.chunks, embedded.vectors):
        pages = tuple(dict.fromkeys(span.page_number for item in chunk.sources
                                    for span in item.page_spans))
        metadata = ChunkMetadata(
            "chunk_metadata_v3", source.document_id, source.document_name,
            source.relative_path, embedded.build_id, source.file_hash,
            embedded.index_identity.build_fingerprint,
            hashlib.sha256(chunk.text.encode("utf-8")).hexdigest(),
            chunk.chunk_index, chunk.kind, chunk.token_count, pages,
            chunk.sources, chunk.parent_unit_id, chunk.fragment_index, chunk.fragment_count,
        )
        records.append(VectorRecord(chunk.chunk_id, source.document_id,
                                    embedded.build_id, source.file_hash,
                                    chunk.text, metadata, vector))
    return tuple(records)
