"""Chunker port implementations bound to validated build context."""

from __future__ import annotations

from typing import Protocol
from rag.v3.application.plain_document import PageCharacterChunker, PageCharacterParameters
from rag.v3.application.structured_chunking import chunk_parsed_document
from rag.v3.contracts.documents import ChunkBatch, ParsedDocument
from rag.v3.contracts.processing import ChunkingContext
from rag.v3.contracts.retrieval import EmbeddingIdentity
from rag.v3.contracts.storage import IndexIdentity


class PassageCounter(Protocol):
    identity: EmbeddingIdentity
    def count_passage(self, text: str) -> int: ...
    def count_text(self, text: str) -> int: ...


class CharacterChunker:
    def __init__(self, size: int = 300, overlap: int = 50):
        self.parameters = PageCharacterParameters(size, overlap)

    def context(self, index: IndexIdentity, build_id: str,
                identity: EmbeddingIdentity) -> ChunkingContext:
        return ChunkingContext(index, build_id, identity,
                               self.parameters.chunk_size_characters,
                               self.parameters.chunk_overlap_characters, None, None)

    def chunk(self, document: ParsedDocument, context: ChunkingContext) -> ChunkBatch:
        if context.character_size is None or context.character_overlap is None:
            raise ValueError("character chunker received token context")
        if (context.character_size != self.parameters.chunk_size_characters
                or context.character_overlap != self.parameters.chunk_overlap_characters):
            raise ValueError("character chunk context differs from bound plugin")
        return PageCharacterChunker(PageCharacterParameters(
            context.character_size, context.character_overlap)).chunk(document)


class StructuredChunker:
    def __init__(self, maximum_input_tokens: int = 512, text_overlap_tokens: int = 32,
                 counter: PassageCounter | None = None):
        if maximum_input_tokens <= 0 or not 0 <= text_overlap_tokens < maximum_input_tokens:
            raise ValueError("invalid structured chunk parameters")
        self.maximum_input_tokens = maximum_input_tokens
        self.text_overlap_tokens = text_overlap_tokens
        self.counter = counter

    def context(self, index: IndexIdentity, build_id: str,
                identity: EmbeddingIdentity) -> ChunkingContext:
        return ChunkingContext(index, build_id, identity, None, None,
                               self.maximum_input_tokens, self.text_overlap_tokens)

    def chunk(self, document: ParsedDocument, context: ChunkingContext) -> ChunkBatch:
        if context.maximum_input_tokens is None or context.text_overlap_tokens is None:
            raise ValueError("structured chunker received character context")
        if (context.maximum_input_tokens != self.maximum_input_tokens
                or context.text_overlap_tokens != self.text_overlap_tokens):
            raise ValueError("structured chunk context differs from bound plugin")
        counter = self.counter
        if counter is None:
            raise ValueError("structured tokenizer was not bound by the runtime")
        if counter.identity != context.embedding_identity:
            raise ValueError("structured tokenizer and embedder identity differ")
        self.counter = counter
        return chunk_parsed_document(document, counter,
                                     max_input_tokens=context.maximum_input_tokens,
                                     overlap_tokens=context.text_overlap_tokens)
