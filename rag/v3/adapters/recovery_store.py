"""Transaction journals and immutable recovery evidence in an index namespace."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from contextlib import contextmanager
from dataclasses import asdict, fields
from pathlib import Path
from typing import Iterator

from rag.v3.application.assembly import canonical_json_bytes
from rag.v3.adapters.vector_codec import decode_metadata
from rag.v3.contracts.storage import (
    ArtifactSnapshot, ChunkMetadata, IndexIdentity, ManifestSnapshot,
    RecoveryJournal, VectorSnapshot,
)


class RecoveryStoreError(RuntimeError):
    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(detail)


def _strict_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate journal key")
        result[key] = value
    return result


def _decode(raw: bytes, index: IndexIdentity, transaction_id: str) -> RecoveryJournal:
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        if not isinstance(data, dict) or set(data) != {item.name for item in fields(RecoveryJournal)}:
            raise ValueError("journal field set differs")
        identity = data.pop("index_identity")
        if not isinstance(identity, dict) or set(identity) != {
                item.name for item in fields(IndexIdentity)}:
            raise ValueError("journal index identity malformed")
        journal = RecoveryJournal(index_identity=IndexIdentity(**identity), **data)
        if journal.index_identity != index or journal.transaction_id != transaction_id:
            raise ValueError("journal identity differs from its path")
        return journal
    except Exception as exc:
        raise RecoveryStoreError("journal_unreadable", "invalid recovery journal") from exc


def _atomic_write(path: Path, content: bytes, *, initial: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}-{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if initial:
            if path.exists():
                raise RecoveryStoreError("manifest_conflict", "journal already exists")
            os.rename(temporary, path)
        else:
            os.replace(temporary, path)
        if path.read_bytes() != content:
            raise RecoveryStoreError("journal_unreadable", "journal readback differs")
    finally:
        if temporary.exists():
            temporary.unlink()


class LocalRecoveryStore:
    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root.resolve()

    def namespace(self, index: IndexIdentity) -> Path:
        path = (self.workspace_root / index.namespace_path).resolve()
        if not path.is_relative_to(self.workspace_root):
            raise RecoveryStoreError("unsafe_path", "recovery namespace escaped workspace")
        return path

    def path(self, index: IndexIdentity, relative: str) -> Path:
        if (not relative or "\\" in relative or ":" in relative
                or any(part in {"", ".", ".."} for part in relative.split("/"))):
            raise RecoveryStoreError("unsafe_path", "unsafe recovery relative path")
        root = self.namespace(index)
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise RecoveryStoreError("unsafe_path", "recovery path escaped namespace")
        return path

    def _journal_path(self, index: IndexIdentity, transaction_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", transaction_id):
            raise RecoveryStoreError("unsafe_path", "invalid transaction ID")
        return self.path(index, f"recovery/{transaction_id}/journal.json")

    def raw(self, index: IndexIdentity, transaction_id: str) -> tuple[bytes | None, str | None]:
        path = self._journal_path(index, transaction_id)
        try:
            if not path.exists():
                return None, None
            raw = path.read_bytes()
            _decode(raw, index, transaction_id)
            return raw, hashlib.sha256(raw).hexdigest()
        except RecoveryStoreError:
            raise
        except OSError as exc:
            raise RecoveryStoreError("journal_unreadable", "cannot read recovery journal") from exc

    def read(self, index: IndexIdentity, transaction_id: str) -> RecoveryJournal | None:
        raw, _ = self.raw(index, transaction_id)
        return _decode(raw, index, transaction_id) if raw is not None else None

    def list_pending(self, index: IndexIdentity) -> tuple[RecoveryJournal, ...]:
        directory = self.path(index, "recovery")
        if not directory.exists():
            return ()
        journals: list[RecoveryJournal] = []
        try:
            for child in directory.iterdir():
                if not child.is_dir():
                    raise RecoveryStoreError("journal_unreadable", "unexpected recovery entry")
                journal = self.read(index, child.name)
                if journal is not None:
                    journals.append(journal)
        except RecoveryStoreError:
            raise
        except OSError as exc:
            raise RecoveryStoreError("journal_unreadable", "cannot enumerate recovery journal") from exc
        return tuple(sorted(journals, key=lambda item: (item.created_at, item.transaction_id)))

    def prepare(self, index: IndexIdentity, journal: RecoveryJournal) -> str:
        if journal.index_identity != index:
            raise RecoveryStoreError("journal_unreadable", "journal index mismatch")
        path = self._journal_path(index, journal.transaction_id)
        content = canonical_json_bytes(asdict(journal))
        _decode(content, index, journal.transaction_id)
        try:
            _atomic_write(path, content, initial=True)
        except RecoveryStoreError:
            raise
        except OSError as exc:
            raise RecoveryStoreError("journal_unreadable", "journal prepare failed") from exc
        return hashlib.sha256(content).hexdigest()

    def update(self, index: IndexIdentity, journal: RecoveryJournal,
               expected_old_sha256: str) -> str:
        _, current_sha = self.raw(index, journal.transaction_id)
        if current_sha != expected_old_sha256:
            raise RecoveryStoreError("manifest_conflict", "journal changed")
        content = canonical_json_bytes(asdict(journal))
        _decode(content, index, journal.transaction_id)
        try:
            _atomic_write(self._journal_path(index, journal.transaction_id), content,
                          initial=False)
        except RecoveryStoreError:
            raise
        except OSError as exc:
            raise RecoveryStoreError("journal_unreadable", "journal update failed") from exc
        return hashlib.sha256(content).hexdigest()

    def clear(self, index: IndexIdentity, transaction_id: str,
              expected_sha256: str) -> None:
        _, current_sha = self.raw(index, transaction_id)
        if current_sha != expected_sha256:
            raise RecoveryStoreError("manifest_conflict", "journal changed before clear")
        try:
            self._journal_path(index, transaction_id).unlink()
        except OSError as exc:
            raise RecoveryStoreError("journal_unreadable", "journal clear failed") from exc

    def cleanup(self, index: IndexIdentity, transaction_id: str) -> None:
        """Remove only this transaction's evidence after its journal is cleared."""
        journal_path = self._journal_path(index, transaction_id)
        if journal_path.exists():
            raise RecoveryStoreError("manifest_conflict", "cannot clean pending recovery evidence")
        directory = journal_path.parent
        if directory.exists():
            try:
                shutil.rmtree(directory)
            except OSError as exc:
                raise RecoveryStoreError("snapshot_unreadable", "recovery cleanup failed") from exc

    def write_evidence(self, index: IndexIdentity, transaction_id: str,
                       filename: str, content: bytes) -> tuple[str, str]:
        if filename not in {"old-manifest.json", "old-vector.json", "old-artifacts.json"}:
            raise RecoveryStoreError("unsafe_path", "unknown recovery evidence role")
        path = self._journal_path(index, transaction_id).with_name(filename)
        if path.exists():
            raise RecoveryStoreError("manifest_conflict", "recovery evidence already exists")
        try:
            _atomic_write(path, content, initial=True)
        except RecoveryStoreError:
            raise
        except OSError as exc:
            raise RecoveryStoreError("snapshot_unreadable", "evidence write failed") from exc
        return (f"recovery/{transaction_id}/{filename}", hashlib.sha256(content).hexdigest())

    def read_evidence(self, index: IndexIdentity, transaction_id: str,
                      filename: str, expected_sha256: str) -> bytes:
        if filename not in {"old-manifest.json", "old-vector.json", "old-artifacts.json"}:
            raise RecoveryStoreError("unsafe_path", "unknown recovery evidence role")
        path = self._journal_path(index, transaction_id).with_name(filename)
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise RecoveryStoreError("snapshot_unreadable", "evidence missing") from exc
        if hashlib.sha256(content).hexdigest() != expected_sha256:
            raise RecoveryStoreError("snapshot_unreadable", "evidence SHA-256 differs")
        return content

    def save_manifest_snapshot(self, index: IndexIdentity, transaction_id: str,
                               old_bytes: bytes | None) -> ManifestSnapshot:
        payload = {
            "existed": old_bytes is not None,
            "bytes_sha256": hashlib.sha256(old_bytes).hexdigest() if old_bytes is not None else None,
            "raw_hex": old_bytes.hex() if old_bytes is not None else None,
        }
        relative, _ = self.write_evidence(index, transaction_id, "old-manifest.json",
                                          canonical_json_bytes(payload))
        snapshot = ManifestSnapshot(old_bytes is not None, payload["bytes_sha256"], relative)
        self.read_manifest_snapshot(index, transaction_id)
        return snapshot

    def read_manifest_snapshot(self, index: IndexIdentity,
                               transaction_id: str) -> tuple[ManifestSnapshot, bytes | None]:
        path = self._journal_path(index, transaction_id).with_name("old-manifest.json")
        try:
            data = json.loads(path.read_bytes().decode("utf-8"), object_pairs_hook=_strict_pairs)
            if set(data) != {"existed", "bytes_sha256", "raw_hex"}:
                raise ValueError("manifest snapshot shape invalid")
            old_bytes = bytes.fromhex(data["raw_hex"]) if data["existed"] else None
            if data["existed"] != (data["raw_hex"] is not None):
                raise ValueError("manifest snapshot existence differs")
            digest = hashlib.sha256(old_bytes).hexdigest() if old_bytes is not None else None
            if digest != data["bytes_sha256"]:
                raise ValueError("manifest snapshot hash differs")
            snapshot = ManifestSnapshot(data["existed"], digest,
                                        f"recovery/{transaction_id}/old-manifest.json")
            return snapshot, old_bytes
        except Exception as exc:
            raise RecoveryStoreError("snapshot_unreadable", "old manifest snapshot invalid") from exc

    def save_vector_snapshot(self, index: IndexIdentity, transaction_id: str,
                             snapshot: VectorSnapshot) -> str:
        if snapshot.index_identity != index:
            raise RecoveryStoreError("snapshot_unreadable", "vector snapshot identity differs")
        relative, _ = self.write_evidence(index, transaction_id, "old-vector.json",
                                          canonical_json_bytes(asdict(snapshot)))
        if self.read_vector_snapshot(index, transaction_id) != snapshot:
            raise RecoveryStoreError("snapshot_unreadable", "vector snapshot readback differs")
        return relative

    def read_vector_snapshot(self, index: IndexIdentity,
                             transaction_id: str) -> VectorSnapshot:
        path = self._journal_path(index, transaction_id).with_name("old-vector.json")
        try:
            data = json.loads(path.read_bytes().decode("utf-8"), object_pairs_hook=_strict_pairs)
            if not isinstance(data, dict) or set(data) != {
                    item.name for item in fields(VectorSnapshot)}:
                raise ValueError("vector snapshot shape invalid")
            raw_identity = data["index_identity"]
            if not isinstance(raw_identity, dict) or set(raw_identity) != {
                    item.name for item in fields(IndexIdentity)}:
                raise ValueError("vector snapshot index invalid")
            metadatas = []
            for raw in data["metadatas"]:
                if not isinstance(raw, dict) or set(raw) != {
                        item.name for item in fields(ChunkMetadata)}:
                    raise ValueError("vector metadata shape invalid")
                encoded = {
                    "schema_version": "vector_metadata_v3",
                    "document_id": raw["document_id"],
                    "document_name": raw["document_name"],
                    "relative_path": raw["relative_path"],
                    "file_hash": raw["file_hash"],
                    "build_id": raw["build_id"],
                    "build_fingerprint": raw["build_fingerprint"],
                    "text_sha256": raw["text_sha256"],
                    "chunk_index": raw["chunk_index"],
                    "chunk_kind": raw["kind"],
                    "token_count_or_minus_one": raw["token_count"] if raw["token_count"] is not None else -1,
                    "parent_unit_id_or_empty": raw["parent_unit_id"] or "",
                    "fragment_index": raw["fragment_index"],
                    "fragment_count": raw["fragment_count"],
                    "page_numbers_json": canonical_json_bytes(raw["page_numbers"]).decode("utf-8"),
                    "sources_json": canonical_json_bytes(raw["sources"]).decode("utf-8"),
                }
                metadatas.append(decode_metadata(encoded))
            snapshot = VectorSnapshot(
                data["schema_version"], IndexIdentity(**raw_identity),
                data["collection_existed"], tuple(data["document_scope"]),
                tuple(data["ids"]), tuple(data["documents"]),
                tuple(tuple(vector) for vector in data["embeddings"]),
                tuple(metadatas), data["sha256"],
            )
            value = asdict(snapshot)
            digest = value.pop("sha256")
            if (snapshot.index_identity != index or
                    hashlib.sha256(canonical_json_bytes(value)).hexdigest() != digest):
                raise ValueError("vector snapshot digest or identity differs")
            return snapshot
        except Exception as exc:
            raise RecoveryStoreError("snapshot_unreadable", "old vector snapshot invalid") from exc

    def save_artifact_snapshots(self, index: IndexIdentity, transaction_id: str,
                                snapshots: tuple[ArtifactSnapshot, ...]) -> str:
        relative, _ = self.write_evidence(index, transaction_id, "old-artifacts.json",
                                          canonical_json_bytes([asdict(item) for item in snapshots]))
        if self.read_artifact_snapshots(index, transaction_id) != snapshots:
            raise RecoveryStoreError("snapshot_unreadable", "artifact snapshot readback differs")
        return relative

    def read_artifact_snapshots(self, index: IndexIdentity,
                                transaction_id: str) -> tuple[ArtifactSnapshot, ...]:
        path = self._journal_path(index, transaction_id).with_name("old-artifacts.json")
        try:
            data = json.loads(path.read_bytes().decode("utf-8"), object_pairs_hook=_strict_pairs)
            snapshots = tuple(ArtifactSnapshot(**item) for item in data)
            for snapshot in snapshots:
                if snapshot.transaction_id != transaction_id:
                    raise ValueError("artifact snapshot transaction differs")
                value = asdict(snapshot)
                digest = value.pop("sha256")
                if hashlib.sha256(canonical_json_bytes(value)).hexdigest() != digest:
                    raise ValueError("artifact snapshot digest differs")
            return snapshots
        except Exception as exc:
            raise RecoveryStoreError("snapshot_unreadable", "old artifact snapshots invalid") from exc

    @contextmanager
    def write_lock(self, index: IndexIdentity) -> Iterator[None]:
        namespace = self.namespace(index)
        namespace.mkdir(parents=True, exist_ok=True)
        path = namespace / "index.lock"
        try:
            with path.open("xb") as stream:
                stream.write(b"\0")
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            pass
        try:
            with path.open("r+b") as stream:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.lockf(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB, 1)
                try:
                    yield
                finally:
                    stream.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.lockf(stream.fileno(), fcntl.LOCK_UN, 1)
        except (OSError, BlockingIOError) as exc:
            raise RecoveryStoreError("lock_unavailable", "index write lock unavailable") from exc
