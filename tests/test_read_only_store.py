from pathlib import Path

from rag.vector_store import ChromaVectorStore


def test_read_only_checks_do_not_create_database_directory(tmp_path: Path) -> None:
    db_path = tmp_path / "missing" / "chroma"
    store = ChromaVectorStore(db_path, "collection")

    assert not store.collection_exists()
    assert store.count_all() == 0
    assert not db_path.exists()

