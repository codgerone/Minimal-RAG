"""Recursive character splitting inside each page; pages never share a chunk."""

from __future__ import annotations

from langchain_text_splitters import RecursiveCharacterTextSplitter

from rag.ingest.chunkers import chunk_id
from rag.models import ChunkBatch, ChunkSource, DocumentChunk, ParsedDocument, TextNode

SEPARATORS = ("\n\n", "\n", ". ", "; ", ", ", " ", "")


class CharacterChunker:
    def __init__(self, chunk_size: int = 300, chunk_overlap: int = 50):
        if chunk_size <= 0 or not 0 <= chunk_overlap < chunk_size:
            raise ValueError("chunk_size 必须为正，且 0 ≤ chunk_overlap < chunk_size")
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap,
            length_function=len, keep_separator="end", separators=list(SEPARATORS))

    def chunk(self, document: ParsedDocument) -> ChunkBatch:
        chunks: list[DocumentChunk] = []
        character_count = 0
        for node in document.nodes:
            if not isinstance(node, TextNode) or len(node.sources) != 1:
                raise ValueError("characters 分块器要求每个节点是单页文本（搭配 pymupdf_pages 解析器）")
            character_count += len(node.text)
            texts: list[str] = []
            for candidate in self.splitter.split_text(node.text):
                text = candidate.strip()
                if text and (not texts or texts[-1] != text):
                    texts.append(text)
            cursor = 0
            for text in texts:
                start = node.text.find(text, cursor)
                if start < 0:
                    raise ValueError("分块文本无法对应回页面原文")
                cursor = start + 1
                chunks.append(DocumentChunk(
                    chunk_id(document.document_id, len(chunks)), document.document_id,
                    len(chunks), "text", text, None,
                    (ChunkSource(node.node_id, node.sources, start, start + len(text), False, "none"),),
                    None, 0, 1))
        return ChunkBatch(document.document_id, document.file_hash, tuple(chunks),
                          character_count, ())
