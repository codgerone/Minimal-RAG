"""Read-only health verdict over the active V3 manifest, vectors and audit files."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Protocol

from rag.v3.application.publication import _verify_artifact_chunks
from rag.v3.application.assembly import canonical_json_bytes
from rag.v3.contracts.assembly import BuildProjection
from rag.v3.contracts.documents import SourceDocument
from rag.v3.contracts.retrieval import EmbeddingIdentity
from rag.v3.contracts.runtime import (
    DocumentStatus, HealthReport, IndexIssue, IndexIssueCode, SourceProbeResult,
)
from rag.v3.contracts.storage import (
    IndexIdentity, Manifest, ManifestDocumentRecord, RecoveryJournal, VectorRecord,
)


class ManifestReader(Protocol):
    def load(self, index: IndexIdentity) -> Manifest | None: ...


class VectorReader(Protocol):
    def collection_exists(self, index: IndexIdentity) -> bool: ...
    def list_records(self, index: IndexIdentity) -> tuple[VectorRecord, ...]: ...


class ArtifactReader(Protocol):
    def verify_active(self, index: IndexIdentity, record: ManifestDocumentRecord) -> None: ...
    def read_active_role(self, index: IndexIdentity,
                         record: ManifestDocumentRecord, role: str) -> bytes: ...


class RecoveryReader(Protocol):
    def list_pending(self, index: IndexIdentity) -> tuple[RecoveryJournal, ...]: ...


class SourceProber(Protocol):
    def probe(self, source: SourceDocument) -> SourceProbeResult: ...


def _issue(code: IndexIssueCode, document_id: str | None = None) -> IndexIssue:
    facts = {
        "pending_recovery": ("transaction", "recovery", "recover"),
        "recovery_failed": ("transaction", "recovery", "recover"),
        "manifest_invalid": ("configuration", "health", "force_rebuild"),
        "manifest_missing_with_collection": ("configuration", "health", "force_rebuild"),
        "configuration_mismatch": ("configuration", "configuration", "repair_configuration"),
        "collection_missing_with_manifest": ("configuration", "vector_store", "incremental_ingest"),
        "collection_manifest_count_mismatch": ("configuration", "vector_store", "force_rebuild"),
        "unknown_vector_document": ("configuration", "vector_store", "force_rebuild"),
        "source_new": ("document", "source_discovery", "incremental_ingest"),
        "source_changed": ("document", "source_discovery", "incremental_ingest"),
        "source_missing": ("document", "source_discovery", "prune_after_confirmation"),
        "source_unprocessable": ("document", "source_probe", "repair_source"),
        "vector_record_invalid": ("document", "vector_store", "incremental_ingest"),
        "artifact_invalid": ("artifact", "health", "incremental_ingest"),
    }
    scope, stage, remediation = facts[code]
    return IndexIssue(code, scope, stage, document_id, True, code.replace("_", " "), remediation)


def _status(source: SourceDocument | None, entry: ManifestDocumentRecord | None,
            state: str, issues: tuple[IndexIssue, ...],
            probe: SourceProbeResult | None = None) -> DocumentStatus:
    return DocumentStatus(
        source.document_id if source is not None else entry.document_id,
        source.relative_path if source is not None else entry.relative_path,
        source.document_name if source is not None else entry.document_name,
        state,
        source.file_hash if source is not None and state != "unassessed" else None,
        entry.file_hash if entry is not None and state != "new" else None,
        (probe.page_count if probe is not None else
         entry.page_count if entry is not None and state != "missing"
         else None),
        entry.chunk_count if entry is not None and state not in {
            "new", "unprocessable"} else None,
        entry.indexed_at if entry is not None and state not in {
            "new", "unprocessable"} else None,
        issues,
    )


class IndexHealth:
    def __init__(self, expected_projection: BuildProjection,
                 embedding_identity: EmbeddingIdentity,
                 source_probe: SourceProber, manifest: ManifestReader,
                 vector: VectorReader, artifacts: ArtifactReader,
                 recovery: RecoveryReader):
        self.expected_projection = expected_projection
        self.embedding_identity = embedding_identity
        self.source_probe = source_probe
        self.manifest = manifest
        self.vector = vector
        self.artifacts = artifacts
        self.recovery = recovery

    def check(self, index: IndexIdentity,
              sources: tuple[SourceDocument, ...]) -> HealthReport:
        checked_at = datetime.now(timezone.utc).isoformat()
        by_source = {item.document_id: item for item in sources}
        if len(by_source) != len(sources):
            raise ValueError("duplicate registry document identity")
        issues: list[IndexIssue] = []
        manifest = None
        try:
            pending = self.recovery.list_pending(index)
            if pending:
                issues.append(_issue("recovery_failed" if any(
                    item.state == "recovery_failed" for item in pending) else "pending_recovery"))
        except Exception:
            issues.append(_issue("recovery_failed"))
        try:
            manifest = self.manifest.load(index)
        except Exception:
            issues.append(_issue("manifest_invalid"))
        by_record = manifest.documents if manifest is not None else {}
        if manifest is not None and (canonical_json_bytes(asdict(manifest.build_configuration))
                                     != canonical_json_bytes(asdict(self.expected_projection))
                                     or manifest.embedding_identity != self.embedding_identity):
            issues.append(_issue("configuration_mismatch"))
        if not issues:
            try:
                exists = self.vector.collection_exists(index)
                if exists and manifest is None:
                    issues.append(_issue("manifest_missing_with_collection"))
                elif manifest is not None and not exists:
                    issues.append(_issue("collection_missing_with_manifest"))
            except Exception:
                issues.append(_issue("collection_manifest_count_mismatch"))
        records: tuple[VectorRecord, ...] = ()
        if not issues and manifest is not None:
            try:
                records = self.vector.list_records(index)
                if any(item.document_id not in by_record for item in records):
                    issues.append(_issue("unknown_vector_document"))
                if len(records) != sum(item.chunk_count for item in by_record.values()):
                    issues.append(_issue("collection_manifest_count_mismatch"))
            except Exception:
                issues.append(_issue("collection_manifest_count_mismatch"))
        identifiers = sorted(set(by_source) | set(by_record),
                             key=lambda key: (by_source[key].relative_path if key in by_source
                                              else by_record[key].relative_path, key))
        if issues:
            statuses = tuple(_status(by_source.get(key), by_record.get(key), "unassessed", ())
                             for key in identifiers)
            return HealthReport(False, index, statuses, tuple(issues), checked_at, False)

        by_vector: dict[str, list[VectorRecord]] = {}
        for item in records:
            by_vector.setdefault(item.document_id, []).append(item)
        statuses = []
        for document_id in identifiers:
            source = by_source.get(document_id)
            entry = by_record.get(document_id)
            item_issues: list[IndexIssue] = []
            probe = None
            if source is None:
                state = "missing"
                item_issues.append(_issue("source_missing", document_id))
            elif entry is None or source.file_hash != entry.file_hash:
                probe = self.source_probe.probe(source)
                if probe.status == "unprocessable":
                    state = "unprocessable"
                    item_issues.append(_issue("source_unprocessable", document_id))
                elif entry is None:
                    state = "new"
                    item_issues.append(_issue("source_new", document_id))
                else:
                    state = "changed"
                    item_issues.append(_issue("source_changed", document_id))
            else:
                own = tuple(by_vector.get(document_id, ()))
                if (len(own) != entry.chunk_count
                        or any(record.build_id != entry.build_id
                               or record.file_hash != entry.file_hash
                               or record.metadata.build_fingerprint != index.build_fingerprint
                               or len(record.embedding) != self.embedding_identity.dimension
                               for record in own)):
                    item_issues.append(_issue("vector_record_invalid", document_id))
                try:
                    self.artifacts.verify_active(index, entry)
                    _verify_artifact_chunks(self.artifacts.read_active_role(index, entry, "chunks"), own)
                except Exception:
                    item_issues.append(_issue("artifact_invalid", document_id))
                state = "invalid" if item_issues else "current"
            status = _status(source, entry, state, tuple(item_issues), probe)
            statuses.append(status)
            issues.extend(item_issues)
        issues.sort(key=lambda item: (item.code, item.document_id or ""))
        return HealthReport(manifest is not None and not issues
                            and all(item.state == "current" for item in statuses),
                            index, tuple(statuses), tuple(issues), checked_at, True)
