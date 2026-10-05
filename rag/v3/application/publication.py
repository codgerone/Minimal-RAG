"""Manifest commit point and evidence driven V3 index recovery."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from typing import Callable, ContextManager, Protocol

from rag.v3.application.artifact_paths import build_artifact_path
from rag.v3.application.assembly import canonical_json_bytes, readable_json_bytes
from rag.v3.application.vector_compare import records_equal, snapshots_equal
from rag.v3.contracts.artifacts import StagedArtifacts
from rag.v3.contracts.documents import ProcessingWarning
from rag.v3.contracts.runtime import RecoveryResult
from rag.v3.contracts.storage import (
    ArtifactSnapshot, IndexIdentity, Manifest, ManifestDocumentRecord,
    ManifestSnapshot, PublicationRequest, PublicationResult, RecoveryJournal,
    VectorRecord, VectorSnapshot,
)


class ManifestPort(Protocol):
    def raw(self, index: IndexIdentity) -> tuple[bytes | None, str | None]: ...
    def raw_unvalidated(self, index: IndexIdentity) -> tuple[bytes | None, str | None]: ...
    def decode(self, raw: bytes, index: IndexIdentity) -> Manifest: ...
    def save_atomic(self, index: IndexIdentity, manifest: Manifest,
                    expected_old_sha256: str | None) -> str: ...
    def restore_bytes(self, index: IndexIdentity, old_bytes: bytes | None,
                      expected_current_sha256: str | None) -> None: ...
    def byte_digest(self, index: IndexIdentity) -> str | None: ...


class VectorPort(Protocol):
    def list_records(self, index: IndexIdentity,
                     document_id: str | None = None) -> tuple[VectorRecord, ...]: ...
    def snapshot(self, index: IndexIdentity,
                 document_scope: tuple[str, ...] | None = None) -> VectorSnapshot: ...
    def restore(self, index: IndexIdentity, snapshot: VectorSnapshot) -> None: ...
    def replace_collection(self, index: IndexIdentity, records: tuple[VectorRecord, ...]) -> None: ...
    def replace_document(self, index: IndexIdentity, document_id: str,
                         records: tuple[VectorRecord, ...]) -> None: ...
    def delete_document(self, index: IndexIdentity, document_id: str) -> None: ...


class ArtifactPort(Protocol):
    def read_active_role(self, index: IndexIdentity, record: ManifestDocumentRecord,
                         role: str) -> bytes: ...
    def verify_active(self, index: IndexIdentity, record: ManifestDocumentRecord) -> None: ...
    def stage(self, index: IndexIdentity, transaction_id: str,
              staged: StagedArtifacts) -> StagedArtifacts: ...
    def snapshot(self, index: IndexIdentity, transaction_id: str,
                 record: ManifestDocumentRecord | None) -> ArtifactSnapshot: ...
    def publish(self, index: IndexIdentity, staged: StagedArtifacts,
                record: ManifestDocumentRecord) -> str: ...
    def quarantine(self, index: IndexIdentity, transaction_id: str,
                   record: ManifestDocumentRecord) -> ArtifactSnapshot: ...
    def restore(self, index: IndexIdentity, snapshot: ArtifactSnapshot,
                record: ManifestDocumentRecord | None) -> None: ...
    def verify_snapshot(self, index: IndexIdentity, snapshot: ArtifactSnapshot,
                        record: ManifestDocumentRecord | None) -> None: ...
    def cleanup_obsolete(self, index: IndexIdentity,
                         active_records: tuple[ManifestDocumentRecord, ...]) -> None: ...
    def cleanup_staging(self, index: IndexIdentity, transaction_id: str) -> None: ...


class RecoveryPort(Protocol):
    def list_pending(self, index: IndexIdentity) -> tuple[RecoveryJournal, ...]: ...
    def write_lock(self, index: IndexIdentity) -> ContextManager[None]: ...
    def read(self, index: IndexIdentity, transaction_id: str) -> RecoveryJournal | None: ...
    def raw(self, index: IndexIdentity, transaction_id: str) -> tuple[bytes | None, str | None]: ...
    def update(self, index: IndexIdentity, journal: RecoveryJournal,
               expected_old_sha256: str | None) -> str: ...
    def clear(self, index: IndexIdentity, transaction_id: str,
              expected_old_sha256: str | None) -> None: ...
    def cleanup(self, index: IndexIdentity, transaction_id: str) -> None: ...
    def read_manifest_snapshot(self, index: IndexIdentity,
                               transaction_id: str) -> tuple[ManifestSnapshot, bytes | None]: ...
    def read_vector_snapshot(self, index: IndexIdentity,
                             transaction_id: str) -> VectorSnapshot: ...
    def read_artifact_snapshots(self, index: IndexIdentity,
                                transaction_id: str) -> tuple[ArtifactSnapshot, ...]: ...
    def save_manifest_snapshot(self, index: IndexIdentity, transaction_id: str,
                               old_bytes: bytes | None) -> ManifestSnapshot: ...
    def save_vector_snapshot(self, index: IndexIdentity, transaction_id: str,
                             snapshot: VectorSnapshot) -> None: ...
    def save_artifact_snapshots(self, index: IndexIdentity, transaction_id: str,
                                snapshots: tuple[ArtifactSnapshot, ...]) -> None: ...
    def prepare(self, index: IndexIdentity, journal: RecoveryJournal) -> str: ...


class PublicationError(RuntimeError):
    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(detail)


def manifest_digest(manifest: Manifest) -> str:
    return hashlib.sha256(readable_json_bytes(asdict(manifest))).hexdigest()


def _verify_artifact_chunks(raw: bytes, records: tuple[VectorRecord, ...]) -> None:
    try:
        envelope = json.loads(raw)
        chunks = envelope["payload"]["chunks"]
        if len(chunks) != len(records):
            raise ValueError("artifact and vector chunk counts differ")
        by_id = {record.chunk_id: record for record in records}
        if len(by_id) != len(records):
            raise ValueError("duplicate vector chunk IDs")
        if {chunk["chunk_id"] for chunk in chunks} != set(by_id):
            raise ValueError("artifact and vector chunk ID sets differ")
        for chunk in chunks:
            record = by_id[chunk["chunk_id"]]
            metadata = record.metadata
            if (chunk["text"] != record.text
                    or chunk["chunk_index"] != metadata.chunk_index
                    or chunk["kind"] != metadata.kind
                    or chunk["token_count"] != metadata.token_count
                    or chunk["parent_unit_id"] != metadata.parent_unit_id
                    or chunk["fragment_index"] != metadata.fragment_index
                    or chunk["fragment_count"] != metadata.fragment_count
                    or canonical_json_bytes(chunk["sources"]) != canonical_json_bytes(
                        [asdict(item) for item in metadata.sources])):
                raise ValueError("artifact and vector chunk content differs")
    except Exception as exc:
        raise PublicationError("publication_failed", "artifact and vector chunks differ") from exc


class PublicationVerifier:
    def __init__(self, manifest: ManifestPort, vector: VectorPort,
                 artifacts: ArtifactPort):
        self.manifest = manifest
        self.vector = vector
        self.artifacts = artifacts

    def verify(self, index: IndexIdentity, expected_sha256: str) -> Manifest:
        raw, actual_sha = self.manifest.raw(index)
        if raw is None or actual_sha != expected_sha256:
            raise PublicationError("publication_failed", "active manifest digest differs")
        manifest = self.manifest.decode(raw, index)
        records = self.vector.list_records(index)
        by_document: dict[str, list[VectorRecord]] = {}
        for item in records:
            by_document.setdefault(item.document_id, []).append(item)
        if set(by_document) != set(manifest.documents):
            raise PublicationError("publication_failed", "manifest and vector document sets differ")
        for document_id, entry in manifest.documents.items():
            own = tuple(by_document[document_id])
            if (len(own) != entry.chunk_count
                    or any(record.build_id != entry.build_id
                           or record.file_hash != entry.file_hash
                           or record.metadata.build_fingerprint != index.build_fingerprint
                           or len(record.embedding) != manifest.embedding_identity.dimension
                           for record in own)):
                raise PublicationError("publication_failed", "manifest and vector identity differs")
            self.artifacts.verify_active(index, entry)
            _verify_artifact_chunks(self.artifacts.read_active_role(index, entry, "chunks"), own)
        return manifest


class Recovery:
    def __init__(self, manifest: ManifestPort, vector: VectorPort,
                 artifacts: ArtifactPort, recovery: RecoveryPort,
                 checkpoint: Callable[[str], None] | None = None):
        self.manifest = manifest
        self.vector = vector
        self.artifacts = artifacts
        self.recovery = recovery
        self.verifier = PublicationVerifier(manifest, vector, artifacts)
        self.checkpoint = checkpoint or (lambda _: None)

    def recover(self, index: IndexIdentity, transaction_id: str) -> RecoveryResult:
        if transaction_id not in {item.transaction_id for item in self.recovery.list_pending(index)}:
            raise PublicationError("recovery_failed", "transaction is not pending in this index")
        with self.recovery.write_lock(index):
            return self._recover_locked(index, transaction_id)

    def _advance(self, index: IndexIdentity, journal: RecoveryJournal,
                 **changes: object) -> RecoveryJournal:
        _, old_sha = self.recovery.raw(index, journal.transaction_id)
        updated = replace(journal, **changes)
        self.recovery.update(index, updated, old_sha)
        return updated

    def _recover_locked(self, index: IndexIdentity, transaction_id: str) -> RecoveryResult:
        journal = self.recovery.read(index, transaction_id)
        if journal is None:
            raise PublicationError("recovery_failed", "pending journal vanished")
        try:
            committed_manifest = self.verifier.verify(index, journal.expected_new_manifest_sha256)
        except Exception:
            committed = False
        else:
            committed = True
        if committed:
            try:
                self.artifacts.cleanup_obsolete(
                    index, tuple(committed_manifest.documents.values()))
                _, sha = self.recovery.raw(index, transaction_id)
                self.recovery.clear(index, transaction_id, sha)
                try:
                    self.recovery.cleanup(index, transaction_id)
                    self.artifacts.cleanup_staging(index, transaction_id)
                except Exception:
                    pass
                return RecoveryResult(transaction_id, index, "committed", True, True, True, ())
            except Exception:
                return RecoveryResult(transaction_id, index, "failed", True, True, True,
                                      ("pending_recovery",))
        verified_manifest = False
        verified_vectors = False
        verified_artifacts = False
        try:
            old_manifest, old_bytes = self.recovery.read_manifest_snapshot(index, transaction_id)
            if old_manifest.snapshot_path != journal.old_manifest_snapshot_path:
                raise PublicationError("snapshot_unreadable", "old manifest path differs")
            if old_manifest.bytes_sha256 != journal.old_manifest_sha256:
                raise PublicationError("snapshot_unreadable", "old manifest SHA differs")
            old_vector = self.recovery.read_vector_snapshot(index, transaction_id)
            if journal.old_vector_snapshot_path != f"recovery/{transaction_id}/old-vector.json":
                raise PublicationError("snapshot_unreadable", "old vector path differs")
            old_artifacts = self.recovery.read_artifact_snapshots(index, transaction_id)
            try:
                old_records = (self.manifest.decode(old_bytes, index).documents
                               if old_bytes is not None else {})
            except Exception:
                if journal.operation != "replace_collection":
                    raise PublicationError("snapshot_unreadable", "old manifest is invalid")
                old_records = {}
            old_paths = {item.artifact_path for item in old_records.values()}
            snapshot_paths = {item.active_path for item in old_artifacts}
            if (not old_paths.issubset(snapshot_paths)
                    or len(snapshot_paths) != len(old_artifacts)):
                raise PublicationError("snapshot_unreadable", "old artifact scope differs")
            journal = self._advance(index, journal, state="restoring",
                                    current_step="vector_restore", error_code=None,
                                    error_message=None)
            self.checkpoint("before_vector_restore")
            self.vector.restore(index, old_vector)
            self.checkpoint("after_vector_restore")
            verified_vectors = snapshots_equal(
                old_vector, self.vector.snapshot(index, old_vector.document_scope or None))
            if not verified_vectors:
                raise PublicationError("vector_restore_failed", "old vector readback differs")
            journal = self._advance(index, journal, current_step="artifact_restore")
            self.checkpoint("before_artifact_restore")
            for snapshot in old_artifacts:
                self.artifacts.restore(index, snapshot, None)
                self.artifacts.verify_snapshot(index, snapshot, None)
            self.checkpoint("after_artifact_restore")
            verified_artifacts = True
            journal = self._advance(index, journal, current_step="manifest_restore")
            self.checkpoint("before_manifest_restore")
            current_sha = self.manifest.byte_digest(index)
            if current_sha != old_manifest.bytes_sha256:
                self.manifest.restore_bytes(index, old_bytes, current_sha)
            actual, _ = self.manifest.raw_unvalidated(index)
            verified_manifest = actual == old_bytes
            if not verified_manifest:
                raise PublicationError("manifest_restore_failed", "old manifest bytes differ")
            self.checkpoint("after_manifest_restore")
            _, sha = self.recovery.raw(index, transaction_id)
            self.recovery.clear(index, transaction_id, sha)
            try:
                self.recovery.cleanup(index, transaction_id)
                self.artifacts.cleanup_staging(index, transaction_id)
            except Exception:
                pass
            return RecoveryResult(transaction_id, index, "rolled_back", True, True, True, ())
        except Exception as exc:
            code = getattr(exc, "code", "verification_failed")
            allowed = {"snapshot_unreadable", "journal_unreadable", "manifest_conflict",
                       "vector_restore_failed", "artifact_restore_failed",
                       "manifest_restore_failed", "verification_failed", "unsafe_path",
                       "lock_unavailable"}
            code = code if code in allowed else "verification_failed"
            try:
                self._advance(index, journal, state="recovery_failed", error_code=code,
                              error_message=str(exc) or code)
            except Exception:
                pass
            return RecoveryResult(transaction_id, index, "failed", verified_manifest,
                                  verified_vectors, verified_artifacts, ("recovery_failed",))


class Publisher:
    def __init__(self, manifest: ManifestPort, vector: VectorPort,
                 artifacts: ArtifactPort, recovery: RecoveryPort,
                 checkpoint: Callable[[str], None] | None = None):
        self.manifest = manifest
        self.vector = vector
        self.artifacts = artifacts
        self.recovery = recovery
        self.resolver = Recovery(manifest, vector, artifacts, recovery)
        self.checkpoint = checkpoint or (lambda _: None)

    def _check_request(self, request: PublicationRequest) -> None:
        index = request.index_identity
        if request.new_manifest.index_identity != index:
            raise PublicationError("publication_failed", "new manifest index differs")
        self.manifest.decode(canonical_json_bytes(asdict(request.new_manifest)), index)
        if request.operation == "replace_collection":
            if request.document_id is not None:
                raise PublicationError("publication_failed", "collection request has document ID")
            expected_docs = set(request.new_manifest.documents)
        else:
            if not request.document_id:
                raise PublicationError("publication_failed", "document request lacks document ID")
            expected_docs = ({request.document_id}
                             if request.operation == "replace_document" else set())
        if request.operation == "prune_document":
            if (request.records or request.staged_artifacts
                    or request.document_id in request.new_manifest.documents):
                raise PublicationError("publication_failed", "prune payload is not empty")
        else:
            if ({item.document_id for item in request.records} != expected_docs
                    or {item.document_id for item in request.staged_artifacts} != expected_docs
                    or len(request.staged_artifacts) != len(expected_docs)
                    or len({item.chunk_id for item in request.records}) != len(request.records)):
                raise PublicationError("publication_failed", "payload document set differs")
            for staged in request.staged_artifacts:
                entry = request.new_manifest.documents[staged.document_id]
                expected_files = {item.role: item.sha256 for item in staged.files}
                expected_files["snapshot_manifest"] = staged.snapshot_manifest_sha256
                if (entry.artifact_path != build_artifact_path(entry)
                        or entry.artifact_files != expected_files
                        or entry.chunk_count != sum(item.document_id == staged.document_id
                                                    for item in request.records)
                        or staged.build_id != entry.build_id
                        or staged.file_hash != entry.file_hash
                        or staged.build_fingerprint != index.build_fingerprint):
                    raise PublicationError("publication_failed", "staged document identity differs")
                self.artifacts.stage(index, request.transaction_id, staged)
        if any(len(item.embedding) != request.new_manifest.embedding_identity.dimension
               or item.metadata.build_fingerprint != index.build_fingerprint
               for item in request.records):
            raise PublicationError("publication_failed", "vector dimension or identity differs")

    def publish(self, request: PublicationRequest) -> PublicationResult:
        self._check_request(request)
        index = request.index_identity
        _, prelock_sha = (self.manifest.raw_unvalidated(index)
                          if request.operation == "replace_collection"
                          else self.manifest.raw(index))
        if prelock_sha != request.expected_old_manifest_sha256:
            raise PublicationError("manifest_conflict", "manifest changed before publication")
        if self.recovery.list_pending(index):
            raise PublicationError("publication_failed", "index has pending recovery")
        with self.recovery.write_lock(index):
            old_bytes, old_sha = (self.manifest.raw_unvalidated(index)
                                  if request.operation == "replace_collection"
                                  else self.manifest.raw(index))
            if old_sha != prelock_sha or self.recovery.list_pending(index):
                raise PublicationError("manifest_conflict", "index changed while taking lock")
            try:
                old_manifest = self.manifest.decode(old_bytes, index) if old_bytes is not None else None
            except Exception:
                if request.operation != "replace_collection":
                    raise
                old_manifest = None
            old_documents = old_manifest.documents if old_manifest is not None else {}
            if request.operation == "replace_collection":
                pass
            elif request.operation == "replace_document":
                if (set(request.new_manifest.documents) !=
                        set(old_documents) | {request.document_id}
                        or any(request.new_manifest.documents[key] != value
                               for key, value in old_documents.items()
                               if key != request.document_id)):
                    raise PublicationError("publication_failed", "document replacement changed other entries")
            elif (set(request.new_manifest.documents) !=
                    set(old_documents) - {request.document_id}
                    or any(request.new_manifest.documents[key] != value
                           for key, value in old_documents.items()
                           if key != request.document_id)):
                raise PublicationError("publication_failed", "prune changed other entries")
            if old_manifest is not None and (
                    old_manifest.embedding_identity != request.new_manifest.embedding_identity
                    or canonical_json_bytes(asdict(old_manifest.build_configuration))
                       != canonical_json_bytes(asdict(request.new_manifest.build_configuration))):
                raise PublicationError("publication_failed", "build identity changed inside index")
            if request.operation == "prune_document" and (
                    old_manifest is None or request.document_id not in old_manifest.documents):
                raise PublicationError("publication_failed", "pruned document is not active")
            old_vector = self.vector.snapshot(
                index, None if request.operation == "replace_collection" else (request.document_id,))
            old_entries = (old_manifest.documents if old_manifest is not None and
                           request.operation == "replace_collection" else
                           {request.document_id: old_manifest.documents[request.document_id]}
                           if old_manifest is not None and request.document_id in old_manifest.documents
                           else {})
            touched = {entry.artifact_path: entry for entry in old_entries.values()}
            if request.operation != "prune_document":
                new_entries = (request.new_manifest.documents.values()
                               if request.operation == "replace_collection" else
                               (request.new_manifest.documents[request.document_id],))
                for entry in new_entries:
                    touched.setdefault(entry.artifact_path, entry)
            old_artifacts = tuple(self.artifacts.snapshot(index, request.transaction_id, entry)
                                  for _, entry in sorted(touched.items()))
            self.recovery.save_manifest_snapshot(index, request.transaction_id, old_bytes)
            self.recovery.save_vector_snapshot(index, request.transaction_id, old_vector)
            self.recovery.save_artifact_snapshots(index, request.transaction_id, old_artifacts)
            expected_new_sha = manifest_digest(request.new_manifest)
            journal = RecoveryJournal(
                "index_recovery_v3", request.transaction_id, index, request.operation,
                request.document_id, "prepared", "none", old_sha,
                f"recovery/{request.transaction_id}/old-manifest.json",
                f"recovery/{request.transaction_id}/old-vector.json",
                (f"recovery/{request.transaction_id}/quarantine/"
                 f"{request.document_id}/{old_entries[request.document_id].build_id}"
                 if request.operation == "prune_document" else None),
                f"staging/{request.transaction_id}" if request.staged_artifacts else None,
                expected_new_sha, request.new_manifest.updated_at, None, None,
            )
            self.checkpoint("before_prepare")
            self.recovery.prepare(index, journal)
            try:
                self.checkpoint("prepared")
                journal = self.resolver._advance(index, journal, state="mutating",
                                                 current_step="vector_replace")
                self.checkpoint("before_vector_replace")
                if request.operation == "replace_collection":
                    self.vector.replace_collection(index, request.records)
                elif request.operation == "replace_document":
                    self.vector.replace_document(index, request.document_id, request.records)
                else:
                    self.vector.delete_document(index, request.document_id)
                self.checkpoint("after_vector_replace")
                journal = self.resolver._advance(index, journal, current_step="vector_verify")
                self.checkpoint("before_vector_verify")
                actual = self.vector.list_records(index,
                    request.document_id if request.operation != "replace_collection" else None)
                if not records_equal(tuple(sorted(request.records,
                                                   key=lambda item: item.chunk_id)), actual):
                    raise PublicationError("publication_failed", "new vector readback differs")
                self.checkpoint("after_vector_verify")
                journal = self.resolver._advance(index, journal, current_step="artifact_publish")
                self.checkpoint("before_artifact_publish")
                if request.operation == "prune_document":
                    self.artifacts.quarantine(index, request.transaction_id,
                                              old_entries[request.document_id])
                else:
                    for staged in request.staged_artifacts:
                        self.artifacts.publish(index, staged,
                            request.new_manifest.documents[staged.document_id])
                self.checkpoint("after_artifact_publish")
                journal = self.resolver._advance(index, journal, current_step="artifact_verify")
                self.checkpoint("before_artifact_verify")
                if request.operation != "prune_document":
                    for staged in request.staged_artifacts:
                        self.artifacts.verify_active(index,
                            request.new_manifest.documents[staged.document_id])
                self.checkpoint("after_artifact_verify")
                journal = self.resolver._advance(index, journal, current_step="manifest_publish")
                self.checkpoint("before_manifest_publish")
                self.manifest.save_atomic(index, request.new_manifest, old_sha)
                self.checkpoint("after_manifest_publish")
                self.resolver.verifier.verify(index, expected_new_sha)
                self.checkpoint("after_commit_verify")
                journal = self.resolver._advance(index, journal,
                                                 current_step="artifact_cleanup")
                self.checkpoint("before_cleanup")
                self.artifacts.cleanup_obsolete(
                    index, tuple(request.new_manifest.documents.values()))
            except Exception:
                result = self.resolver._recover_locked(index, request.transaction_id)
                return PublicationResult(request.transaction_id, request.operation, index,
                    request.document_id, result.outcome, result.outcome == "committed",
                    old_sha, expected_new_sha if result.outcome == "committed" else None,
                    tuple(sorted(request.new_manifest.documents)) if result.outcome == "committed"
                    and request.operation == "replace_collection" else
                    (request.document_id,) if result.outcome == "committed"
                    and request.operation == "replace_document" else (), (), result)
            warnings: tuple[ProcessingWarning, ...] = ()
            try:
                _, journal_sha = self.recovery.raw(index, request.transaction_id)
                self.recovery.clear(index, request.transaction_id, journal_sha)
                self.recovery.cleanup(index, request.transaction_id)
                self.artifacts.cleanup_staging(index, request.transaction_id)
                self.checkpoint("after_cleanup")
            except Exception:
                warnings = (ProcessingWarning("artifact_cleanup_failed", "artifact_cleanup",
                                              "postcommit recovery cleanup is pending", ()),)
            published = (tuple(sorted(request.new_manifest.documents))
                         if request.operation == "replace_collection" else
                         (request.document_id,) if request.operation == "replace_document" else ())
            return PublicationResult(request.transaction_id, request.operation, index,
                                     request.document_id, "committed", True, old_sha,
                                     expected_new_sha, published, warnings, None)
