from pathlib import Path

import pymupdf
import pytest

from rag.config import SelectedPipelineSettings, Settings, select_pipeline
from rag.document_registry import discover_documents
from rag.errors import IndexNotReadyError
from rag.indexer import Indexer
from rag.retriever import Retriever, query_with_stable_ties
from rag.models import RetrievalHit
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


def _settings(tmp_path: Path) -> SelectedPipelineSettings:
    documents = tmp_path / "documents"
    documents.mkdir()
    return select_pipeline(Settings(
        tmp_path, documents, tmp_path / ".rag/system-v2/chroma",
        tmp_path / ".rag/system-v2/artifacts", "fake", "revision",
        100, 10, 512, 32, 4, False, None, "llm",
    ), "v1")


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


class EqualDistanceStore:
    def __init__(self) -> None:
        self.requested: list[int] = []

    def count_all(self) -> int:
        return 8

    def count_document(self, document_id: str) -> int:
        return 8

    def query(self, embedding, top_k: int, document_id=None):
        self.requested.append(top_k)
        ids = tuple(reversed([f"chunk-{index:02d}" for index in range(top_k)]))
        return [
            RetrievalHit(item, "doc", "doc.pdf", "doc.pdf", 1, index,
                         item, 0.0)
            for index, item in enumerate(ids)
        ]


def test_stable_ties_expand_boundary_and_return_strict_k() -> None:
    store = EqualDistanceStore()

    hits = query_with_stable_ties(store, [1.0, 0.0], 3)  # type: ignore[arg-type]

    assert store.requested == [4, 8]
    assert [item.chunk_id for item in hits] == ["chunk-00", "chunk-01", "chunk-02"]


class DistinctBoundaryStore(EqualDistanceStore):
    def query(self, embedding, top_k: int, document_id=None):
        self.requested.append(top_k)
        return [
            RetrievalHit(f"chunk-{index:02d}", "doc", "doc.pdf", "doc.pdf",
                         1, index, str(index), float(index))
            for index in range(top_k)
        ]


def test_stable_ties_do_not_expand_past_distinct_boundary() -> None:
    store = DistinctBoundaryStore()

    hits = query_with_stable_ties(store, [1.0, 0.0], 3)  # type: ignore[arg-type]

    assert store.requested == [4]
    assert [item.chunk_id for item in hits] == ["chunk-00", "chunk-01", "chunk-02"]

