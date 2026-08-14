"""Stable, page-bounded recursive character chunking."""

from __future__ import annotations

from collections.abc import Sequence

from langchain_text_splitters import RecursiveCharacterTextSplitter

from rag.models import PageText, TextChunk


DEFAULT_SEPARATORS = ["\n\n", "\n", ". ", "; ", ", ", " ", ""]


def make_splitter(
    chunk_size: int,
    chunk_overlap: int,
) -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=len,
        keep_separator="end",
        separators=DEFAULT_SEPARATORS,
    )


def chunk_pages(
    pages: Sequence[PageText],
    file_hash: str,
    splitter: RecursiveCharacterTextSplitter,
) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    for page in pages:
        texts: list[str] = []
        for candidate in splitter.split_text(page.text):
            text = candidate.strip()
            if not text or (texts and texts[-1] == text):
                continue
            texts.append(text)

        for chunk_index, text in enumerate(texts):
            chunks.append(
                TextChunk(
                    chunk_id=(
                        f"{page.document_id}-p{page.page_number}-c{chunk_index:02d}"
                    ),
                    document_id=page.document_id,
                    document_name=page.document_name,
                    relative_path=page.relative_path,
                    page_number=page.page_number,
                    chunk_index=chunk_index,
                    text=text,
                    file_hash=file_hash,
                )
            )
    return chunks

