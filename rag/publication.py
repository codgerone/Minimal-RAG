"""Crash-evident document publication with persisted rollback evidence."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal

from rag.errors import ManifestError, VectorStoreError
from rag.models import BuiltDocument
from rag.document_registry import make_artifact_document_name
from rag.pipeline_manifest import (
    ManifestDocumentRecord, PipelineManifest, load_pipeline_manifest,
    save_pipeline_manifest_atomic,
)


class IndexPublicationError(ManifestError):
    pass


@dataclass(frozen=True)
class RecoveryJournal:
    pipeline_id: Literal["v1", "v2"]
    collection_name: str
    operation: Literal["replace_document", "force_rebuild", "prune_document"]
    document_id: str | None
    build_id: str
    state: Literal["prepared", "mutating", "restoring", "recovery_failed"]
    current_step: Literal[
        "vector_replace", "vector_verify", "artifact_publish", "manifest_publish",
        "vector_restore", "manifest_restore", "artifact_restore", "artifact_cleanup",
    ]
    old_manifest_sha256: str | None
    old_manifest_path: str
    snapshot_path: str
    artifact_quarantine_path: str | None
    created_at: str
    error_type: str | None
    error_message: str | None
    schema_version: Literal["index_recovery_v1"] = "index_recovery_v1"

    def __post_init__(self) -> None:
        if self.schema_version != "index_recovery_v1":
            raise IndexPublicationError("Recovery journal schema_version 不受支持。")
        if self.operation == "force_rebuild" and self.document_id is not None:
            raise IndexPublicationError("force_rebuild journal 的 document_id 必须为空。")
        if self.operation != "force_rebuild" and not self.document_id:
            raise IndexPublicationError("文档级 recovery journal 必须包含 document_id。")
        if self.operation == "prune_document":
            if not self.artifact_quarantine_path:
                raise IndexPublicationError("prune journal 必须包含 artifact quarantine 路径。")
        elif self.artifact_quarantine_path is not None:
            raise IndexPublicationError("仅 prune journal 可包含 artifact quarantine 路径。")
        failed = self.state == "recovery_failed"
        if failed != bool(self.error_type and self.error_message):
            raise IndexPublicationError("Recovery journal 的 state 与错误字段不一致。")


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _safe_remove_tree(path: Path, allowed_root: Path) -> None:
    resolved, root = path.resolve(), allowed_root.resolve()
    if not resolved.is_relative_to(root) or resolved == root:
        raise IndexPublicationError(f"拒绝清理越界路径：{resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)


class PublicationManager:
    def __init__(self, *, project_root: Path, runtime, vector_store, clock) -> None:
        self.project_root = project_root.resolve()
        self.runtime = runtime
        self.vector_store = vector_store
        self.clock = clock
        self.recovery_root = runtime.manifest_path.parents[2] / "recovery" / runtime.pipeline_id

    def _relative(self, path: Path) -> str:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.project_root):
            raise IndexPublicationError(f"持久化路径超出项目目录：{resolved}")
        return resolved.relative_to(self.project_root).as_posix()

    def _write_journal(self, path: Path, journal: RecoveryJournal) -> None:
        _atomic_bytes(path, _json_bytes(asdict(journal)))
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if loaded["build_id"] != journal.build_id or loaded["state"] != journal.state:
            raise IndexPublicationError("Recovery journal 回读校验失败。")

    def _cleanup_superseded_artifacts(
        self, previous: PipelineManifest, current: PipelineManifest,
    ) -> None:
        """Best-effort cleanup after the new manifest is already authoritative."""
        for document_id, old_record in previous.documents.items():
            new_record = current.documents.get(document_id)
            if not old_record.artifact_path or (
                new_record is not None
                and new_record.artifact_path == old_record.artifact_path
            ):
                continue
            old_path = self.project_root / old_record.artifact_path
            try:
                _safe_remove_tree(old_path, old_path.parents[1])
            except Exception:
                pass  # committed build remains valid; readiness reports the orphan.

    def pending_journals(self) -> tuple[Path, ...]:
        if not self.recovery_root.exists():
            return ()
        return tuple(sorted(self.recovery_root.glob("*/journal.json")))

    def recover_pending(self) -> bool:
        """Finish or roll back the sole interrupted transaction for this pipeline."""
        journals = self.pending_journals()
        if not journals:
            return False
        if len(journals) != 1:
            raise IndexPublicationError("当前 pipeline 存在多个 recovery journal，无法安全判断恢复顺序。")
        journal_path = journals[0]
        journal = None
        try:
            raw = json.loads(journal_path.read_text(encoding="utf-8"))
            if set(raw) != set(RecoveryJournal.__dataclass_fields__):
                raise IndexPublicationError("Recovery journal 字段不符合契约。")
            journal = RecoveryJournal(**raw)
            if journal.pipeline_id != self.runtime.pipeline_id or journal.collection_name != self.runtime.collection_name:
                raise IndexPublicationError("Recovery journal 与当前 pipeline 身份不一致。")
            recovery = journal_path.parent.resolve()
            if recovery.parent != self.recovery_root.resolve() or recovery.name != journal.build_id:
                raise IndexPublicationError("Recovery journal 所在路径与 build_id 不一致。")
            snapshot_path = self._validated_recovery_path(journal.snapshot_path, recovery)
            old_manifest_path = self._validated_recovery_path(journal.old_manifest_path, recovery)
            snapshot_raw = json.loads(snapshot_path.read_text(encoding="utf-8"))
            snapshot = {key: snapshot_raw[key] for key in ("ids", "documents", "embeddings", "metadatas")}
            self._validate_snapshot(snapshot)
            old_bytes = old_manifest_path.read_bytes()
            expected_hash = hashlib.sha256(old_bytes).hexdigest() if journal.old_manifest_sha256 else None
            if expected_hash != journal.old_manifest_sha256:
                raise IndexPublicationError("Recovery old manifest hash 校验失败。")
            if self._transaction_is_committed(journal):
                self._finish_committed_recovery(journal, recovery)
                return True
            restoring = replace(journal, state="restoring", current_step="vector_restore",
                                error_type=None, error_message=None)
            self._write_journal(journal_path, restoring)
            self._rollback_persisted(restoring, snapshot, old_bytes, recovery)
            return True
        except Exception as exc:
            try:
                if journal is not None:
                    self._write_journal(journal_path, replace(
                        journal, state="recovery_failed", error_type=type(exc).__name__,
                        error_message=str(exc)))
            except Exception:
                pass
            raise IndexPublicationError("中断事务恢复失败；已保留 recovery 证据。", cause=exc) from exc

    def _validated_recovery_path(self, relative: str, recovery: Path) -> Path:
        path = (self.project_root / relative).resolve()
        if path.parent != recovery:
            raise IndexPublicationError("Recovery 证据路径越界或不在事务目录内。")
        return path

    @staticmethod
    def _validate_snapshot(snapshot: dict) -> None:
        if set(snapshot) != {"ids", "documents", "embeddings", "metadatas"}:
            raise IndexPublicationError("Recovery snapshot 字段不符合契约。")
        lengths = {len(snapshot[key]) for key in snapshot}
        if len(lengths) != 1 or len(snapshot["ids"]) != len(set(snapshot["ids"])):
            raise IndexPublicationError("Recovery snapshot 数组长度或 ID 唯一性校验失败。")

    def _transaction_is_committed(self, journal: RecoveryJournal) -> bool:
        try:
            manifest = load_pipeline_manifest(self.runtime.manifest_path, self.runtime)
        except ManifestError:
            return False
        if manifest is None:
            return False
        if journal.operation == "prune_document":
            assert journal.document_id is not None
            return journal.document_id not in manifest.documents and self.vector_store.count_document(journal.document_id) == 0
        if journal.operation == "replace_document":
            assert journal.document_id is not None
            record = manifest.documents.get(journal.document_id)
            return bool(record and record.build_id == journal.build_id and self._record_is_readable(record))
        return all(self._record_is_readable(record) for record in manifest.documents.values()) and (
            self.vector_store.count_all() == sum(item.chunk_count for item in manifest.documents.values())
        )

    def _record_is_readable(self, record: ManifestDocumentRecord) -> bool:
        if self.vector_store.count_document(record.document_id) != record.chunk_count:
            return False
        return record.artifact_path is None or (self.project_root / record.artifact_path).is_dir()

    def _finish_committed_recovery(self, journal: RecoveryJournal, recovery: Path) -> None:
        journal_path = recovery / "journal.json"
        if journal.operation == "prune_document":
            journal_path.unlink()
        _safe_remove_tree(recovery, self.recovery_root)

    def _rollback_persisted(self, journal: RecoveryJournal, snapshot: dict, old_bytes: bytes, recovery: Path) -> None:
        journal_path = recovery / "journal.json"
        if journal.operation == "force_rebuild":
            self.vector_store.restore_snapshot(snapshot, recreate=True)
        else:
            assert journal.document_id is not None
            self.vector_store.delete_document(journal.document_id)
            self.vector_store.restore_snapshot(snapshot)
        self._write_journal(journal_path, replace(journal, current_step="manifest_restore"))
        if journal.old_manifest_sha256 is None:
            if self.runtime.manifest_path.exists():
                self.runtime.manifest_path.unlink()
        else:
            _atomic_bytes(self.runtime.manifest_path, old_bytes)
        self._write_journal(journal_path, replace(journal, current_step="artifact_restore"))
        if journal.operation == "prune_document" and journal.artifact_quarantine_path:
            quarantine = self._validated_recovery_path(journal.artifact_quarantine_path, recovery)
            old_manifest = load_pipeline_manifest(self.runtime.manifest_path, self.runtime)
            record = old_manifest.documents.get(journal.document_id) if old_manifest else None
            if quarantine.exists() and record and record.artifact_path:
                target = (self.project_root / record.artifact_path).parent
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    raise IndexPublicationError("prune 恢复目标 artifact 目录已存在。")
                os.replace(quarantine, target)
        restored = self.vector_store.snapshot(None if journal.operation == "force_rebuild" else journal.document_id)
        if set(restored["ids"]) != set(snapshot["ids"]):
            raise IndexPublicationError("恢复后的向量 ID 与持久化 snapshot 不一致。")
        current_bytes = self.runtime.manifest_path.read_bytes() if self.runtime.manifest_path.exists() else b""
        if current_bytes != old_bytes:
            raise IndexPublicationError("恢复后的 manifest bytes 与持久化旧版本不一致。")
        _safe_remove_tree(recovery, self.recovery_root)

    def replace_document(
        self, manifest: PipelineManifest, built: BuiltDocument, timestamp: str,
    ) -> PipelineManifest:
        result = built.result
        source = result.source
        recovery = self.recovery_root / result.build_id
        if self.pending_journals():
            raise IndexPublicationError("当前 pipeline 存在未完成 recovery journal，拒绝开始写操作。")
        recovery.mkdir(parents=True)
        snapshot_path = recovery / "snapshot.json"
        old_manifest_path = recovery / "old-manifest.bin"
        journal_path = recovery / "journal.json"
        old_bytes = self.runtime.manifest_path.read_bytes() if self.runtime.manifest_path.exists() else b""
        snapshot = self.vector_store.snapshot(source.document_id)
        snapshot_payload = {"document_id": source.document_id, **snapshot}
        _atomic_bytes(snapshot_path, _json_bytes(snapshot_payload))
        _atomic_bytes(old_manifest_path, old_bytes)
        if json.loads(snapshot_path.read_text(encoding="utf-8"))["document_id"] != source.document_id:
            raise IndexPublicationError("VectorSnapshot 回读身份不一致。")
        if old_manifest_path.read_bytes() != old_bytes:
            raise IndexPublicationError("旧 Manifest bytes 回读不一致。")
        journal = RecoveryJournal(
            self.runtime.pipeline_id, self.runtime.collection_name, "replace_document",
            source.document_id, result.build_id, "prepared", "vector_replace",
            hashlib.sha256(old_bytes).hexdigest() if self.runtime.manifest_path.exists() else None,
            self._relative(old_manifest_path), self._relative(snapshot_path), None,
            timestamp, None, None,
        )
        self._write_journal(journal_path, journal)
        published_path: Path | None = None
        try:
            journal = replace(journal, state="mutating")
            self._write_journal(journal_path, journal)
            self.vector_store.delete_document(source.document_id)
            if result.pipeline_id == "v2":
                self.vector_store.add_v2_build(result, built.embeddings, self.runtime.build_config_fingerprint)
            else:
                self.vector_store.add_chunks(result.chunks, built.embeddings)
            journal = replace(journal, current_step="vector_verify")
            self._write_journal(journal_path, journal)
            current = self.vector_store.snapshot(source.document_id)
            if set(current["ids"]) != {chunk.chunk_id for chunk in result.chunks}:
                raise VectorStoreError("新向量回读 chunk IDs 不一致。")

            artifact_path = None
            journal = replace(journal, current_step="artifact_publish")
            self._write_journal(journal_path, journal)
            if result.pipeline_id == "v2":
                assert result.artifact_stage is not None
                artifact_document = make_artifact_document_name(
                    source.relative_path, source.document_id
                )
                published_path = (result.artifact_stage.staging_path.parents[2] / "documents" /
                                  artifact_document / result.build_id)
                published_path.parent.mkdir(parents=True, exist_ok=True)
                if published_path.exists():
                    raise IndexPublicationError(f"不可变 artifact build 已存在：{published_path}")
                os.replace(result.artifact_stage.staging_path, published_path)
                artifact_path = self._relative(published_path)

            record = ManifestDocumentRecord(
                source.document_id, source.document_name, source.relative_path, source.file_hash,
                result.build_id, self.runtime.build_config_fingerprint,
                result.stats.page_count, result.stats.character_count, result.stats.chunk_count,
                artifact_path, timestamp,
            )
            documents = dict(manifest.documents)
            old_record = documents.get(source.document_id)
            documents[source.document_id] = record
            updated = replace(manifest, documents=documents, updated_at=timestamp)
            journal = replace(journal, current_step="manifest_publish")
            self._write_journal(journal_path, journal)
            save_pipeline_manifest_atomic(self.runtime.manifest_path, updated, result.build_id)
            _safe_remove_tree(recovery, self.recovery_root)
            if old_record and old_record.artifact_path and old_record.artifact_path != artifact_path:
                try:
                    _safe_remove_tree(self.project_root / old_record.artifact_path,
                                      result.artifact_stage.staging_path.parents[2] / "documents")
                except Exception:
                    pass  # committed build remains valid; health reports orphan/cleanup residue.
            return updated
        except Exception as exc:
            try:
                journal = replace(journal, state="restoring", current_step="vector_restore")
                self._write_journal(journal_path, journal)
                self.vector_store.delete_document(source.document_id)
                self.vector_store.restore_snapshot(snapshot)
                journal = replace(journal, current_step="manifest_restore")
                self._write_journal(journal_path, journal)
                if journal.old_manifest_sha256 is None:
                    if self.runtime.manifest_path.exists():
                        self.runtime.manifest_path.unlink()
                else:
                    _atomic_bytes(self.runtime.manifest_path, old_bytes)
                journal = replace(journal, current_step="artifact_restore")
                self._write_journal(journal_path, journal)
                if published_path is not None and published_path.exists():
                    _safe_remove_tree(published_path, published_path.parent)
                if result.artifact_stage and result.artifact_stage.staging_path.exists():
                    _safe_remove_tree(result.artifact_stage.staging_path,
                                      result.artifact_stage.staging_path.parents[2])
                restored = self.vector_store.snapshot(source.document_id)
                if set(restored["ids"]) != set(snapshot["ids"]):
                    raise IndexPublicationError("回滚后向量 ID 与 snapshot 不一致。")
                if (self.runtime.manifest_path.read_bytes() if self.runtime.manifest_path.exists() else b"") != old_bytes:
                    raise IndexPublicationError("回滚后 Manifest bytes 不一致。")
                _safe_remove_tree(recovery, self.recovery_root)
            except Exception as restore_exc:
                failed = replace(journal, state="recovery_failed",
                                 error_type=type(restore_exc).__name__, error_message=str(restore_exc))
                self._write_journal(journal_path, failed)
                raise IndexPublicationError(
                    "索引发布失败且自动恢复未能完成；已保留 recovery 证据。",
                    cause=restore_exc,
                ) from exc
            raise IndexPublicationError("索引发布失败；旧向量和 Manifest 已恢复。", cause=exc) from exc

    def force_rebuild(
        self, manifest: PipelineManifest, builds: tuple[BuiltDocument, ...], timestamp: str,
    ) -> PipelineManifest:
        if not builds:
            raise IndexPublicationError("force rebuild 没有可发布文档。")
        build_id = f"force-{builds[0].result.build_id}"
        recovery = self.recovery_root / build_id
        if self.pending_journals():
            raise IndexPublicationError("当前 pipeline 存在未完成 recovery journal。")
        recovery.mkdir(parents=True)
        snapshot_path, old_manifest_path = recovery / "snapshot.json", recovery / "old-manifest.bin"
        journal_path = recovery / "journal.json"
        snapshot = self.vector_store.snapshot()
        old_exists = self.runtime.manifest_path.exists()
        old_bytes = self.runtime.manifest_path.read_bytes() if old_exists else b""
        _atomic_bytes(snapshot_path, _json_bytes({"collection_name": self.runtime.collection_name, **snapshot}))
        _atomic_bytes(old_manifest_path, old_bytes)
        journal = RecoveryJournal(
            self.runtime.pipeline_id, self.runtime.collection_name, "force_rebuild", None, build_id,
            "prepared", "vector_replace", hashlib.sha256(old_bytes).hexdigest() if old_exists else None,
            self._relative(old_manifest_path), self._relative(snapshot_path), None,
            timestamp, None, None,
        )
        self._write_journal(journal_path, journal)
        published: list[Path] = []
        try:
            journal = replace(journal, state="mutating")
            self._write_journal(journal_path, journal)
            self.vector_store.recreate_collection()
            for built in builds:
                if built.result.pipeline_id == "v2":
                    self.vector_store.add_v2_build(built.result, built.embeddings,
                                                   self.runtime.build_config_fingerprint)
                else:
                    self.vector_store.add_chunks(built.result.chunks, built.embeddings)
            journal = replace(journal, current_step="vector_verify")
            self._write_journal(journal_path, journal)
            expected_ids = {chunk.chunk_id for built in builds for chunk in built.result.chunks}
            if set(self.vector_store.snapshot()["ids"]) != expected_ids:
                raise VectorStoreError("全量新向量回读 IDs 不一致。")
            journal = replace(journal, current_step="artifact_publish")
            self._write_journal(journal_path, journal)
            records = {}
            for built in builds:
                result, source = built.result, built.result.source
                artifact_path = None
                if result.pipeline_id == "v2":
                    assert result.artifact_stage is not None
                    artifact_document = make_artifact_document_name(
                        source.relative_path, source.document_id
                    )
                    target = (result.artifact_stage.staging_path.parents[2] / "documents" /
                              artifact_document / result.build_id)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(result.artifact_stage.staging_path, target)
                    published.append(target)
                    artifact_path = self._relative(target)
                records[source.document_id] = ManifestDocumentRecord(
                    source.document_id, source.document_name, source.relative_path, source.file_hash,
                    result.build_id, self.runtime.build_config_fingerprint, result.stats.page_count,
                    result.stats.character_count, result.stats.chunk_count, artifact_path, timestamp,
                )
            updated = PipelineManifest(
                manifest.pipeline_id, manifest.collection_name, manifest.build_config,
                manifest.build_config_fingerprint, records, manifest.created_at, timestamp,
            )
            journal = replace(journal, current_step="manifest_publish")
            self._write_journal(journal_path, journal)
            save_pipeline_manifest_atomic(self.runtime.manifest_path, updated, build_id)
            _safe_remove_tree(recovery, self.recovery_root)
            self._cleanup_superseded_artifacts(manifest, updated)
            return updated
        except Exception as exc:
            try:
                journal = replace(journal, state="restoring", current_step="vector_restore")
                self._write_journal(journal_path, journal)
                self.vector_store.restore_snapshot(snapshot, recreate=True)
                journal = replace(journal, current_step="manifest_restore")
                self._write_journal(journal_path, journal)
                if old_exists:
                    _atomic_bytes(self.runtime.manifest_path, old_bytes)
                elif self.runtime.manifest_path.exists():
                    self.runtime.manifest_path.unlink()
                for target in published:
                    if target.exists():
                        _safe_remove_tree(target, target.parents[2])
                if set(self.vector_store.snapshot()["ids"]) != set(snapshot["ids"]):
                    raise IndexPublicationError("全量回滚后的向量不一致。")
                _safe_remove_tree(recovery, self.recovery_root)
            except Exception as restore_exc:
                self._write_journal(journal_path, replace(
                    journal, state="recovery_failed", error_type=type(restore_exc).__name__,
                    error_message=str(restore_exc)))
                raise IndexPublicationError("全量发布及恢复均失败；已保留 recovery 证据。", cause=restore_exc) from exc
            raise IndexPublicationError("全量发布失败；旧 collection 与 manifest 已恢复。", cause=exc) from exc

    def prune_document(
        self, manifest: PipelineManifest, document_id: str, build_id: str, timestamp: str,
    ) -> PipelineManifest:
        record = manifest.documents[document_id]
        recovery = self.recovery_root / build_id
        if self.pending_journals():
            raise IndexPublicationError("当前 pipeline 存在未完成 recovery journal。")
        recovery.mkdir(parents=True)
        snapshot_path, old_manifest_path = recovery / "snapshot.json", recovery / "old-manifest.bin"
        journal_path, quarantine = recovery / "journal.json", recovery / "artifact-quarantine"
        snapshot = self.vector_store.snapshot(document_id)
        old_bytes = self.runtime.manifest_path.read_bytes()
        _atomic_bytes(snapshot_path, _json_bytes({"document_id": document_id, **snapshot}))
        _atomic_bytes(old_manifest_path, old_bytes)
        journal = RecoveryJournal(
            self.runtime.pipeline_id, self.runtime.collection_name, "prune_document", document_id,
            build_id, "prepared", "artifact_cleanup", hashlib.sha256(old_bytes).hexdigest(),
            self._relative(old_manifest_path), self._relative(snapshot_path), self._relative(quarantine),
            timestamp, None, None,
        )
        self._write_journal(journal_path, journal)
        original_artifact_dir = (self.project_root / record.artifact_path).parent if record.artifact_path else None
        moved = False
        try:
            journal = replace(journal, state="mutating")
            self._write_journal(journal_path, journal)
            if original_artifact_dir and original_artifact_dir.exists():
                quarantine.parent.mkdir(parents=True, exist_ok=True)
                os.replace(original_artifact_dir, quarantine)
                moved = True
            journal = replace(journal, current_step="vector_replace")
            self._write_journal(journal_path, journal)
            self.vector_store.delete_document(document_id)
            documents = dict(manifest.documents)
            del documents[document_id]
            updated = replace(manifest, documents=documents, updated_at=timestamp)
            journal = replace(journal, current_step="manifest_publish")
            self._write_journal(journal_path, journal)
            save_pipeline_manifest_atomic(self.runtime.manifest_path, updated, build_id)
            journal_path.unlink()
            _safe_remove_tree(recovery, self.recovery_root)
            return updated
        except Exception as exc:
            try:
                journal = replace(journal, state="restoring", current_step="vector_restore")
                self._write_journal(journal_path, journal)
                self.vector_store.delete_document(document_id)
                self.vector_store.restore_snapshot(snapshot)
                _atomic_bytes(self.runtime.manifest_path, old_bytes)
                if moved and quarantine.exists() and original_artifact_dir is not None:
                    original_artifact_dir.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(quarantine, original_artifact_dir)
                _safe_remove_tree(recovery, self.recovery_root)
            except Exception as restore_exc:
                self._write_journal(journal_path, replace(
                    journal, state="recovery_failed", error_type=type(restore_exc).__name__,
                    error_message=str(restore_exc)))
                raise IndexPublicationError("prune 失败且恢复未完成。", cause=restore_exc) from exc
            raise IndexPublicationError("prune 失败；旧状态已恢复。", cause=exc) from exc
