"""Chunker interface: assembled document in, ordered chunks with sources out."""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from rag.models import ChunkBatch, ParsedDocument


# Rules version of chunking and chunk source regions (chunkers/, regions.py). Bump it, to the release that ships the change, whenever this
# code changes its output: the documents passing through the step are then rebuilt.
RULES_VERSION = "v4.1.1"


class Chunker(Protocol):
    def chunk(self, document: ParsedDocument) -> ChunkBatch: ...

    def identity(self) -> dict[str, object]: ...


def chunk_id(document_id: str, index: int) -> str:
    return f"{document_id}-c{index:04d}"


def renumber(batch: ChunkBatch) -> ChunkBatch:
    """Give every chunk the uniform `<document_id>-cNNNN` ID."""
    return replace(batch, chunks=tuple(replace(item, chunk_id=chunk_id(item.document_id, i))
                                       for i, item in enumerate(batch.chunks)))
