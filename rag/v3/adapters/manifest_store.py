"""Strict V3 manifest load and compare-and-replace persistence."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import asdict, fields
from pathlib import Path

from rag.v3.application.assembly import canonical_json_bytes, readable_json_bytes
from rag.v3.contracts.assembly import BuildProjection, BuildProjectionBinding
from rag.v3.contracts.retrieval import EmbeddingIdentity
from rag.v3.contracts.storage import IndexIdentity, Manifest, ManifestDocumentRecord


class ManifestStoreError(RuntimeError):
    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(detail)


def _strict_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _shape(cls, value: object) -> dict[str, object]:
    expected = {field.name for field in fields(cls)}
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"{cls.__name__} field set invalid")
    return value


def decode_manifest(raw: bytes, expected: IndexIdentity) -> Manifest:
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        data = _shape(Manifest, data)
        identity = IndexIdentity(**_shape(IndexIdentity, data["index_identity"]))
        if identity != expected:
            raise ValueError("manifest index identity differs from requested namespace")
        projection_data = _shape(BuildProjection, data["build_configuration"])
        binding_data = projection_data["bindings"]
        if not isinstance(binding_data, list):
            raise ValueError("projection bindings must be an array")
        projection = BuildProjection(
            **{key: value for key, value in projection_data.items() if key != "bindings"},
            bindings=tuple(BuildProjectionBinding(**_shape(BuildProjectionBinding, value))
                           for value in binding_data),
        )
        if hashlib.sha256(canonical_json_bytes(asdict(projection))).hexdigest() != identity.build_fingerprint:
            raise ValueError("manifest build projection fingerprint mismatch")
        embedding = EmbeddingIdentity(**_shape(EmbeddingIdentity, data["embedding_identity"]))
        documents_data = data["documents"]
        if not isinstance(documents_data, dict):
            raise ValueError("manifest documents must be a map")
        documents = {key: ManifestDocumentRecord(**_shape(ManifestDocumentRecord, value))
                     for key, value in documents_data.items()}
        return Manifest(data["schema_version"], identity, projection, embedding,
                        documents, data["created_at"], data["updated_at"])
    except Exception as exc:
        raise ManifestStoreError("manifest_failed", "invalid V3 manifest") from exc


class LocalManifestStore:
    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root.resolve()

    def _path(self, index: IndexIdentity) -> Path:
        namespace = (self.workspace_root / index.namespace_path).resolve()
        if not namespace.is_relative_to(self.workspace_root):
            raise ManifestStoreError("unsafe_path", "manifest namespace escaped workspace")
        return namespace / "manifest.json"

    @staticmethod
    def decode(raw: bytes, index: IndexIdentity) -> Manifest:
        return decode_manifest(raw, index)

    def byte_digest(self, index: IndexIdentity) -> str | None:
        return self.raw_unvalidated(index)[1]

    def raw_unvalidated(self, index: IndexIdentity) -> tuple[bytes | None, str | None]:
        path = self._path(index)
        if not path.exists():
            return None, None
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ManifestStoreError("manifest_failed", "cannot read manifest bytes") from exc
        return data, hashlib.sha256(data).hexdigest()

    def raw(self, index: IndexIdentity) -> tuple[bytes | None, str | None]:
        data, digest = self.raw_unvalidated(index)
        if data is not None:
            decode_manifest(data, index)
        return data, digest

    def load(self, index: IndexIdentity) -> Manifest | None:
        data, _ = self.raw(index)
        return decode_manifest(data, index) if data is not None else None

    def save_atomic(self, index: IndexIdentity, manifest: Manifest,
                    expected_old_sha256: str | None) -> str:
        if manifest.index_identity != index:
            raise ManifestStoreError("manifest_failed", "manifest identity differs from target")
        path = self._path(index)
        current, current_sha = self.raw_unvalidated(index)
        if current_sha != expected_old_sha256:
            raise ManifestStoreError("manifest_conflict", "active manifest changed")
        content = readable_json_bytes(asdict(manifest))
        decode_manifest(content, index)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".manifest-{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            if path.read_bytes() != content:
                raise ManifestStoreError("manifest_failed", "manifest readback differs")
            return hashlib.sha256(content).hexdigest()
        except ManifestStoreError:
            raise
        except OSError as exc:
            raise ManifestStoreError("manifest_failed", "manifest atomic replacement failed") from exc
        finally:
            if temporary.exists():
                temporary.unlink()

    def restore_bytes(self, index: IndexIdentity, old_bytes: bytes | None,
                      expected_current_sha256: str | None) -> None:
        """Restore exact old bytes, including proof that no manifest existed."""
        path = self._path(index)
        current_sha = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        if current_sha != expected_current_sha256:
            raise ManifestStoreError("manifest_conflict", "manifest changed before restore")
        if old_bytes is None:
            if path.exists():
                path.unlink()
            if path.exists():
                raise ManifestStoreError("manifest_failed", "manifest removal readback failed")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".manifest-restore-{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(old_bytes)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            if path.read_bytes() != old_bytes:
                raise ManifestStoreError("manifest_failed", "restored manifest bytes differ")
        finally:
            if temporary.exists():
                temporary.unlink()
