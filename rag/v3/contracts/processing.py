"""Parser evidence and processing handoff models."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeAlias

from rag.v3.contracts.documents import ParsedDocument, PrimaryDocument, ProcessingWarning, SourceDocument

if TYPE_CHECKING:
    from rag.v3.contracts.retrieval import EmbeddingIdentity
    from rag.v3.contracts.runtime import OperationError
    from rag.v3.contracts.tables import (
        ContentResolution, GroupingReport, PreparedTableContent, ScoringReport,
        TableExtractionReport,
    )
    from rag.v3.contracts.storage import IndexIdentity


JsonValue: TypeAlias = "None | bool | str | int | float | list[JsonValue] | dict[str, JsonValue]"


@dataclass(frozen=True)
class PyMuPDFNativeBlock:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    block_no: int
    block_type: int

    def __post_init__(self) -> None:
        if not all(math.isfinite(x) for x in (self.x0, self.y0, self.x1, self.y1)):
            raise ValueError("native block has nonfinite coordinates")


@dataclass(frozen=True)
class PyMuPDFNativePage:
    page_number: int
    blocks: tuple[PyMuPDFNativeBlock, ...]


@dataclass(frozen=True)
class PyMuPDFNativePayload:
    pages: tuple[PyMuPDFNativePage, ...]


@dataclass(frozen=True)
class DoclingNativePayload:
    export: dict[str, JsonValue]


@dataclass(frozen=True)
class NativeParserEvidence:
    parser_plugin_id: str
    format_id: Literal["pymupdf_pages_v1", "docling_document_v1"]
    raw_payload: PyMuPDFNativePayload | DoclingNativePayload

    def __post_init__(self) -> None:
        if (self.format_id == "pymupdf_pages_v1") != isinstance(self.raw_payload, PyMuPDFNativePayload):
            raise ValueError("native evidence format and payload mismatch")


@dataclass(frozen=True)
class DocumentRequest:
    source: SourceDocument
    index_identity: IndexIdentity
    build_id: str
    table_branch_attached: bool


@dataclass(frozen=True)
class ProcessingResult:
    source: SourceDocument
    index_identity: IndexIdentity
    build_id: str
    table_branch_attached: bool
    native_parser_evidence: NativeParserEvidence
    primary_document: PrimaryDocument
    extraction_reports: tuple[TableExtractionReport, ...]
    resolutions: tuple[ContentResolution, ...]
    grouping_report: GroupingReport | None
    scoring_report: ScoringReport | None
    prepared_tables: tuple[PreparedTableContent, ...]
    parsed_document: ParsedDocument
    warnings: tuple[ProcessingWarning, ...]


@dataclass(frozen=True)
class ChunkingContext:
    index_identity: IndexIdentity
    build_id: str
    embedding_identity: EmbeddingIdentity
    character_size: int | None
    character_overlap: int | None
    maximum_input_tokens: int | None
    text_overlap_tokens: int | None

    def __post_init__(self) -> None:
        character = self.character_size is not None
        token = self.maximum_input_tokens is not None
        if character == token or not self.build_id:
            raise ValueError("exactly one chunking parameter family required")
        if character:
            if (self.character_overlap is None or self.text_overlap_tokens is not None
                    or self.character_size <= 0
                    or not 0 <= self.character_overlap < self.character_size):
                raise ValueError("invalid character chunking context")
        elif (self.text_overlap_tokens is None or self.character_overlap is not None
              or self.maximum_input_tokens <= 0
              or not 0 <= self.text_overlap_tokens < self.maximum_input_tokens):
            raise ValueError("invalid token chunking context")


@dataclass(frozen=True)
class IndexerInput:
    configuration_name: str
    index_identity: IndexIdentity
    sources: tuple[SourceDocument, ...]
    selected_document_id: str | None
    force: bool
    prune: bool
    prune_authorized: bool

    def __post_init__(self) -> None:
        ids = tuple(item.document_id for item in self.sources)
        if (not self.configuration_name or len(set(ids)) != len(ids)
                or self.selected_document_id is not None and (
                    self.selected_document_id not in ids or self.prune)
                or self.prune and not self.prune_authorized):
            raise ValueError("invalid indexer input")


@dataclass(frozen=True)
class IngestDocumentResult:
    document_id: str
    relative_path: str
    state: Literal["added", "updated", "skipped", "failed", "missing", "pruned", "not_published"]
    error: OperationError | None
    transaction_id: str | None

    def __post_init__(self) -> None:
        if self.state in {"added", "updated", "pruned"}:
            if not self.transaction_id or self.error is not None:
                raise ValueError("committed document result requires transaction")
        elif self.state == "failed":
            if self.error is None:
                raise ValueError("failed document result requires machine error")
        elif self.transaction_id is not None or self.error is not None:
            raise ValueError("nonpublished document has transaction or error")


@dataclass(frozen=True)
class IngestResult:
    configuration_name: str
    index_identity: IndexIdentity
    scanned: int
    documents: tuple[IngestDocumentResult, ...]
    collection_count: int

    def __post_init__(self) -> None:
        if self.scanned < 0 or self.collection_count < 0:
            raise ValueError("invalid ingest result counts")
