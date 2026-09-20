from dataclasses import replace
from pathlib import Path

from rag.config import SelectedPipelineSettings, Settings, select_pipeline
from rag.manifest import (
    global_config_matches,
    load_manifest,
    make_empty_manifest,
    save_manifest_atomic,
    validate_index,
)
from rag.models import DocumentState, ManifestDocument, SourceDocument


def _settings(tmp_path: Path) -> SelectedPipelineSettings:
    return select_pipeline(Settings(
        project_root=tmp_path,
        documents_dir=tmp_path / "documents",
        db_path=tmp_path / ".rag/system-v2/chroma",
        artifacts_path=tmp_path / ".rag/system-v2/artifacts",
        embedding_model="embedding",
        embedding_model_revision="revision",
        v1_chunk_size=50,
        v1_chunk_overlap=10,
        v2_max_input_tokens=512,
        v2_text_overlap_tokens=32,
        top_k=2,
        diagnostics_enabled=False,
        openrouter_api_key=None,
        openrouter_model="llm",
    ), "v1")


def test_manifest_round_trip_multiple_documents(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    manifest = make_empty_manifest(settings)
    documents = {
        "b": ManifestDocument("b.pdf", "b.pdf", "hb", 1, 20, 2, "now"),
        "a": ManifestDocument("a.pdf", "a.pdf", "ha", 2, 30, 3, "now"),
    }
    manifest = replace(manifest, documents=documents, updated_at="now")

    save_manifest_atomic(settings.manifest_path, manifest)

    assert load_manifest(settings.manifest_path) == manifest
    assert not settings.manifest_path.with_name("manifest.json.tmp").exists()


def test_global_config_change_requires_rebuild(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    manifest = make_empty_manifest(settings)

    assert global_config_matches(manifest, settings)
    changed = replace(settings, application=replace(settings.application, v1_chunk_size=51))
    assert not global_config_matches(manifest, changed)
    changed_model = replace(
        settings,
        application=replace(settings.application, embedding_model="other"),
    )
    assert not global_config_matches(
        manifest, changed_model
    )


class _Store:
    def __init__(self, exists: bool = True) -> None:
        self.exists = exists
        self.per_document_queries = 0

    def collection_exists(self) -> bool:
        return self.exists

    def count_all(self) -> int:
        return 0

    def count_document(self, document_id: str) -> int:
        self.per_document_queries += 1
        return 0

    def get_metadatas(self, document_id: str | None = None) -> list[dict[str, str]]:
        if document_id is not None:
            self.per_document_queries += 1
        return []


def _source(tmp_path: Path, name: str = "sales.pdf") -> SourceDocument:
    return SourceDocument("sales", name, name, tmp_path / name, "hash")


def test_config_mismatch_marks_all_documents_unassessed_without_store_queries(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    source = _source(tmp_path)
    manifest = replace(
        make_empty_manifest(settings),
        documents={
            source.document_id: ManifestDocument(
                source.relative_path, source.document_name, source.file_hash, 1, 1, 1, "now"
            ),
            "removed": ManifestDocument("removed.pdf", "removed.pdf", "old", 1, 1, 1, "now"),
        },
    )
    store = _Store()

    changed = replace(settings, application=replace(settings.application, v1_chunk_size=51))
    health = validate_index(changed, [source], manifest, store)  # type: ignore[arg-type]

    assert [status.state for status in health.document_statuses] == [
        DocumentState.UNASSESSED,
        DocumentState.UNASSESSED,
    ]
    assert [issue.code for issue in health.issues] == ["global_config_mismatch"]
    assert store.per_document_queries == 0


def test_missing_collection_marks_documents_unassessed_without_store_queries(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    source = _source(tmp_path)
    manifest = make_empty_manifest(settings)
    store = _Store(exists=False)

    health = validate_index(settings, [source], manifest, store)  # type: ignore[arg-type]

    assert health.document_statuses[0].state is DocumentState.UNASSESSED
    assert [issue.code for issue in health.issues] == ["collection_missing"]
    assert store.per_document_queries == 0
