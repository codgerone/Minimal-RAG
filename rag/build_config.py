"""Serializable build configuration contracts for the v1 and v2 pipelines."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from rag.errors import ConfigurationError
from rag.pipeline_types import PipelineId


TableStrategy: TypeAlias = Literal[
    "lines", "lines_strict", "text", "lattice", "stream", "network",
    "hybrid", "accurate", "hi_res",
]
ToolName: TypeAlias = Literal["pymupdf", "camelot", "docling", "unstructured"]


@dataclass(frozen=True)
class V1DependencyVersions:
    pymupdf: str
    sentence_transformers: str
    transformers: str
    chromadb: str
    langchain_text_splitters: str


@dataclass(frozen=True)
class V2DependencyVersions:
    pymupdf: str
    camelot: str
    docling: str
    docling_core: str
    unstructured: str
    sentence_transformers: str
    transformers: str
    chromadb: str


@dataclass(frozen=True)
class TokenizerConfig:
    model_name: str
    revision: str
    passage_prefix: Literal["passage: "] = "passage: "
    query_prefix: Literal["query: "] = "query: "
    add_special_tokens: Literal[True] = True
    truncation: Literal[False] = False


@dataclass(frozen=True)
class EmbeddingConfig:
    model_name: str
    revision: str
    vector_dimension: int
    normalize_embeddings: Literal[True] = True


@dataclass(frozen=True)
class V1ParserConfig:
    rule_version: Literal["pymupdf_text_v1"] = "pymupdf_text_v1"


@dataclass(frozen=True)
class V1ChunkerConfig:
    chunk_size_characters: int
    chunk_overlap_characters: int
    rule_version: Literal["recursive_character_v1"] = "recursive_character_v1"
    separator_version: Literal["legacy_default_v1"] = "legacy_default_v1"


@dataclass(frozen=True)
class V1BuildConfig:
    parser: V1ParserConfig
    chunker: V1ChunkerConfig
    tokenizer: TokenizerConfig
    embedding: EmbeddingConfig
    dependency_versions: V1DependencyVersions
    schema_version: Literal["build_config_v1"] = "build_config_v1"
    pipeline_id: Literal["v1"] = "v1"


@dataclass(frozen=True)
class V2ParserConfig:
    rule_version: Literal["docling_layout_v1"] = "docling_layout_v1"
    docling_version: Literal["2.121.0"] = "2.121.0"
    do_ocr: Literal[False] = False
    do_table_structure: Literal[True] = True
    table_mode: Literal["accurate"] = "accurate"
    do_cell_matching: Literal[True] = True
    generate_page_images: Literal[False] = False
    mapper_version: Literal["docling_mapper_v2"] = "docling_mapper_v2"


@dataclass(frozen=True)
class TableSelectionConfig:
    strategies: tuple[TableStrategy, ...] = (
        "lines", "lines_strict", "text", "lattice", "stream", "network",
        "hybrid", "accurate", "hi_res",
    )
    metric_weights: tuple[float, float, float, float] = (0.25, 0.25, 0.25, 0.25)
    tool_tiebreak_order: tuple[ToolName, ToolName, ToolName, ToolName] = (
        "pymupdf", "camelot", "docling", "unstructured",
    )
    rule_version: Literal["table_selection_v1"] = "table_selection_v1"
    page_bounds_tolerance_pt: Literal[0.000001] = 0.000001
    page_size_tolerance_pt: Literal[1.0] = 1.0
    boundary_cluster_tolerance_pt: Literal[2.0] = 2.0
    comparison_epsilon: Literal[0.000000000001] = 0.000000000001
    candidate_coverage_minimum: Literal[0.65] = 0.65
    slot_coverage_minimum: Literal[0.77] = 0.77


@dataclass(frozen=True)
class TableHeaderConfig:
    rule_version: Literal["table_header_v1"] = "table_header_v1"
    sample_row_budget: Literal[8] = 8
    minimum_independent_observations: Literal[2] = 2


@dataclass(frozen=True)
class TableSerializationConfig:
    rule_version: Literal["table_text_v1"] = "table_text_v1"


@dataclass(frozen=True)
class DocumentTextConfig:
    rule_version: Literal["document_text_v1"] = "document_text_v1"


@dataclass(frozen=True)
class V2ChunkerConfig:
    maximum_input_tokens: int
    text_overlap_tokens: int
    rule_version: Literal["structured_chunk_v1"] = "structured_chunk_v1"
    separator_version: Literal["recursive_boundaries_v1"] = "recursive_boundaries_v1"


@dataclass(frozen=True)
class V2BuildConfig:
    parser: V2ParserConfig
    table_selection: TableSelectionConfig
    table_header: TableHeaderConfig
    table_serialization: TableSerializationConfig
    document_text: DocumentTextConfig
    chunker: V2ChunkerConfig
    tokenizer: TokenizerConfig
    embedding: EmbeddingConfig
    dependency_versions: V2DependencyVersions
    schema_version: Literal["build_config_v1"] = "build_config_v1"
    pipeline_id: Literal["v2"] = "v2"


BuildConfig: TypeAlias = V1BuildConfig | V2BuildConfig


def validate_build_config(config: BuildConfig) -> None:
    if config.pipeline_id not in ("v1", "v2"):
        raise ConfigurationError("BuildConfig pipeline_id 无效。")
    if not config.tokenizer.model_name.strip() or not config.tokenizer.revision.strip():
        raise ConfigurationError("Tokenizer 的模型名和 revision 不能为空。")
    if not config.embedding.model_name.strip() or not config.embedding.revision.strip():
        raise ConfigurationError("Embedding 的模型名和 revision 不能为空。")
    if (
        config.tokenizer.model_name != config.embedding.model_name
        or config.tokenizer.revision != config.embedding.revision
    ):
        raise ConfigurationError("Tokenizer 与 Embedding 的模型身份不一致。")
    if config.embedding.vector_dimension <= 0:
        raise ConfigurationError("Embedding vector_dimension 必须大于 0。")
    if isinstance(config, V1BuildConfig):
        size = config.chunker.chunk_size_characters
        overlap = config.chunker.chunk_overlap_characters
    else:
        size = config.chunker.maximum_input_tokens
        overlap = config.chunker.text_overlap_tokens
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ConfigurationError("BuildConfig 的分块上限和 overlap 无效。")
