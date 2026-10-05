"""V3 index identity shared by build, query and publication contracts."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Literal

from rag.v3.contracts.documents import ChunkKind, ChunkSource

if TYPE_CHECKING:
    from rag.v3.contracts.assembly import BuildProjection
    from rag.v3.contracts.artifacts import StagedArtifacts
    from rag.v3.contracts.documents import ProcessingWarning
    from rag.v3.contracts.retrieval import EmbeddingIdentity
    from rag.v3.contracts.runtime import RecoveryResult


@dataclass(frozen=True)
class IndexIdentity:
    build_fingerprint: str
    storage_schema_version: Literal["index_store_v3"]
    collection_name: str
    namespace_path: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", self.build_fingerprint):
            raise ValueError("index fingerprint must be full SHA-256")
        if self.storage_schema_version != "index_store_v3":
            raise ValueError("unsupported V3 storage schema")
        namespace = PurePosixPath(self.namespace_path)
        if (not self.collection_name or not self.namespace_path or namespace.is_absolute() or
                any(part in {".", "..", ""} for part in self.namespace_path.split("/"))):
            raise ValueError("index namespace is unsafe")
        if (self.collection_name != f"rag_v3_{self.build_fingerprint[:32]}" or
                self.namespace_path != f".rag/system-v3/indexes/{self.build_fingerprint}"):
            raise ValueError("index physical identity differs from fingerprint")


@dataclass(frozen=True)
class ChunkMetadata:
    schema_version: Literal["chunk_metadata_v3"]
    document_id: str
    document_name: str
    relative_path: str
    build_id: str
    file_hash: str
    build_fingerprint: str
    text_sha256: str
    chunk_index: int
    kind: ChunkKind
    token_count: int | None
    page_numbers: tuple[int, ...]
    sources: tuple[ChunkSource, ...]
    parent_unit_id: str | None
    fragment_index: int
    fragment_count: int

    def __post_init__(self) -> None:
        pages = tuple(dict.fromkeys(span.page_number for source in self.sources
                                    for span in source.page_spans))
        if self.schema_version != "chunk_metadata_v3" or pages != self.page_numbers:
            raise ValueError("chunk metadata schema or source pages mismatch")
        for value in (self.file_hash, self.build_fingerprint, self.text_sha256):
            if not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError("metadata SHA-256 field invalid")


@dataclass(frozen=True)
class VectorRecord:
    chunk_id: str
    document_id: str
    build_id: str
    file_hash: str
    text: str
    metadata: ChunkMetadata
    embedding: tuple[float, ...]

    def __post_init__(self) -> None:
        import hashlib
        import math
        if (self.document_id != self.metadata.document_id or
                self.build_id != self.metadata.build_id or
                self.file_hash != self.metadata.file_hash or
                hashlib.sha256(self.text.encode("utf-8")).hexdigest() != self.metadata.text_sha256 or
                not all(math.isfinite(value) for value in self.embedding)):
            raise ValueError("vector record and metadata disagree")


@dataclass(frozen=True)
class BrowseRecord:
    chunk_id: str
    text: str
    metadata: ChunkMetadata

    def __post_init__(self) -> None:
        import hashlib
        if (not self.chunk_id or not self.text.strip()
                or hashlib.sha256(self.text.encode("utf-8")).hexdigest()
                != self.metadata.text_sha256):
            raise ValueError("browse record content differs from metadata")


@dataclass(frozen=True)
class VectorQueryHit:
    record: VectorRecord
    distance: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.distance):
            raise ValueError("query hit distance must be finite")


@dataclass(frozen=True)
class ManifestDocumentRecord:
    document_id: str
    document_name: str
    relative_path: str
    file_hash: str
    build_id: str
    page_count: int
    character_count: int
    chunk_count: int
    artifact_path: str
    artifact_files: dict[str, str]
    indexed_at: str

    def __post_init__(self) -> None:
        if (not self.document_id or not self.build_id or self.page_count <= 0
                or self.character_count < 0 or self.chunk_count <= 0
                or not self.artifact_path or not self.artifact_files):
            raise ValueError("manifest document record incomplete")
        if not re.fullmatch(r"[0-9a-f]{64}", self.file_hash):
            raise ValueError("manifest file hash invalid")
        if any(not re.fullmatch(r"[0-9a-f]{64}", value)
               for value in self.artifact_files.values()):
            raise ValueError("manifest artifact hash invalid")


@dataclass(frozen=True)
class Manifest:
    schema_version: Literal["index_manifest_v3"]
    index_identity: IndexIdentity
    build_configuration: BuildProjection
    embedding_identity: EmbeddingIdentity
    documents: dict[str, ManifestDocumentRecord]
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        if self.schema_version != "index_manifest_v3":
            raise ValueError("unsupported manifest schema")
        if set(self.documents) != {record.document_id for record in self.documents.values()}:
            raise ValueError("manifest document map identity mismatch")


@dataclass(frozen=True)
class VectorSnapshot:
    schema_version: Literal["vector_snapshot_v3"]
    index_identity: IndexIdentity
    collection_existed: bool
    document_scope: tuple[str, ...]
    ids: tuple[str, ...]
    documents: tuple[str, ...]
    embeddings: tuple[tuple[float, ...], ...]
    metadatas: tuple[ChunkMetadata, ...]
    sha256: str

    def __post_init__(self) -> None:
        sizes = (len(self.ids), len(self.documents), len(self.embeddings), len(self.metadatas))
        if (self.schema_version != "vector_snapshot_v3" or len(set(sizes)) != 1
                or len(set(self.ids)) != len(self.ids)
                or (not self.collection_existed and self.ids)
                or not re.fullmatch(r"[0-9a-f]{64}", self.sha256)):
            raise ValueError("invalid vector snapshot")
        if any(not all(math.isfinite(value) for value in vector)
               for vector in self.embeddings):
            raise ValueError("snapshot contains nonfinite vector")


@dataclass(frozen=True)
class ManifestSnapshot:
    existed: bool
    bytes_sha256: str | None
    snapshot_path: str | None

    def __post_init__(self) -> None:
        if self.existed != (self.bytes_sha256 is not None and self.snapshot_path is not None):
            raise ValueError("manifest snapshot existence mismatch")


@dataclass(frozen=True)
class ArtifactSnapshot:
    schema_version: Literal["artifact_snapshot_v3"]
    transaction_id: str
    document_id: str
    build_id: str
    existed: bool
    active_path: str | None
    snapshot_path: str | None
    files: dict[str, str]
    sha256: str

    def __post_init__(self) -> None:
        if (self.schema_version != "artifact_snapshot_v3" or not self.transaction_id
                or not re.fullmatch(r"[0-9a-f]{64}", self.sha256)):
            raise ValueError("invalid artifact snapshot identity")
        if self.existed:
            if (not self.document_id or not self.build_id or not self.active_path
                    or not self.snapshot_path):
                raise ValueError("existing artifact snapshot lacks recovery evidence")
        elif self.snapshot_path is not None or self.files or (
                self.active_path is not None and (not self.document_id or not self.build_id)):
            raise ValueError("absent artifact snapshot has invalid target")


PublicationOperation = Literal["replace_document", "replace_collection", "prune_document"]
PublicationStep = Literal[
    "none", "vector_replace", "vector_verify", "artifact_publish", "artifact_verify",
    "manifest_publish", "vector_restore", "manifest_restore", "artifact_restore",
    "artifact_cleanup",
]
RecoveryFailureCode = Literal[
    "snapshot_unreadable", "journal_unreadable", "manifest_conflict",
    "vector_restore_failed", "artifact_restore_failed", "manifest_restore_failed",
    "verification_failed", "unsafe_path", "lock_unavailable",
]


@dataclass(frozen=True)
class RecoveryJournal:
    schema_version: Literal["index_recovery_v3"]
    transaction_id: str
    index_identity: IndexIdentity
    operation: PublicationOperation
    document_id: str | None
    state: Literal["prepared", "mutating", "restoring", "recovery_failed"]
    current_step: PublicationStep
    old_manifest_sha256: str | None
    old_manifest_snapshot_path: str
    old_vector_snapshot_path: str
    artifact_quarantine_path: str | None
    new_staging_path: str | None
    expected_new_manifest_sha256: str
    created_at: str
    error_code: RecoveryFailureCode | None
    error_message: str | None

    def __post_init__(self) -> None:
        if (self.schema_version != "index_recovery_v3" or not self.transaction_id
                or not re.fullmatch(r"[0-9a-f]{64}", self.expected_new_manifest_sha256)):
            raise ValueError("invalid recovery journal identity")
        if (self.operation == "replace_collection") != (self.document_id is None):
            raise ValueError("recovery journal scope differs from operation")
        if self.state == "prepared" and self.current_step != "none":
            raise ValueError("prepared journal must have no attempted step")
        if self.state == "recovery_failed":
            if self.error_code is None or not self.error_message:
                raise ValueError("failed recovery journal needs error evidence")
        elif self.error_code is not None or self.error_message is not None:
            raise ValueError("nonfailed recovery journal has error evidence")
        if self.operation != "prune_document" and self.artifact_quarantine_path is not None:
            raise ValueError("only prune may quarantine old artifacts")
        if self.old_manifest_sha256 is not None and not re.fullmatch(
                r"[0-9a-f]{64}", self.old_manifest_sha256):
            raise ValueError("invalid old manifest digest")


@dataclass(frozen=True)
class PublicationRequest:
    transaction_id: str
    operation: PublicationOperation
    index_identity: IndexIdentity
    document_id: str | None
    records: tuple[VectorRecord, ...]
    staged_artifacts: tuple[StagedArtifacts, ...]
    new_manifest: Manifest
    expected_old_manifest_sha256: str | None


@dataclass(frozen=True)
class PublicationResult:
    transaction_id: str
    operation: PublicationOperation
    index_identity: IndexIdentity
    document_id: str | None
    outcome: Literal["committed", "rolled_back", "failed"]
    commit_point_reached: bool
    old_manifest_sha256: str | None
    new_manifest_sha256: str | None
    published_document_ids: tuple[str, ...]
    warnings: tuple[ProcessingWarning, ...]
    recovery_result: RecoveryResult | None
