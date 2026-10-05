"""Plain-text assembly and page-bounded character chunk plugin implementations."""

from __future__ import annotations

from dataclasses import dataclass

from langchain_text_splitters import RecursiveCharacterTextSplitter

from rag.v3.contracts.documents import (
    ChunkBatch, ChunkSource, DocumentChunk, ParsedDocument, PrimaryDocument,
    PrimaryText, SourceDocument, TextNode,
)


SEPARATORS = ("\n\n", "\n", ". ", "; ", ", ", " ", "")


class DocumentCompositionError(ValueError):
    pass


class PlainPageComposer:
    """The no-table path of the ordered composer, with explicit source checks."""

    def compose(self, source: SourceDocument, primary: PrimaryDocument) -> ParsedDocument:
        if primary.document_id != source.document_id or primary.file_hash != source.file_hash:
            raise DocumentCompositionError("primary and source identities disagree")
        if primary.table_slots or primary.native_tables or any(not isinstance(item, PrimaryText)
                                                             for item in primary.elements):
            raise DocumentCompositionError("plain page composer received structured elements")
        nodes = tuple(TextNode(item.element_id, item.kind, item.text, item.sources,
                               item.source_ref) for item in primary.elements)
        return ParsedDocument(source.document_id, source.document_name, source.relative_path,
                              source.file_hash, nodes, primary.warnings)


@dataclass(frozen=True)
class PageCharacterParameters:
    chunk_size_characters: int = 300
    chunk_overlap_characters: int = 50

    def __post_init__(self) -> None:
        if (type(self.chunk_size_characters) is not int or self.chunk_size_characters <= 0 or
                type(self.chunk_overlap_characters) is not int or
                not 0 <= self.chunk_overlap_characters < self.chunk_size_characters):
            raise ValueError("invalid character chunk parameters")


class PageCharacterChunker:
    def __init__(self, parameters: PageCharacterParameters):
        self.parameters = parameters
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=parameters.chunk_size_characters,
            chunk_overlap=parameters.chunk_overlap_characters,
            length_function=len, keep_separator="end", separators=list(SEPARATORS))

    def chunk(self, document: ParsedDocument) -> ChunkBatch:
        chunks: list[DocumentChunk] = []
        character_count = 0
        previous_page = 0
        for node in document.nodes:
            if not isinstance(node, TextNode) or node.kind != "paragraph" or len(node.sources) != 1:
                raise ValueError("page character chunker requires one page per text node")
            page = node.sources[0].page_number
            if page <= previous_page:
                raise ValueError("plain text pages are not strictly increasing")
            previous_page = page
            character_count += len(node.text)
            texts: list[str] = []
            for candidate in self.splitter.split_text(node.text):
                text = candidate.strip()
                if text and (not texts or texts[-1] != text):
                    texts.append(text)
            cursor = 0
            for page_index, text in enumerate(texts):
                start = node.text.find(text, cursor)
                if start < 0:
                    raise ValueError("splitter text does not map to source page")
                end = start + len(text)
                cursor = start + 1
                chunks.append(DocumentChunk(
                    f"{document.document_id}-p{page}-c{page_index:02d}",
                    document.document_id, len(chunks), "text", text, None,
                    (ChunkSource(node.node_id, node.sources, start, end, False, "none"),),
                    None, 0, 1))
        return ChunkBatch(document.document_id, document.file_hash, tuple(chunks),
                          character_count, ())
