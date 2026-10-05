from dataclasses import replace
from pathlib import Path
import hashlib
from dataclasses import asdict

import pytest

from rag.v3.adapters.recovery_store import LocalRecoveryStore, RecoveryStoreError
from rag.v3.application.assembly import builtin_configuration, index_identity
from rag.v3.application.assembly import canonical_json_bytes
from rag.v3.contracts.storage import RecoveryJournal, VectorSnapshot


def _journal(index, transaction_id: str) -> RecoveryJournal:
    return RecoveryJournal(
        "index_recovery_v3", transaction_id, index, "replace_document", "doc_a",
        "prepared", "none", None,
        f"recovery/{transaction_id}/old-manifest.json",
        f"recovery/{transaction_id}/old-vector.json", None,
        f"staging/{transaction_id}", "1" * 64,
        "2026-09-25T00:00:00+00:00", None, None,
    )


def test_journal_compare_replace_list_and_corruption(tmp_path: Path) -> None:
    index = index_identity(builtin_configuration("plain_text"))
    store = LocalRecoveryStore(tmp_path)
    first = _journal(index, "tx_b")
    second = _journal(index, "tx_a")
    assert store.list_pending(index) == ()
    first_sha = store.prepare(index, first)
    store.prepare(index, second)
    assert tuple(item.transaction_id for item in store.list_pending(index)) == ("tx_a", "tx_b")
    updated = replace(first, state="mutating", current_step="vector_replace")
    with pytest.raises(RecoveryStoreError) as conflict:
        store.update(index, updated, "0" * 64)
    assert conflict.value.code == "manifest_conflict"
    updated_sha = store.update(index, updated, first_sha)
    assert store.read(index, "tx_b") == updated
    store.clear(index, "tx_b", updated_sha)
    assert tuple(item.transaction_id for item in store.list_pending(index)) == ("tx_a",)
    path = tmp_path / index.namespace_path / "recovery/tx_a/journal.json"
    path.write_text('{"schema_version":"index_recovery_v3"}', encoding="utf-8")
    with pytest.raises(RecoveryStoreError) as invalid:
        store.list_pending(index)
    assert invalid.value.code == "journal_unreadable"


def test_write_lock_competes_and_evidence_is_verified(tmp_path: Path) -> None:
    index = index_identity(builtin_configuration("plain_text"))
    store = LocalRecoveryStore(tmp_path)
    with store.write_lock(index):
        with pytest.raises(RecoveryStoreError) as competing:
            with LocalRecoveryStore(tmp_path).write_lock(index):
                pass
        assert competing.value.code == "lock_unavailable"
    with store.write_lock(index):
        relative, digest = store.write_evidence(index, "tx_e", "old-manifest.json", b"old")
        assert relative == "recovery/tx_e/old-manifest.json"
        assert store.read_evidence(index, "tx_e", "old-manifest.json", digest) == b"old"
        with pytest.raises(RecoveryStoreError) as damage:
            store.read_evidence(index, "tx_e", "old-manifest.json", "0" * 64)
        assert damage.value.code == "snapshot_unreadable"


def test_typed_recovery_snapshots_distinguish_absent_from_empty(tmp_path: Path) -> None:
    index = index_identity(builtin_configuration("plain_text"))
    store = LocalRecoveryStore(tmp_path)
    manifest = store.save_manifest_snapshot(index, "tx_s", None)
    assert not manifest.existed
    assert store.read_manifest_snapshot(index, "tx_s") == (manifest, None)
    vector = VectorSnapshot("vector_snapshot_v3", index, False, (), (), (), (), (), "0" * 64)
    data = asdict(vector)
    data.pop("sha256")
    vector = replace(vector, sha256=hashlib.sha256(canonical_json_bytes(data)).hexdigest())
    store.save_vector_snapshot(index, "tx_s", vector)
    assert store.read_vector_snapshot(index, "tx_s") == vector
    store.save_artifact_snapshots(index, "tx_s", ())
    assert store.read_artifact_snapshots(index, "tx_s") == ()
    path = tmp_path / index.namespace_path / "recovery/tx_s/old-vector.json"
    path.write_bytes(path.read_bytes().replace(b'"collection_existed":false',
                                               b'"collection_existed":true'))
    with pytest.raises(RecoveryStoreError) as damage:
        store.read_vector_snapshot(index, "tx_s")
    assert damage.value.code == "snapshot_unreadable"
