"""Reject mixed manifest/vector views during concurrent publication."""

from __future__ import annotations

from typing import Protocol

from rag.v3.contracts.storage import IndexIdentity


class ReadGateError(RuntimeError):
    pass


class ManifestDigestPort(Protocol):
    def raw(self, index: IndexIdentity) -> tuple[bytes | None, str | None]: ...


class RecoveryPendingPort(Protocol):
    def list_pending(self, index: IndexIdentity) -> tuple[object, ...]: ...


class ManifestReadGate:
    def __init__(self, manifests: ManifestDigestPort, recovery: RecoveryPendingPort):
        self.manifests = manifests
        self.recovery = recovery

    def begin(self, index: IndexIdentity) -> str:
        if self.recovery.list_pending(index):
            raise ReadGateError("index has pending recovery")
        _, digest = self.manifests.raw(index)
        if digest is None:
            raise ReadGateError("index has no active manifest")
        return digest

    def finish(self, index: IndexIdentity, original_digest: str) -> None:
        if self.recovery.list_pending(index):
            raise ReadGateError("index changed during read")
        _, digest = self.manifests.raw(index)
        if digest != original_digest:
            raise ReadGateError("index changed during read")
