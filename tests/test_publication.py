from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from rag.build_config import (
    EmbeddingConfig, TokenizerConfig, V1BuildConfig, V1ChunkerConfig,
    V1DependencyVersions, V1ParserConfig,
)
from rag.models import BuiltDocument, DocumentBuildResult, DocumentBuildStats, SourceDocument, TextChunk
from rag.pipeline_manifest import PipelineManifest
from rag.pipeline_registry import build_config_fingerprint
from rag.publication import IndexPublicationError, PublicationManager


class FakeStore:
    def __init__(self, records=None):
        self.records = dict(records or {})

    def snapshot(self, document_id=None):
        records = [value for value in self.records.values()
                   if document_id is None or value[2]["document_id"] == document_id]
        return {"ids": [item[0] for item in records], "documents": [item[1] for item in records],
                "embeddings": [item[3] for item in records], "metadatas": [item[2] for item in records]}

    def delete_document(self, document_id):
        self.records = {key: value for key, value in self.records.items()
                        if value[2]["document_id"] != document_id}

    def add_chunks(self, chunks, embeddings):
        for chunk, vector in zip(chunks, embeddings):
            metadata = {"document_id": chunk.document_id, "document_name": chunk.document_name,
                        "relative_path": chunk.relative_path, "page_number": chunk.page_number,
                        "chunk_index": chunk.chunk_index, "file_hash": chunk.file_hash}
            self.records[chunk.chunk_id] = (chunk.chunk_id, chunk.text, metadata, list(vector))

    def restore_snapshot(self, snapshot, recreate=False):
        if recreate:
            self.records = {}
        for identifier, text, metadata, vector in zip(snapshot["ids"], snapshot["documents"],
                                                       snapshot["metadatas"], snapshot["embeddings"]):
            self.records[identifier] = (identifier, text, metadata, vector)

    def recreate_collection(self):
        self.records = {}

    def count_document(self, document_id):
        return sum(value[2]["document_id"] == document_id for value in self.records.values())

    def count_all(self):
        return len(self.records)


def _runtime(root: Path):
    config = V1BuildConfig(V1ParserConfig(), V1ChunkerConfig(700, 100),
                           TokenizerConfig("model", "revision"), EmbeddingConfig("model", "revision", 2),
                           V1DependencyVersions("1", "1", "1", "1", "1"))
    return SimpleNamespace(pipeline_id="v1", collection_name="minimal_rag_documents_v1",
                           manifest_path=root / ".rag/system-v2/pipelines/v1/manifest.json", build_config=config,
                           build_config_fingerprint=build_config_fingerprint(config))


def _built(root: Path, text: str = "new") -> BuiltDocument:
    source = SourceDocument("doc", "a.pdf", "documents/a.pdf", root / "a.pdf", "newhash")
    chunk = TextChunk("new-id", "doc", "a.pdf", "documents/a.pdf", 1, 0, text, "newhash")
    result = DocumentBuildResult(source, "v1", "build", (chunk,), DocumentBuildStats(1, len(text), 1), None)
    return BuiltDocument(result, ((1.0, 0.0),))


def _manifest(runtime) -> PipelineManifest:
    return PipelineManifest("v1", runtime.collection_name, runtime.build_config,
                            runtime.build_config_fingerprint, {}, "2026-09-16T00:00:00Z", "2026-09-16T00:00:00Z")


def test_document_publication_persists_manifest_after_vector_verification(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    store = FakeStore()
    manager = PublicationManager(project_root=tmp_path, runtime=runtime, vector_store=store,
                                 clock=lambda: datetime.now(UTC))
    updated = manager.replace_document(_manifest(runtime), _built(tmp_path), "2026-09-16T00:00:01Z")
    assert set(store.records) == {"new-id"}
    assert updated.documents["doc"].build_id == "build"
    assert runtime.manifest_path.is_file()
    assert not any((tmp_path / ".rag/system-v2/recovery/v1").iterdir())


def test_manifest_failure_restores_old_vectors_and_manifest_bytes(tmp_path: Path, monkeypatch) -> None:
    runtime = _runtime(tmp_path)
    runtime.manifest_path.parent.mkdir(parents=True)
    runtime.manifest_path.write_bytes(b"old-manifest")
    old_metadata = {"document_id": "doc", "document_name": "a.pdf", "relative_path": "documents/a.pdf",
                    "page_number": 1, "chunk_index": 0, "file_hash": "oldhash"}
    store = FakeStore({"old-id": ("old-id", "old", old_metadata, [0.0, 1.0])})
    manager = PublicationManager(project_root=tmp_path, runtime=runtime, vector_store=store,
                                 clock=lambda: datetime.now(UTC))
    monkeypatch.setattr("rag.publication.save_pipeline_manifest_atomic",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("injected")))
    with pytest.raises(IndexPublicationError, match="已恢复"):
        manager.replace_document(_manifest(runtime), _built(tmp_path), "2026-09-16T00:00:01Z")
    assert set(store.records) == {"old-id"}
    assert runtime.manifest_path.read_bytes() == b"old-manifest"
    assert not any((tmp_path / ".rag/system-v2/recovery/v1").iterdir())


def test_startup_recovery_rolls_back_process_interrupt_before_manifest(tmp_path: Path, monkeypatch) -> None:
    runtime = _runtime(tmp_path)
    old_metadata = {"document_id": "doc", "document_name": "a.pdf", "relative_path": "documents/a.pdf",
                    "page_number": 1, "chunk_index": 0, "file_hash": "oldhash"}
    store = FakeStore({"old-id": ("old-id", "old", old_metadata, [0.0, 1.0])})
    manager = PublicationManager(project_root=tmp_path, runtime=runtime, vector_store=store,
                                 clock=lambda: datetime.now(UTC))
    monkeypatch.setattr(store, "add_chunks", lambda *_args: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        manager.replace_document(_manifest(runtime), _built(tmp_path), "2026-09-16T00:00:01Z")

    monkeypatch.undo()
    recovered = PublicationManager(project_root=tmp_path, runtime=runtime, vector_store=store,
                                   clock=lambda: datetime.now(UTC)).recover_pending()
    assert recovered is True
    assert set(store.records) == {"old-id"}
    assert not runtime.manifest_path.exists()
    assert not any((tmp_path / ".rag/system-v2/recovery/v1").iterdir())


def test_startup_recovery_finishes_commit_interrupted_during_cleanup(tmp_path: Path, monkeypatch) -> None:
    runtime = _runtime(tmp_path)
    store = FakeStore()
    manager = PublicationManager(project_root=tmp_path, runtime=runtime, vector_store=store,
                                 clock=lambda: datetime.now(UTC))
    from rag import publication
    original_remove = publication._safe_remove_tree
    monkeypatch.setattr(publication, "_safe_remove_tree",
                        lambda *_args: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        manager.replace_document(_manifest(runtime), _built(tmp_path), "2026-09-16T00:00:01Z")

    monkeypatch.setattr(publication, "_safe_remove_tree", original_remove)
    assert manager.recover_pending() is True
    assert set(store.records) == {"new-id"}
    assert not any((tmp_path / ".rag/system-v2/recovery/v1").iterdir())


def test_force_rebuild_failure_restores_whole_collection(tmp_path: Path, monkeypatch) -> None:
    runtime = _runtime(tmp_path)
    old_metadata = {"document_id": "old-doc", "document_name": "old.pdf",
                    "relative_path": "documents/old.pdf", "page_number": 1,
                    "chunk_index": 0, "file_hash": "oldhash"}
    store = FakeStore({"old-id": ("old-id", "old", old_metadata, [0.0, 1.0])})
    manager = PublicationManager(project_root=tmp_path, runtime=runtime, vector_store=store,
                                 clock=lambda: datetime.now(UTC))
    runtime.manifest_path.parent.mkdir(parents=True)
    runtime.manifest_path.write_bytes(b"old-manifest")
    monkeypatch.setattr("rag.publication.save_pipeline_manifest_atomic",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("injected")))
    with pytest.raises(IndexPublicationError):
        manager.force_rebuild(_manifest(runtime), (_built(tmp_path),), "2026-09-16T00:00:01Z")
    assert set(store.records) == {"old-id"}
    assert runtime.manifest_path.read_bytes() == b"old-manifest"


def test_superseded_artifact_cleanup_keeps_current_build(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    manager = PublicationManager(project_root=tmp_path, runtime=runtime, vector_store=FakeStore(),
                                 clock=lambda: datetime.now(UTC))
    document_root = tmp_path / ".rag/system-v2/artifacts/v2/documents/a--doc"
    old_build = document_root / "old-build"
    new_build = document_root / "new-build"
    old_build.mkdir(parents=True)
    new_build.mkdir()
    old_record = SimpleNamespace(
        artifact_path=old_build.relative_to(tmp_path).as_posix()
    )
    new_record = SimpleNamespace(
        artifact_path=new_build.relative_to(tmp_path).as_posix()
    )

    manager._cleanup_superseded_artifacts(
        SimpleNamespace(documents={"doc": old_record}),
        SimpleNamespace(documents={"doc": new_record}),
    )

    assert not old_build.exists()
    assert new_build.is_dir()


def test_prune_removes_only_selected_document(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    store = FakeStore()
    manager = PublicationManager(project_root=tmp_path, runtime=runtime, vector_store=store,
                                 clock=lambda: datetime.now(UTC))
    manifest = manager.replace_document(_manifest(runtime), _built(tmp_path), "2026-09-16T00:00:01Z")
    updated = manager.prune_document(manifest, "doc", "prune-build", "2026-09-16T00:00:02Z")
    assert updated.documents == {}
    assert store.records == {}
    assert not any((tmp_path / ".rag/system-v2/recovery/v1").iterdir())
