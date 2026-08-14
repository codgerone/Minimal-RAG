from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pymupdf
import pytest

from rag.config import Settings
from rag.document_registry import discover_documents
from rag.errors import ManifestError
from rag.indexer import Indexer
from rag.manifest import load_manifest, validate_index
from rag.vector_store import ChromaVectorStore


class FakeEmbedder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [
            [float((sum(map(ord, text)) % 97) + 1), float(len(text) + 1)]
            for text in texts
        ]


def _write_pdf(path: Path, texts: list[str]) -> None:
    with pymupdf.open() as pdf:
        for text in texts:
            page = pdf.new_page()
            page.insert_text((72, 72), text)
        pdf.save(path)


def _settings(tmp_path: Path) -> Settings:
    documents = tmp_path / "documents"
    documents.mkdir()
    return Settings(
        project_root=tmp_path,
        documents_dir=documents,
        db_path=tmp_path / ".rag" / "chroma",
        manifest_path=tmp_path / ".rag" / "manifest.json",
        collection_name="test_collection",
        embedding_model="fake",
        chunk_size=80,
        chunk_overlap=10,
        top_k=2,
        openrouter_api_key=None,
        openrouter_model="llm",
    )


def _indexer(settings: Settings, embedder: FakeEmbedder) -> Indexer:
    return Indexer(
        settings,
        embedder,  # type: ignore[arg-type]
        ChromaVectorStore(settings.db_path, settings.collection_name),
        clock=lambda: datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
    )


def test_incremental_ingest_skips_unchanged_and_updates_only_changed(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _write_pdf(settings.documents_dir / "a.pdf", ["alpha"])
    _write_pdf(settings.documents_dir / "b.pdf", ["beta"])
    embedder = FakeEmbedder()
    indexer = _indexer(settings, embedder)

    first = indexer.ingest_all()
    assert first.added == 2
    assert len(embedder.calls) == 2

    second = indexer.ingest_all()
    assert second.skipped == 2
    assert len(embedder.calls) == 2

    _write_pdf(settings.documents_dir / "a.pdf", ["alpha changed"])
    third = indexer.ingest_all()
    assert third.updated == 1
    assert third.skipped == 1
    assert len(embedder.calls) == 3

    manifest = load_manifest(settings.manifest_path)
    assert manifest is not None
    store = ChromaVectorStore(settings.db_path, settings.collection_name)
    health = validate_index(
        settings, discover_documents(settings.documents_dir), manifest, store
    )
    assert health.usable


def test_prune_removes_only_missing_index_records(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    a = settings.documents_dir / "a.pdf"
    b = settings.documents_dir / "b.pdf"
    _write_pdf(a, ["alpha"])
    _write_pdf(b, ["beta"])
    embedder = FakeEmbedder()
    indexer = _indexer(settings, embedder)
    indexer.ingest_all()
    manifest_before = load_manifest(settings.manifest_path)
    assert manifest_before is not None
    b_id = next(
        key
        for key, value in manifest_before.documents.items()
        if value.relative_path == "b.pdf"
    )

    b.unlink()
    without_prune = indexer.ingest_all()
    assert without_prune.missing == 1
    assert indexer.vector_store.count_document(b_id) > 0

    with_prune = indexer.ingest_all(prune=True)
    assert with_prune.pruned == 1
    assert indexer.vector_store.count_document(b_id) == 0
    assert a.exists()


def test_force_prebuild_failure_preserves_old_collection(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_pdf(settings.documents_dir / "good.pdf", ["good text"])
    embedder = FakeEmbedder()
    indexer = _indexer(settings, embedder)
    indexer.ingest_all()
    old_count = indexer.vector_store.count_all()
    (settings.documents_dir / "broken.pdf").write_bytes(b"not a pdf")

    summary = indexer.ingest_all(force=True)

    assert summary.failed == 1
    assert indexer.vector_store.count_all() == old_count


def test_single_file_force_does_not_rebuild_other_document(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_pdf(settings.documents_dir / "a.pdf", ["alpha"])
    _write_pdf(settings.documents_dir / "b.pdf", ["beta"])
    embedder = FakeEmbedder()
    indexer = _indexer(settings, embedder)
    indexer.ingest_all()
    call_count = len(embedder.calls)

    result = indexer.ingest_one("a.pdf", force=True)

    assert result.updated == 1
    assert len(embedder.calls) == call_count + 1


def test_changed_global_config_is_rejected_without_force(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_pdf(settings.documents_dir / "a.pdf", ["alpha"])
    embedder = FakeEmbedder()
    _indexer(settings, embedder).ingest_all()

    changed = replace(settings, chunk_size=90)
    with pytest.raises(ManifestError) as exc_info:
        _indexer(changed, embedder).ingest_all()
    assert "--force" in (exc_info.value.remediation or "")

