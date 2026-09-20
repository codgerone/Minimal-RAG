from dataclasses import replace
from pathlib import Path

import pytest

from rag.build_config import (
    EmbeddingConfig, TokenizerConfig, V1BuildConfig, V1ChunkerConfig,
    V1DependencyVersions, V1ParserConfig, V2BuildConfig, V2ChunkerConfig,
    V2DependencyVersions, V2ParserConfig, TableSelectionConfig,
    TableHeaderConfig, TableSerializationConfig, DocumentTextConfig,
)
from rag.errors import ConfigurationError
from rag.pipeline_registry import PipelineRegistry, build_config_fingerprint


def _config(pipeline_id="v1"):
    common = (TokenizerConfig("model", "revision"), EmbeddingConfig("model", "revision", 384))
    if pipeline_id == "v1":
        return V1BuildConfig(V1ParserConfig(), V1ChunkerConfig(700, 100), *common, V1DependencyVersions("1", "2", "3", "4", "5"))
    return V2BuildConfig(V2ParserConfig(), TableSelectionConfig(), TableHeaderConfig(), TableSerializationConfig(), DocumentTextConfig(), V2ChunkerConfig(512, 32), *common, V2DependencyVersions("1", "2", "3", "4", "5", "6", "7", "8"))


class _Processor:
    def process(self, source, build_id: str):
        return source, build_id


def test_registry_returns_fixed_isolated_pipeline_identities(tmp_path: Path) -> None:
    registry = PipelineRegistry(tmp_path)

    v1 = registry.get("v1")
    v2 = registry.get("v2")

    assert v1.pipeline_id == "v1"
    assert v1.collection_name == "minimal_rag_documents_v1"
    assert v1.manifest_path == tmp_path / ".rag/system-v2/pipelines/v1/manifest.json"
    assert v2.pipeline_id == "v2"
    assert v2.collection_name == "minimal_rag_documents_v2"
    assert v2.manifest_path == tmp_path / ".rag/system-v2/pipelines/v2/manifest.json"
    assert v1.collection_name != v2.collection_name
    assert v1.manifest_path != v2.manifest_path


@pytest.mark.parametrize("pipeline_id", ["", "V1", "v3", "legacy"])
def test_registry_rejects_unknown_pipeline_before_resource_construction(
    tmp_path: Path, pipeline_id: str
) -> None:
    with pytest.raises(ConfigurationError, match="不支持的 pipeline"):
        PipelineRegistry(tmp_path).get(pipeline_id)


def test_registry_creates_validated_runtime_and_canonical_fingerprint(
    tmp_path: Path,
) -> None:
    config = _config("v1")

    runtime = PipelineRegistry(tmp_path).create_runtime(
        "v1", config, _Processor()
    )

    assert runtime.pipeline_id == "v1"
    assert runtime.build_config is config
    assert runtime.build_config_fingerprint == build_config_fingerprint(config)
    assert len(runtime.build_config_fingerprint) == 64


def test_registry_rejects_runtime_identity_mismatches(tmp_path: Path) -> None:
    registry = PipelineRegistry(tmp_path)
    runtime = registry.create_runtime("v1", _config("v1"), _Processor())

    mismatches = (
        replace(runtime, collection_name="minimal_rag_documents_v2"),
        replace(runtime, manifest_path=tmp_path / ".rag/system-v2/pipelines/v2/manifest.json"),
        replace(runtime, build_config=_config("v2")),
        replace(runtime, build_config_fingerprint="0" * 64),
    )
    for mismatch in mismatches:
        with pytest.raises(ConfigurationError):
            registry.validate_runtime(mismatch)


def test_registry_rejects_pipeline_and_build_config_mismatch(
    tmp_path: Path,
) -> None:
    with pytest.raises(ConfigurationError, match="BuildConfig"):
        PipelineRegistry(tmp_path).create_runtime(
            "v1", _config("v2"), _Processor()
        )
