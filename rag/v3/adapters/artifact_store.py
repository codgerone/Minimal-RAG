"""Immutable build directories and scoped artifact recovery evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import asdict, replace
from pathlib import Path

from rag.v3.application.artifacts import ArtifactError, verify_staged_artifacts
from rag.v3.application.artifact_paths import (
    build_artifact_path, document_directory, valid_artifact_paths,
)
from rag.v3.application.assembly import canonical_json_bytes
from rag.v3.contracts.artifacts import ArtifactFileRef, StagedArtifacts
from rag.v3.contracts.storage import (
    ArtifactSnapshot, IndexIdentity, ManifestDocumentRecord,
)


class ArtifactStoreError(RuntimeError):
    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(detail)


def _snapshot_digest(snapshot: ArtifactSnapshot) -> str:
    value = asdict(snapshot)
    del value["sha256"]
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


class LocalArtifactStore:
    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root.resolve()

    def namespace(self, index: IndexIdentity) -> Path:
        path = (self.workspace_root / index.namespace_path).resolve()
        if not path.is_relative_to(self.workspace_root):
            raise ArtifactStoreError("unsafe_path", "artifact namespace escaped workspace")
        return path

    def _path(self, index: IndexIdentity, relative_path: str) -> Path:
        parts = relative_path.split("/")
        if (not relative_path or any(part in {"", ".", ".."} for part in parts)
                or "\\" in relative_path or ":" in relative_path):
            raise ArtifactStoreError("unsafe_path", "unsafe artifact relative path")
        root = self.namespace(index)
        path = (root / relative_path).resolve()
        if not path.is_relative_to(root):
            raise ArtifactStoreError("unsafe_path", "artifact path escaped namespace")
        return path

    @staticmethod
    def _directory_files(directory: Path) -> dict[str, str]:
        if not directory.is_dir() or directory.is_symlink():
            raise ArtifactStoreError("artifact_failed", "artifact directory is not a real directory")
        files: dict[str, str] = {}
        for item in directory.iterdir():
            if not item.is_file() or item.is_symlink():
                raise ArtifactStoreError("artifact_failed", "artifact directory has unsafe entry")
            files[item.name] = hashlib.sha256(item.read_bytes()).hexdigest()
        return files

    def _verify_build(self, root: Path, index: IndexIdentity,
                      record: ManifestDocumentRecord) -> None:
        manifest_path = root / "snapshot-manifest.json"
        try:
            manifest_bytes = manifest_path.read_bytes()
            if hashlib.sha256(manifest_bytes).hexdigest() != record.artifact_files["snapshot_manifest"]:
                raise ValueError("artifact snapshot manifest hash differs")
            manifest = json.loads(manifest_bytes)
            branch = bool(manifest["attached_slots"])
            refs = tuple(ArtifactFileRef(**item) for item in manifest["files"])
            staged = StagedArtifacts(root, record.document_id, record.build_id,
                                     record.file_hash, index.build_fingerprint,
                                     record.artifact_files["snapshot_manifest"], refs)
            verify_staged_artifacts(staged, branch=branch)
            actual = {item.role: item.sha256 for item in refs}
            actual["snapshot_manifest"] = staged.snapshot_manifest_sha256
            if actual != record.artifact_files:
                raise ValueError("active manifest artifact file map differs")
            expected_names = {item.relative_path for item in refs} | {"snapshot-manifest.json"}
            if {item.name for item in root.iterdir()} != expected_names:
                raise ValueError("artifact build contains unexpected or missing files")
        except (ArtifactError, KeyError, OSError, ValueError, TypeError) as exc:
            raise ArtifactStoreError("artifact_failed", "active artifact verification failed") from exc

    def verify_active(self, index: IndexIdentity,
                      record: ManifestDocumentRecord) -> None:
        if record.artifact_path not in valid_artifact_paths(record):
            raise ArtifactStoreError("artifact_failed", "artifact path differs from document identity")
        self._verify_build(self._path(index, record.artifact_path), index, record)

    def read_active_role(self, index: IndexIdentity,
                         record: ManifestDocumentRecord, role: str) -> bytes:
        self.verify_active(index, record)
        root = self._path(index, record.artifact_path)
        manifest = json.loads((root / "snapshot-manifest.json").read_bytes())
        matches = [item["relative_path"] for item in manifest["files"]
                   if item["role"] == role]
        if len(matches) != 1:
            raise ArtifactStoreError("artifact_failed", "active artifact role absent or ambiguous")
        path = self._path(index, f"{record.artifact_path}/{matches[0]}")
        if path.parent != root:
            raise ArtifactStoreError("unsafe_path", "active role escaped build directory")
        return path.read_bytes()

    def stage(self, index: IndexIdentity, transaction_id: str,
              staged: StagedArtifacts) -> StagedArtifacts:
        expected = self._path(index, f"staging/{transaction_id}/{staged.document_id}")
        if staged.staging_path.resolve() != expected:
            raise ArtifactStoreError("unsafe_path", "stage belongs to another transaction")
        self.verify(staged)
        return staged

    def verify(self, staged: StagedArtifacts) -> StagedArtifacts:
        manifest = json.loads((staged.staging_path / "snapshot-manifest.json").read_bytes())
        verify_staged_artifacts(staged, branch=bool(manifest["attached_slots"]))
        return staged

    def publish(self, index: IndexIdentity, staged: StagedArtifacts,
                record: ManifestDocumentRecord) -> str:
        self.verify(staged)
        if (staged.document_id, staged.build_id, staged.file_hash,
                staged.build_fingerprint) != (record.document_id, record.build_id,
                                              record.file_hash, index.build_fingerprint):
            raise ArtifactStoreError("publication_failed", "staged artifact identity differs")
        target = self._path(index, record.artifact_path)
        if record.artifact_path not in valid_artifact_paths(record):
            raise ArtifactStoreError("unsafe_path", "target artifact path invalid")
        if target.exists():
            try:
                self.verify_active(index, record)
                return record.artifact_path
            except ArtifactStoreError:
                if not target.is_dir() or target.is_symlink():
                    raise ArtifactStoreError("unsafe_path", "existing artifact target is unsafe")
                shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(staged.staging_path, target)
            self.verify_active(index, record)
            return record.artifact_path
        except Exception as exc:
            raise ArtifactStoreError("publication_failed", "artifact build publish failed") from exc

    def snapshot(self, index: IndexIdentity, transaction_id: str,
                 record: ManifestDocumentRecord | None) -> ArtifactSnapshot:
        if record is None:
            provisional = ArtifactSnapshot("artifact_snapshot_v3", transaction_id,
                                           "", "", False, None, None, {}, "0" * 64)
            return ArtifactSnapshot("artifact_snapshot_v3", transaction_id,
                                    "", "", False, None, None, {},
                                    _snapshot_digest(provisional))
        source = self._path(index, record.artifact_path)
        if record.artifact_path not in valid_artifact_paths(record):
            raise ArtifactStoreError("unsafe_path", "old artifact path invalid")
        if not source.exists():
            provisional = ArtifactSnapshot("artifact_snapshot_v3", transaction_id,
                record.document_id, record.build_id, False, record.artifact_path,
                None, {}, "0" * 64)
            return replace(provisional, sha256=_snapshot_digest(provisional))
        files = self._directory_files(source)
        relative = f"recovery/{transaction_id}/artifacts/{record.document_id}/{record.build_id}"
        destination = self._path(index, relative)
        if destination.exists():
            raise ArtifactStoreError("artifact_failed", "recovery artifact snapshot already exists")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination)
        if self._directory_files(destination) != files:
            raise ArtifactStoreError("artifact_failed", "artifact recovery copy differs")
        provisional = ArtifactSnapshot("artifact_snapshot_v3", transaction_id,
                                       record.document_id, record.build_id, True,
                                       record.artifact_path, relative,
                                       files, "0" * 64)
        return ArtifactSnapshot("artifact_snapshot_v3", transaction_id,
                                record.document_id, record.build_id, True,
                                record.artifact_path, relative,
                                files, _snapshot_digest(provisional))

    def quarantine(self, index: IndexIdentity, transaction_id: str,
                   record: ManifestDocumentRecord) -> ArtifactSnapshot:
        source = self._path(index, record.artifact_path)
        if not source.exists():
            return self.snapshot(index, transaction_id, record)
        files = self._directory_files(source)
        relative = f"recovery/{transaction_id}/quarantine/{record.document_id}/{record.build_id}"
        destination = self._path(index, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise ArtifactStoreError("publication_failed", "quarantine target exists")
        os.replace(source, destination)
        if self._directory_files(destination) != files:
            raise ArtifactStoreError("publication_failed", "quarantined artifact bytes differ")
        provisional = ArtifactSnapshot("artifact_snapshot_v3", transaction_id,
                                       record.document_id, record.build_id, True,
                                       record.artifact_path, relative,
                                       files, "0" * 64)
        return ArtifactSnapshot("artifact_snapshot_v3", transaction_id,
                                record.document_id, record.build_id, True,
                                record.artifact_path, relative,
                                files, _snapshot_digest(provisional))

    def _snapshot_target(self, index: IndexIdentity, snapshot: ArtifactSnapshot,
                         record: ManifestDocumentRecord | None) -> Path | None:
        if snapshot.active_path is None:
            if record is not None:
                raise ArtifactStoreError("artifact_restore_failed", "snapshot target is absent")
            return None
        if record is not None:
            if (snapshot.document_id != record.document_id
                    or snapshot.build_id != record.build_id
                    or snapshot.active_path != record.artifact_path
                    or record.artifact_path not in valid_artifact_paths(record)):
                raise ArtifactStoreError("artifact_restore_failed", "snapshot record identity differs")
        else:
            parts = snapshot.active_path.split("/")
            if (len(parts) != 4 or parts[:2] != ["artifacts", "documents"]
                    or not parts[2].endswith("--" + snapshot.document_id)
                    or parts[3] != snapshot.build_id):
                raise ArtifactStoreError("unsafe_path", "snapshot target identity is unsafe")
        return self._path(index, snapshot.active_path)

    def verify_snapshot(self, index: IndexIdentity, snapshot: ArtifactSnapshot,
                        record: ManifestDocumentRecord | None) -> None:
        if _snapshot_digest(snapshot) != snapshot.sha256:
            raise ArtifactStoreError("artifact_restore_failed", "artifact snapshot digest invalid")
        active = self._snapshot_target(index, snapshot, record)
        if active is None and not snapshot.existed:
            return
        if not snapshot.existed:
            if active.exists():
                raise ArtifactStoreError("artifact_restore_failed", "old artifact was absent")
        elif self._directory_files(active) != snapshot.files:
            raise ArtifactStoreError("artifact_restore_failed", "old artifact bytes differ")

    def restore(self, index: IndexIdentity, snapshot: ArtifactSnapshot,
                record: ManifestDocumentRecord | None) -> None:
        if _snapshot_digest(snapshot) != snapshot.sha256:
            raise ArtifactStoreError("artifact_restore_failed", "artifact snapshot digest invalid")
        active = self._snapshot_target(index, snapshot, record)
        if not snapshot.existed:
            if active is not None and active.exists():
                if active.is_dir() and not active.is_symlink():
                    shutil.rmtree(active)
                else:
                    raise ArtifactStoreError("unsafe_path", "artifact target is not a real directory")
            self.verify_snapshot(index, snapshot, record)
            return
        saved = self._path(index, snapshot.snapshot_path)
        if not saved.exists():
            raise ArtifactStoreError("artifact_restore_failed", "old artifact snapshot missing")
        if self._directory_files(saved) != snapshot.files:
            raise ArtifactStoreError("artifact_restore_failed", "old artifact snapshot bytes differ")
        try:
            self.verify_snapshot(index, snapshot, record)
            return
        except ArtifactStoreError:
            pass
        if active.exists():
            if active.is_dir():
                shutil.rmtree(active)
            else:
                active.unlink()
        active.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(saved, active)
        self.verify_snapshot(index, snapshot, record)

    def cleanup_staging(self, index: IndexIdentity, transaction_id: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", transaction_id):
            raise ArtifactStoreError("unsafe_path", "invalid staging transaction ID")
        root = self._path(index, f"staging/{transaction_id}")
        if root.exists():
            try:
                shutil.rmtree(root)
            except OSError as exc:
                raise ArtifactStoreError("artifact_failed", "staging cleanup failed") from exc

    def cleanup_obsolete(self, index: IndexIdentity,
                         active_records: tuple[ManifestDocumentRecord, ...]) -> None:
        """Keep one verified active build per document after a committed publication."""
        root = self._path(index, "artifacts/documents")
        if not root.exists():
            if active_records:
                raise ArtifactStoreError("artifact_failed", "active artifact root is missing")
            return
        if not root.is_dir() or root.is_symlink():
            raise ArtifactStoreError("unsafe_path", "artifact root is not a real directory")
        active: set[Path] = set()
        ids: set[str] = set()
        for record in active_records:
            if record.document_id in ids or record.artifact_path not in valid_artifact_paths(record):
                raise ArtifactStoreError("unsafe_path", "active artifact identity is invalid")
            ids.add(record.document_id)
            self.verify_active(index, record)
            target = self._path(index, record.artifact_path)
            if not target.is_relative_to(root):
                raise ArtifactStoreError("unsafe_path", "active artifact escaped document root")
            active.add(target)
        for document_dir in sorted(root.iterdir()):
            if (document_dir.is_symlink() or not document_dir.is_dir()
                    or not document_dir.resolve().is_relative_to(root)
                    or not re.search(r"--[0-9a-f]{16}$", document_dir.name)):
                raise ArtifactStoreError("unsafe_path", "unsafe document artifact directory")
            for build_dir in sorted(document_dir.iterdir()):
                if (build_dir.is_symlink() or not build_dir.is_dir()
                        or not build_dir.resolve().is_relative_to(root)
                        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", build_dir.name)):
                    raise ArtifactStoreError("unsafe_path", "unsafe build artifact directory")
                if build_dir in active:
                    continue
                if any(child.is_symlink() or not child.is_file() for child in build_dir.iterdir()):
                    raise ArtifactStoreError("unsafe_path", "obsolete build has unsafe contents")
                try:
                    shutil.rmtree(build_dir)
                except OSError as exc:
                    raise ArtifactStoreError("artifact_failed", "obsolete build cleanup failed") from exc
            if not any(document_dir.iterdir()):
                try:
                    document_dir.rmdir()
                except OSError as exc:
                    raise ArtifactStoreError("artifact_failed", "empty document cleanup failed") from exc
