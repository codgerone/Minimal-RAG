from pathlib import Path

import pytest

from rag.models import (
    ArtifactStageResult, ChunkSource, DocumentBuildResult, DocumentBuildStats,
    SourceDocument, TextChunk, V2DocumentChunk, V2RetrievalHit,
)
from rag.v2.common import BoundingBox, PageSpan
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


def test_list_records_returns_sorted_text_and_metadata_without_embeddings(
    store: ChromaVectorStore,
) -> None:
    chunks = [
        _chunk("b-p1-c01", "b", 1, 1, "beta second"),
        _chunk("a-p1-c00", "a", 1, 0, "alpha first"),
    ]
    store.add_chunks(chunks, [[0.0, 1.0], [1.0, 0.0]])

    records = store.list_records()

    assert [item.record_id for item in records] == ["a-p1-c00", "b-p1-c01"]
    assert records[0].document == "alpha first"
    assert records[0].metadata["document_id"] == "a"
    assert not hasattr(records[0], "embedding")
    assert [item.record_id for item in store.list_records("b")] == ["b-p1-c01"]


def test_write_rejects_mismatched_embedding_count(
    store: ChromaVectorStore,
) -> None:
    with pytest.raises(Exception, match="数量不一致"):
        store.add_chunks([_chunk("a", "a", 1, 0, "alpha")], [])


def test_v2_metadata_round_trip_preserves_multi_page_sources_and_page_filter(store, tmp_path: Path) -> None:
    staging = tmp_path / "stage"
    staging.mkdir()
    paths = [staging / name for name in ("raw.json", "parsed.json", "chunks.json", "summary.json", "review.html")]
    for path in paths:
        path.write_text("x", encoding="utf-8")
    artifact = ArtifactStageResult(staging, *paths, None, 0, 1)
    source = SourceDocument("doc", "a.pdf", "documents/a.pdf", tmp_path / "a.pdf", "hash")
    chunk_source = ChunkSource("node", (
        PageSpan(1, BoundingBox(0, 0, 1, 1), "p1"), PageSpan(2, None, "p2"),
    ), 0, 4, False, "none")
    chunk = V2DocumentChunk("doc-v2-c000000", "doc", "v2", 0, "text", "text", 6,
                            (chunk_source,), None, 0, 1)
    result = DocumentBuildResult(source, "v2", "build", (chunk,), DocumentBuildStats(2, 4, 1), artifact)
    store.add_v2_build(result, ((1.0, 0.0),), "fingerprint")

    restored = store.list_chunks("doc", page=2)
    assert restored == [chunk]
    hit = store.query((1.0, 0.0), 1)[0]
    assert isinstance(hit, V2RetrievalHit)
    assert hit.page_numbers == (1, 2)
    assert hit.sources == (chunk_source,)

