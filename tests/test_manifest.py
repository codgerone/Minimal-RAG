from dataclasses import replace
from pathlib import Path

from rag.config import Settings
from rag.manifest import (
    global_config_matches,
    load_manifest,
    make_empty_manifest,
    save_manifest_atomic,
)
from rag.models import ManifestDocument


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        project_root=tmp_path,
        documents_dir=tmp_path / "documents",
        db_path=tmp_path / ".rag" / "chroma",
        manifest_path=tmp_path / ".rag" / "manifest.json",
        collection_name="collection",
        embedding_model="embedding",
        chunk_size=50,
        chunk_overlap=10,
        top_k=2,
        openrouter_api_key=None,
        openrouter_model="llm",
    )


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
    assert not global_config_matches(manifest, replace(settings, chunk_size=51))
    assert not global_config_matches(
        manifest, replace(settings, embedding_model="other")
    )

