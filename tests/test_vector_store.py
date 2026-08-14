from pathlib import Path

import pytest

from rag.models import TextChunk
from rag.vector_store import ChromaVectorStore


def _chunk(
    chunk_id: str,
    document_id: str,
    page: int,
    index: int,
    text: str,
) -> TextChunk:
    return TextChunk(
        chunk_id=chunk_id,
        document_id=document_id,
        document_name=f"{document_id}.pdf",
        relative_path=f"{document_id}.pdf",
        page_number=page,
        chunk_index=index,
        text=text,
        file_hash=f"hash-{document_id}",
    )


@pytest.fixture
def store(tmp_path: Path) -> ChromaVectorStore:
    return ChromaVectorStore(tmp_path / "chroma", "test_collection")


def test_explicit_vectors_persist_query_filter_and_delete(
    store: ChromaVectorStore,
) -> None:
    chunks = [
        _chunk("a-p1-c00", "a", 1, 0, "alpha"),
        _chunk("b-p2-c00", "b", 2, 0, "beta"),
    ]
    store.add_chunks(chunks, [[1.0, 0.0], [0.0, 1.0]])

    assert store.count_all() == 2
    assert store.count_document("a") == 1
    assert [hit.document_id for hit in store.query([1.0, 0.0], 2)] == ["a", "b"]
    assert [hit.document_id for hit in store.query([1.0, 0.0], 2, "b")] == ["b"]
    assert store.query([1.0, 0.0], 2)[0].similarity == pytest.approx(1.0)

    reopened = ChromaVectorStore(store.db_path, store.collection_name)
    assert reopened.list_chunks("b")[0].text == "beta"
    reopened.delete_document("a")
    assert reopened.count_all() == 1
    assert reopened.count_document("a") == 0


def test_write_rejects_mismatched_embedding_count(
    store: ChromaVectorStore,
) -> None:
    with pytest.raises(Exception, match="数量不一致"):
        store.add_chunks([_chunk("a", "a", 1, 0, "alpha")], [])

