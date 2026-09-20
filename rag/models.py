"""Data-only domain models shared across Minimal RAG modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal, TypeAlias

from rag.pipeline_types import PipelineId


class DocumentState(str, Enum):
    NEW = "new"
    CURRENT = "current"
    CHANGED = "changed"
    MISSING = "missing"
    INVALID = "invalid"
    UNPROCESSABLE = "unprocessable"
    UNASSESSED = "unassessed"


@dataclass(frozen=True)
class SourceDocument:
    document_id: str
    document_name: str
    relative_path: str
    absolute_path: Path
    file_hash: str


@dataclass(frozen=True)
class PageText:
    document_id: str
    document_name: str
    relative_path: str
    page_number: int
    text: str


@dataclass(frozen=True)
class TextChunk:
    chunk_id: str
    document_id: str
    document_name: str
    relative_path: str
    page_number: int
    chunk_index: int
    text: str
    file_hash: str


@dataclass(frozen=True)
class ChunkSource:
    node_id: str
    page_spans: tuple[Any, ...]
    source_text_start: int | None
    source_text_end: int | None
    repeated_context: bool
    context_kind: Literal["none", "overlap", "table_header", "merged_cell", "list_ancestor", "fallback_locator"]

    def __post_init__(self) -> None:
        if (self.source_text_start is None) != (self.source_text_end is None):
            raise ValueError("ChunkSource 文本范围必须同时存在或同时为空。")
        if self.source_text_start is not None and not 0 <= self.source_text_start <= self.source_text_end:
            raise ValueError("ChunkSource 文本范围无效。")
        if self.repeated_context != (self.context_kind != "none"):
            raise ValueError("ChunkSource repeated_context 与 context_kind 不一致。")


@dataclass(frozen=True)
class V2DocumentChunk:
    chunk_id: str
    document_id: str
    pipeline_id: Literal["v2"]
    chunk_index: int
    kind: Literal["text", "list", "table"]
    text: str
    token_count: int
    sources: tuple[ChunkSource, ...]
    parent_unit_id: str | None
    fragment_index: int
    fragment_count: int

    def __post_init__(self) -> None:
        if self.pipeline_id != "v2" or self.chunk_index < 0 or self.token_count <= 0 or not self.text:
            raise ValueError("V2DocumentChunk 基本字段无效。")
        if self.fragment_count <= 0 or not 0 <= self.fragment_index < self.fragment_count:
            raise ValueError("V2DocumentChunk fragment 范围无效。")
        split = self.fragment_count > 1
        if split != (self.parent_unit_id is not None):
            raise ValueError("仅拆分 chunk 必须具有 parent_unit_id。")


DocumentChunk: TypeAlias = TextChunk | V2DocumentChunk


@dataclass(frozen=True)
class RetrievalHit:
    chunk_id: str
    document_id: str
    document_name: str
    relative_path: str
    page_number: int
    chunk_index: int
    text: str
    distance: float

    @property
    def similarity(self) -> float:
        return 1.0 - self.distance


@dataclass(frozen=True)
class V2RetrievalHit:
    chunk_id: str
    document_id: str
    document_name: str
    relative_path: str
    chunk_index: int
    chunk_kind: Literal["text", "list", "table"]
    text: str
    distance: float
    page_numbers: tuple[int, ...]
    sources: tuple[ChunkSource, ...]

    @property
    def similarity(self) -> float:
        return 1.0 - self.distance


@dataclass(frozen=True)
class ManifestDocument:
    relative_path: str
    document_name: str
    file_hash: str
    page_count: int
    character_count: int
    chunk_count: int
    indexed_at: str


@dataclass(frozen=True)
class Manifest:
    schema_version: int
    collection_name: str
    embedding_model: str
    chunking: dict[str, Any]
    documents: dict[str, ManifestDocument] = field(default_factory=dict)
    updated_at: str = ""


@dataclass(frozen=True)
class DocumentStatus:
    state: DocumentState
    document_id: str
    relative_path: str
    document_name: str
    file_hash: str | None = None
    page_count: int | None = None
    chunk_count: int | None = None
    indexed_at: str | None = None
    detail: str | None = None
    recorded_file_hash: str | None = None
    issues: tuple[IndexIssue, ...] = ()


@dataclass(frozen=True)
class IndexIssue:
    code: str
    message: str
    remediation: str
    scope: Literal["pipeline", "document", "artifact"] = "pipeline"
    document_id: str | None = None
    blocking: bool = True

    @property
    def remediation_command(self) -> str:
        return self.remediation


@dataclass(frozen=True)
class IndexHealth:
    usable: bool
    document_statuses: tuple[DocumentStatus, ...]
    issues: tuple[IndexIssue, ...]


@dataclass(frozen=True)
class BuiltDocument:
    result: DocumentBuildResult
    embeddings: tuple[tuple[float, ...], ...]


@dataclass(frozen=True)
class DocumentBuildStats:
    page_count: int
    character_count: int
    chunk_count: int

    def __post_init__(self) -> None:
        if min(self.page_count, self.character_count, self.chunk_count) < 0:
            raise ValueError("DocumentBuildStats 计数不能为负数。")


@dataclass(frozen=True)
class ArtifactStageResult:
    staging_path: Path
    raw_docling_path: Path
    parsed_document_path: Path
    chunks_path: Path
    selection_summary_path: Path
    winner_review_path: Path
    diagnostics_path: Path | None
    winner_count: int
    chunk_count: int

    def __post_init__(self) -> None:
        if self.winner_count < 0 or self.chunk_count < 0:
            raise ValueError("ArtifactStageResult 计数不能为负数。")
        root = self.staging_path.resolve()
        required = (
            self.raw_docling_path,
            self.parsed_document_path,
            self.chunks_path,
            self.selection_summary_path,
            self.winner_review_path,
        )
        optional = (() if self.diagnostics_path is None else (self.diagnostics_path,))
        if any(not path.resolve().is_relative_to(root) for path in required + optional):
            raise ValueError("ArtifactStageResult 路径必须位于 staging_path 内。")
        if any(not path.is_file() for path in required):
            raise ValueError("ArtifactStageResult 必需产物必须已经存在。")
        if self.diagnostics_path is not None and not self.diagnostics_path.is_dir():
            raise ValueError("ArtifactStageResult diagnostics_path 必须是已存在目录。")


@dataclass(frozen=True)
class DocumentBuildResult:
    source: SourceDocument
    pipeline_id: PipelineId
    build_id: str
    chunks: tuple[DocumentChunk, ...]
    stats: DocumentBuildStats
    artifact_stage: ArtifactStageResult | None

    def __post_init__(self) -> None:
        if self.pipeline_id not in {"v1", "v2"}:
            raise ValueError("DocumentBuildResult pipeline_id 无效。")
        if not self.build_id.strip():
            raise ValueError("DocumentBuildResult build_id 不能为空。")
        if self.stats.chunk_count != len(self.chunks):
            raise ValueError("DocumentBuildResult chunk_count 与 chunks 不一致。")
        if self.pipeline_id == "v1" and self.artifact_stage is not None:
            raise ValueError("V1 DocumentBuildResult 不得包含 artifact_stage。")
        if self.pipeline_id == "v2" and self.artifact_stage is None:
            raise ValueError("V2 DocumentBuildResult 必须包含 artifact_stage。")
        if self.artifact_stage and self.artifact_stage.chunk_count != len(self.chunks):
            raise ValueError("ArtifactStageResult chunk_count 与 chunks 不一致。")
        for chunk in self.chunks:
            if chunk.document_id != self.source.document_id:
                raise ValueError("DocumentBuildResult chunk 与 source 身份不一致。")
            if isinstance(chunk, TextChunk) and (
                chunk.document_name != self.source.document_name
                or chunk.relative_path != self.source.relative_path
                or chunk.file_hash != self.source.file_hash
            ):
                raise ValueError("V1 chunk 与 source 身份不一致。")


@dataclass(frozen=True)
class IngestDocumentResult:
    relative_path: str
    state: str
    detail: str | None = None


@dataclass(frozen=True)
class IngestSummary:
    scanned: int
    added: int
    updated: int
    skipped: int
    failed: int
    missing: int
    pruned: int
    collection_count: int
    results: tuple[IngestDocumentResult, ...] = ()


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    question_zh: str
    question_en: str
    expected_documents: tuple[str, ...]
    expected_pages: tuple[int, ...]
    expected_terms: tuple[str, ...]
    answerable: bool
    expected_refusal_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvaluationResult:
    case_id: str
    question: str
    passed: bool | None
    detail: str
