"""Stage snapshot file identities and immutable handoff to publication."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias

from rag.v3.contracts.documents import ChunkBatch, ParsedDocument, PrimaryDocument
from rag.v3.contracts.processing import NativeParserEvidence
from rag.v3.contracts.tables import (
    ContentResolution, GroupingReport, PreparedTableContent, ScoringReport,
    TableExtractionReport,
)

ArtifactRole: TypeAlias = Literal[
    "main_parser_native", "main_parser", "table_extractor", "table_extractor_review",
    "table_selector", "table_selector_review", "table_content_preparation",
    "table_content_preparation_review", "parsed_document", "chunks", "chunk_review",
]


@dataclass(frozen=True)
class BoundExtractionReport:
    binding_id: str
    plugin_id: str
    order: int
    report: TableExtractionReport


@dataclass(frozen=True)
class TableExtractionSnapshot:
    reports: tuple[BoundExtractionReport, ...]


@dataclass(frozen=True)
class TableSelectionSnapshot:
    grouping: GroupingReport
    scoring: ScoringReport
    resolutions: tuple[ContentResolution, ...]


@dataclass(frozen=True)
class TableContentPreparationSnapshot:
    prepared_tables: tuple[PreparedTableContent, ...]


ArtifactPayload: TypeAlias = (
    NativeParserEvidence | PrimaryDocument | TableExtractionSnapshot |
    TableSelectionSnapshot | TableContentPreparationSnapshot | ParsedDocument | ChunkBatch
)


@dataclass(frozen=True)
class ArtifactEnvelope:
    schema_version: Literal[
        "main_parser_native_v3", "main_parser_v3", "table_extractor_v3",
        "table_selector_v3", "table_content_preparation_v3", "parsed_document_v3",
        "document_chunks_v3",
    ]
    document_id: str
    build_id: str
    file_hash: str
    build_fingerprint: str
    payload: ArtifactPayload


@dataclass(frozen=True)
class ArtifactFileRef:
    role: ArtifactRole
    relative_path: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        if (not re.fullmatch(r"[0-9a-f]{64}", self.sha256) or self.size_bytes < 0
                or "/" in self.relative_path or "\\" in self.relative_path
                or self.relative_path in {"", ".", ".."}):
            raise ValueError("invalid artifact file reference")


@dataclass(frozen=True)
class SnapshotManifest:
    schema_version: Literal["snapshot_manifest_v3"]
    document_id: str
    build_id: str
    file_hash: str
    build_fingerprint: str
    main_parser_plugin_id: str
    attached_slots: tuple[str, ...]
    unattached_slots: tuple[str, ...]
    files: tuple[ArtifactFileRef, ...]


@dataclass(frozen=True)
class StagedArtifacts:
    staging_path: Path
    document_id: str
    build_id: str
    file_hash: str
    build_fingerprint: str
    snapshot_manifest_sha256: str
    files: tuple[ArtifactFileRef, ...]
