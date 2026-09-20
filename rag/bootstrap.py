"""Lazy construction of a fully consistent selected pipeline runtime."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from rag.build_config import (
    DocumentTextConfig, EmbeddingConfig, TableHeaderConfig, TableSelectionConfig,
    TableSerializationConfig, TokenizerConfig, V1BuildConfig, V1ChunkerConfig,
    V1DependencyVersions, V1ParserConfig, V2BuildConfig, V2ChunkerConfig,
    V2DependencyVersions, V2ParserConfig,
)
from rag.config import SelectedPipelineSettings
from rag.document_processor import V1DocumentProcessor, V2DocumentProcessor
from rag.pipeline_registry import PipelineRegistry, build_config_fingerprint


def _version(distribution: str) -> str:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return "not-installed"


def build_config(settings: SelectedPipelineSettings, *, vector_dimension: int = 384):
    tokenizer = TokenizerConfig(settings.embedding_model, settings.embedding_model_revision)
    embedding = EmbeddingConfig(settings.embedding_model, settings.embedding_model_revision, vector_dimension)
    if settings.identity.pipeline_id == "v1":
        return V1BuildConfig(
            V1ParserConfig(), V1ChunkerConfig(settings.v1_chunk_size, settings.v1_chunk_overlap),
            tokenizer, embedding,
            V1DependencyVersions(
                _version("PyMuPDF"), _version("sentence-transformers"), _version("transformers"),
                _version("chromadb"), _version("langchain-text-splitters"),
            ),
        )
    return V2BuildConfig(
        V2ParserConfig(), TableSelectionConfig(), TableHeaderConfig(), TableSerializationConfig(),
        DocumentTextConfig(), V2ChunkerConfig(settings.v2_max_input_tokens, settings.v2_text_overlap_tokens),
        tokenizer, embedding,
        V2DependencyVersions(
            _version("PyMuPDF"), _version("camelot-py"), _version("docling"),
            _version("docling-core"), _version("unstructured"),
            _version("sentence-transformers"), _version("transformers"), _version("chromadb"),
        ),
    )


def create_runtime(settings: SelectedPipelineSettings, *, vector_dimension: int = 384):
    config = build_config(settings, vector_dimension=vector_dimension)
    fingerprint = build_config_fingerprint(config)
    if config.pipeline_id == "v1":
        processor = V1DocumentProcessor(settings.v1_chunk_size, settings.v1_chunk_overlap)
    else:
        processor = V2DocumentProcessor(
            build_config=config, build_config_fingerprint=fingerprint,
            artifacts_root=settings.artifacts_path,
            diagnostics_enabled=settings.diagnostics_enabled,
        )
    return PipelineRegistry(settings.project_root).create_runtime(config.pipeline_id, config, processor)
