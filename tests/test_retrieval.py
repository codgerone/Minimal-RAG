from pathlib import Path

import pymupdf
import pytest

from rag.config import Settings
from rag.document_registry import discover_documents
from rag.errors import IndexNotReadyError
from rag.indexer import Indexer
from rag.retriever import Retriever
from rag.vector_store import ChromaVectorStore


class ControlledEmbedder:
    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [
            [1.0, 0.0] if "alpha" in text else [0.0, 1.0] for text in texts
        ]

    def embed_query(self, question: str) -> list[float]:
        return [1.0, 0.0] if "alpha" in question else [0.0, 1.0]


def _pdf(path: Path, text: str) -> None:
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), text)
        pdf.save(path)


def _settings(tmp_path: Path) -> Settings:
    documents = tmp_path / "documents"
    documents.mkdir()
    return Settings(
        tmp_path,
        documents,
        tmp_path / ".rag" / "chroma",
        tmp_path / ".rag" / "manifest.json",
        "collection",
        "fake",
        100,
        10,
        4,
        None,
        "llm",
    )


def test_retrieval_crosses_documents_filters_and_sorts(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _pdf(settings.documents_dir / "a.pdf", "alpha content")
    _pdf(settings.documents_dir / "b.pdf", "beta content")
    embedder = ControlledEmbedder()
    store = ChromaVectorStore(settings.db_path, settings.collection_name)
    Indexer(settings, embedder, store).ingest_all()  # type: ignore[arg-type]
    retriever = Retriever(settings, embedder, store)  # type: ignore[arg-type]

    hits = retriever.search("alpha question", top_k=2)
    filtered = retriever.search(
        "alpha question", top_k=2, document_selector="b.pdf"
    )

    assert [hit.document_name for hit in hits] == ["a.pdf", "b.pdf"]
    assert [hit.document_name for hit in filtered] == ["b.pdf"]
    assert hits[0].distance <= hits[1].distance
    assert hits[0].similarity == pytest.approx(1.0)


def test_retrieval_refuses_stale_index(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _pdf(settings.documents_dir / "a.pdf", "alpha content")
    embedder = ControlledEmbedder()
    store = ChromaVectorStore(settings.db_path, settings.collection_name)
    Indexer(settings, embedder, store).ingest_all()  # type: ignore[arg-type]
    _pdf(settings.documents_dir / "b.pdf", "beta content")

    with pytest.raises(IndexNotReadyError, match="尚未索引"):
        Retriever(settings, embedder, store).search("alpha")  # type: ignore[arg-type]

