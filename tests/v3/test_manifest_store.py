import hashlib
from datetime import datetime, timezone

import pytest

from rag.v3.adapters.e5 import default_identity
from rag.v3.adapters.manifest_store import LocalManifestStore, ManifestStoreError
from rag.v3.application.assembly import build_projection, builtin_configuration, index_identity
from rag.v3.contracts.storage import Manifest


def test_manifest_atomic_compare_and_strict_readback(tmp_path) -> None:
    configuration = builtin_configuration("plain_text")
    identity = index_identity(configuration)
    timestamp = datetime.now(timezone.utc).isoformat()
    manifest = Manifest("index_manifest_v3", identity, build_projection(configuration),
                        default_identity(), {}, timestamp, timestamp)
    store = LocalManifestStore(tmp_path)
    assert store.load(identity) is None
    sha = store.save_atomic(identity, manifest, None)
    assert store.load(identity) == manifest
    path = tmp_path / identity.namespace_path / "manifest.json"
    saved = path.read_bytes()
    assert saved.startswith(b"{\n  ") and saved.endswith(b"\n")
    assert sha == hashlib.sha256(saved).hexdigest()
    with pytest.raises(ManifestStoreError) as conflict:
        store.save_atomic(identity, manifest, None)
    assert conflict.value.code == "manifest_conflict"
    assert store.save_atomic(identity, manifest, sha) == sha
    path.write_text('{"schema_version":"index_manifest_v3","schema_version":"bad"}',
                    encoding="utf-8")
    with pytest.raises(ManifestStoreError) as damaged:
        store.load(identity)
    assert damaged.value.code == "manifest_failed"
