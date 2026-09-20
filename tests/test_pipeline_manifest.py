from dataclasses import replace
from pathlib import Path
import json

import pytest

from rag.build_config import (
    EmbeddingConfig, TokenizerConfig, V1BuildConfig, V1ChunkerConfig,
    V1DependencyVersions, V1ParserConfig,
)
from rag.errors import ManifestError
from rag.pipeline_manifest import (
    ManifestDocumentRecord, PipelineManifest, load_pipeline_manifest,
    save_pipeline_manifest_atomic,
)
from rag.pipeline_registry import build_config_fingerprint


def _config() -> V1BuildConfig:
    return V1BuildConfig(V1ParserConfig(), V1ChunkerConfig(700, 100),
                         TokenizerConfig("model", "revision"),
                         EmbeddingConfig("model", "revision", 3),
                         V1DependencyVersions("1", "1", "1", "1", "1"))


def test_pipeline_manifest_strict_round_trip_and_sorted_documents(tmp_path: Path) -> None:
    config = _config()
    fingerprint = build_config_fingerprint(config)
    records = {
        key: ManifestDocumentRecord(key, f"{key}.pdf", f"documents/{key}.pdf", "hash", "build",
                                    fingerprint, 1, 2, 1, None, "2026-09-16T00:00:00Z")
        for key in ("b", "a")
    }
    manifest = PipelineManifest("v1", "minimal_rag_documents_v1", config, fingerprint,
                                records, "2026-09-16T00:00:00Z", "2026-09-16T00:00:00Z")
    path = tmp_path / "manifest.json"
    save_pipeline_manifest_atomic(path, manifest, "build")
    assert list(json.loads(path.read_text(encoding="utf-8"))["documents"]) == ["a", "b"]
    assert load_pipeline_manifest(path) == manifest


def test_pipeline_manifest_rejects_extra_fields_and_wrong_fingerprint(tmp_path: Path) -> None:
    config = _config()
    with pytest.raises(ManifestError, match="fingerprint"):
        PipelineManifest("v1", "minimal_rag_documents_v1", config, "bad", {}, "x", "x")

    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"schema_version": "pipeline_manifest_v2", "extra": 1}), encoding="utf-8")
    with pytest.raises(ManifestError, match="字段不匹配"):
        load_pipeline_manifest(path)
