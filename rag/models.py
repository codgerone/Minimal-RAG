"""Data-only domain models shared across Minimal RAG modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class DocumentState(str, Enum):
    NEW = "new"
    CURRENT = "current"
    CHANGED = "changed"
    MISSING = "missing"
    INVALID = "invalid"


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


@dataclass(frozen=True)
class IndexIssue:
    code: str
    message: str
    remediation: str


@dataclass(frozen=True)
class IndexHealth:
    usable: bool
    document_statuses: tuple[DocumentStatus, ...]
    issues: tuple[IndexIssue, ...]


@dataclass(frozen=True)
class BuiltDocument:
    source: SourceDocument
    pages: tuple[PageText, ...]
    chunks: tuple[TextChunk, ...]
    embeddings: tuple[tuple[float, ...], ...]


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

