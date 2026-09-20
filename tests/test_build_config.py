from dataclasses import replace

import pytest

from rag.build_config import (
    DocumentTextConfig,
    EmbeddingConfig,
    TableHeaderConfig,
    TableSelectionConfig,
    TableSerializationConfig,
    TokenizerConfig,
    V1BuildConfig,
    V1ChunkerConfig,
    V1DependencyVersions,
    V1ParserConfig,
    V2BuildConfig,
    V2ChunkerConfig,
    V2DependencyVersions,
    V2ParserConfig,
    validate_build_config,
)
from rag.errors import ConfigurationError
from rag.pipeline_registry import build_config_fingerprint


def _tokenizer() -> TokenizerConfig:
    return TokenizerConfig("model", "revision")


def _embedding() -> EmbeddingConfig:
    return EmbeddingConfig("model", "revision", 384)


def _v1() -> V1BuildConfig:
    return V1BuildConfig(
        V1ParserConfig(),
        V1ChunkerConfig(700, 100),
        _tokenizer(),
        _embedding(),
        V1DependencyVersions("1", "2", "3", "4", "5"),
    )


def _v2() -> V2BuildConfig:
    return V2BuildConfig(
        V2ParserConfig(),
        TableSelectionConfig(),
        TableHeaderConfig(),
        TableSerializationConfig(),
        DocumentTextConfig(),
        V2ChunkerConfig(512, 32),
        _tokenizer(),
        _embedding(),
        V2DependencyVersions("1", "2", "3", "4", "5", "6", "7", "8"),
    )


def test_build_configs_have_closed_pipeline_identities_and_validate() -> None:
    v1 = _v1()
    v2 = _v2()

    validate_build_config(v1)
    validate_build_config(v2)
    assert v1.pipeline_id == "v1"
    assert v2.pipeline_id == "v2"
    assert v2.table_selection.strategies == (
        "lines", "lines_strict", "text", "lattice", "stream", "network",
        "hybrid", "accurate", "hi_res",
    )


def test_fingerprint_changes_only_when_build_config_changes() -> None:
    config = _v1()
    changed = replace(
        config,
        chunker=replace(config.chunker, chunk_size_characters=701),
    )

    assert build_config_fingerprint(config) == build_config_fingerprint(_v1())
    assert build_config_fingerprint(config) != build_config_fingerprint(changed)


@pytest.mark.parametrize(
    "config",
    [
        replace(_v1(), tokenizer=TokenizerConfig("other", "revision")),
        replace(_v1(), embedding=EmbeddingConfig("model", "other", 384)),
        replace(_v1(), embedding=EmbeddingConfig("model", "revision", 0)),
        replace(_v1(), chunker=V1ChunkerConfig(100, 100)),
        replace(_v2(), chunker=V2ChunkerConfig(32, 32)),
    ],
)
def test_invalid_build_config_is_rejected(config) -> None:
    with pytest.raises(ConfigurationError):
        validate_build_config(config)

