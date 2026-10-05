"""Machine-readable command, health and operation outcome models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from rag.v3.contracts.assembly import SavedConfiguration
from rag.v3.contracts.storage import IndexIdentity


CommandName: TypeAlias = Literal["documents", "ingest", "chunks", "browse", "search", "ask", "chat", "eval"]
DocumentState: TypeAlias = Literal["current", "new", "changed", "missing", "invalid", "unprocessable", "unassessed"]
IndexIssueCode: TypeAlias = Literal[
    "pending_recovery", "recovery_failed", "manifest_invalid", "manifest_missing_with_collection",
    "configuration_mismatch", "collection_missing_with_manifest", "collection_manifest_count_mismatch",
    "unknown_vector_document", "source_new", "source_changed", "source_missing",
    "source_unprocessable", "vector_record_invalid", "artifact_invalid",
]
RemediationAction: TypeAlias = Literal["recover", "incremental_ingest", "force_rebuild",
                                       "prune_after_confirmation", "repair_source", "repair_configuration", "none"]
StageCode: TypeAlias = Literal[
    "configuration", "source_discovery", "source_probe", "main_parsing", "table_extraction",
    "table_selection", "assembly", "chunking", "artifact_staging", "embedding",
    "vector_store", "publication", "recovery", "health", "retrieval", "prompt", "llm", "evaluation",
]
OperationErrorCode: TypeAlias = Literal[
    "invalid_argument", "unknown_configuration", "invalid_assembly", "missing_source_directory",
    "unsafe_document_selector", "ambiguous_document_selector", "pdf_unreadable", "pdf_no_content",
    "main_parser_failed", "optional_extractor_failed", "selection_invariant_failed",
    "assembly_invariant_failed", "chunking_failed", "artifact_failed", "embedding_failed",
    "vector_store_failed", "manifest_failed", "manifest_conflict", "publication_failed",
    "recovery_failed", "index_not_ready", "retrieval_failed", "prompt_failed",
    "llm_missing_credentials", "llm_auth_failed", "llm_rate_limited", "llm_timeout",
    "llm_upstream_failed", "evaluation_invalid", "evaluation_failed",
]


@dataclass(frozen=True)
class CommandContext:
    command: CommandName
    configuration_name: str | None
    configuration: SavedConfiguration | None
    index_identity: IndexIdentity | None
    interactive: bool
    selection_mode: Literal["default", "named", "wizard", "all_configs"]
    query_k: int | None
    document_selector: str | None
    debug: bool
    force: bool
    prune: bool
    live: bool


@dataclass(frozen=True)
class IndexIssue:
    code: IndexIssueCode
    scope: Literal["configuration", "document", "artifact", "transaction"]
    stage: StageCode
    document_id: str | None
    blocking: bool
    message: str
    remediation: RemediationAction


@dataclass(frozen=True)
class DocumentStatus:
    document_id: str
    relative_path: str
    document_name: str
    state: DocumentState
    source_file_hash: str | None
    recorded_file_hash: str | None
    page_count: int | None
    chunk_count: int | None
    indexed_at: str | None
    issues: tuple[IndexIssue, ...]


@dataclass(frozen=True)
class HealthReport:
    usable: bool
    index_identity: IndexIdentity
    document_statuses: tuple[DocumentStatus, ...]
    issues: tuple[IndexIssue, ...]
    checked_at: str
    inspection_complete: bool


@dataclass(frozen=True)
class SourceProbeResult:
    document_id: str
    file_hash: str
    status: Literal["processable", "unprocessable"]
    page_count: int | None
    reason: Literal["pdf_unreadable", "pdf_no_content"] | None

    def __post_init__(self) -> None:
        if self.status == "processable":
            if self.page_count is None or self.page_count <= 0 or self.reason is not None:
                raise ValueError("processable source probe fields differ")
        elif self.reason is None:
            raise ValueError("unprocessable source probe lacks reason")


@dataclass(frozen=True)
class OperationError:
    code: OperationErrorCode
    stage: StageCode
    scope: Literal["configuration", "document", "artifact", "transaction", "request"]
    document_id: str | None
    retryable: bool
    message: str
    diagnostic_ref: str | None


@dataclass(frozen=True)
class RecoveryResult:
    transaction_id: str
    index_identity: IndexIdentity
    outcome: Literal["committed", "rolled_back", "failed"]
    verified_manifest: bool
    verified_vectors: bool
    verified_artifacts: bool
    remaining_issues: tuple[IndexIssueCode, ...]

    def __post_init__(self) -> None:
        if self.outcome == "failed":
            if not self.remaining_issues:
                raise ValueError("failed recovery must retain blocking issue")
        elif (self.remaining_issues or not all((self.verified_manifest,
                                                self.verified_vectors,
                                                self.verified_artifacts))):
            raise ValueError("finished recovery must verify every store")
