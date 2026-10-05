from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import os
import pickle
import subprocess
import sys

import fitz
import pytest

from rag.v3.adapters.artifact_store import ArtifactStoreError, LocalArtifactStore, build_artifact_path
from rag.v3.adapters.chroma_store import ChromaV3Store
from rag.v3.adapters.e5 import default_identity
from rag.v3.adapters.local_registry import LocalPdfRegistry, RegistryRequest
from rag.v3.adapters.manifest_store import LocalManifestStore
from rag.v3.adapters.processor_plugins import PlainPageMainParser
from rag.v3.adapters.recovery_store import LocalRecoveryStore
from rag.v3.adapters.source_probe import PdfSourceProbe
from rag.v3.adapters.vector_codec import make_vector_records
from rag.v3.application.artifacts import stage_artifacts
from rag.v3.application.assembly import build_projection, builtin_configuration, index_identity
from rag.v3.application.document_processor import DocumentProcessor
from rag.v3.application.plain_document import PageCharacterChunker, PageCharacterParameters
from rag.v3.application.publication import Publisher, Recovery
from rag.v3.application.health import IndexHealth
from rag.v3.application.read_gate import ManifestReadGate, ReadGateError
from rag.v3.application.vector_compare import snapshots_equal
from rag.v3.contracts.processing import DocumentRequest
from rag.v3.contracts.retrieval import PassageEmbeddingBatch
from rag.v3.contracts.storage import Manifest, ManifestDocumentRecord, PublicationRequest


class SimulatedCrash(BaseException):
    pass


def _payload(tmp_path: Path, source, config, tx: str, build: str,
             old_manifest: Manifest | None, expected_sha: str | None) -> PublicationRequest:
    index = index_identity(config)
    result = DocumentProcessor(PlainPageMainParser()).process(
        DocumentRequest(source, index, build, False))
    batch = PageCharacterChunker(PageCharacterParameters()).chunk(result.parsed_document)
    staged = stage_artifacts(result, batch, build_projection(config), tmp_path, tx)
    embedding_identity = replace(default_identity(), dimension=2)
    embedded = PassageEmbeddingBatch(index, source.document_id, source.file_hash, build,
                                     embedding_identity,
                                     tuple(chunk.chunk_id for chunk in batch.chunks),
                                     tuple((0.5, 0.5) for _ in batch.chunks))
    records = make_vector_records(source, batch, embedded)
    files = {item.role: item.sha256 for item in staged.files}
    files["snapshot_manifest"] = staged.snapshot_manifest_sha256
    now = datetime.now(timezone.utc).isoformat()
    provisional = ManifestDocumentRecord(source.document_id, source.document_name,
        source.relative_path, source.file_hash, build, 1, batch.character_count,
        len(batch.chunks), "placeholder", files, now)
    entry = replace(provisional, artifact_path=build_artifact_path(provisional))
    manifest = Manifest("index_manifest_v3", index, build_projection(config),
                        embedding_identity, {source.document_id: entry},
                        old_manifest.created_at if old_manifest else now, now)
    return PublicationRequest(tx, "replace_document", index, source.document_id,
                              records, (staged,), manifest, expected_sha)


def test_publication_commit_rollback_and_crash_recovery(tmp_path: Path) -> None:
    source_root = tmp_path / "documents"
    source_root.mkdir()
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((40, 40), "Publication recovery evidence")
        pdf.save(source_root / "sample.pdf")
    source = LocalPdfRegistry().discover(RegistryRequest(source_root, "sample.pdf"))[0]
    config = builtin_configuration("plain_text")
    index = index_identity(config)
    manifest_store = LocalManifestStore(tmp_path)
    vector = ChromaV3Store(tmp_path)
    artifacts = LocalArtifactStore(tmp_path)
    recovery_store = LocalRecoveryStore(tmp_path)
    publisher = Publisher(manifest_store, vector, artifacts, recovery_store)

    def build_directories() -> list[Path]:
        root = tmp_path / index.namespace_path / "artifacts" / "documents"
        return sorted(path for path in root.glob("*/*") if path.is_dir())

    def health():
        return IndexHealth(build_projection(config), replace(default_identity(), dimension=2),
                           PdfSourceProbe(), manifest_store, vector, artifacts,
                           recovery_store).check(index, (source,))

    assert health().document_statuses[0].state == "new"

    initial = _payload(tmp_path, source, config, "tx_initial", "build_1", None, None)
    assert publisher.publish(initial).outcome == "committed"
    assert build_directories() == [tmp_path / index.namespace_path /
                                   initial.new_manifest.documents[source.document_id].artifact_path]
    old, old_sha = manifest_store.raw(index)
    assert old is not None
    assert Recovery(manifest_store, vector, artifacts, recovery_store).verifier.verify(index, old_sha)
    assert health().usable
    active_chunks = tmp_path / index.namespace_path / initial.new_manifest.documents[
        source.document_id].artifact_path / "chunks.json"
    original_chunks = active_chunks.read_bytes()
    active_chunks.write_bytes(b"{}")
    assert health().document_statuses[0].state == "invalid"
    damaged_old = _payload(tmp_path, source, config, "tx_damaged_old", "build_damaged",
                           manifest_store.load(index), old_sha)
    failing = Publisher(manifest_store, vector, artifacts, recovery_store,
                        lambda step: (_ for _ in ()).throw(RuntimeError("injected"))
                        if step == "after_vector_replace" else None)
    assert failing.publish(damaged_old).outcome == "rolled_back"
    assert active_chunks.read_bytes() == b"{}"
    assert manifest_store.raw(index)[1] == old_sha
    active_chunks.write_bytes(original_chunks)
    assert health().usable

    failed = _payload(tmp_path, source, config, "tx_rollback", "build_2",
                      manifest_store.load(index), old_sha)
    failing = Publisher(manifest_store, vector, artifacts, recovery_store,
                        lambda step: (_ for _ in ()).throw(RuntimeError("injected"))
                        if step == "after_vector_replace" else None)
    assert failing.publish(failed).outcome == "rolled_back"
    assert manifest_store.raw(index)[1] == old_sha
    assert recovery_store.list_pending(index) == ()

    before_commit = _payload(tmp_path, source, config, "tx_crash_before", "build_3",
                             manifest_store.load(index), old_sha)
    crashing = Publisher(manifest_store, vector, artifacts, recovery_store,
                         lambda step: (_ for _ in ()).throw(SimulatedCrash())
                         if step == "after_artifact_publish" else None)
    with pytest.raises(SimulatedCrash):
        crashing.publish(before_commit)
    assert len(recovery_store.list_pending(index)) == 1
    assert health().document_statuses[0].state == "unassessed"
    recovered = Recovery(manifest_store, vector, artifacts, recovery_store).recover(
        index, "tx_crash_before")
    assert recovered.outcome == "rolled_back"
    assert manifest_store.raw(index)[1] == old_sha
    assert health().usable
    assert len(build_directories()) == 1

    after_commit = _payload(tmp_path, source, config, "tx_crash_after", "build_4",
                            manifest_store.load(index), old_sha)
    crashing = Publisher(manifest_store, vector, artifacts, recovery_store,
                         lambda step: (_ for _ in ()).throw(SimulatedCrash())
                         if step == "after_manifest_publish" else None)
    with pytest.raises(SimulatedCrash):
        crashing.publish(after_commit)
    recovered = Recovery(manifest_store, vector, artifacts, recovery_store).recover(
        index, "tx_crash_after")
    assert recovered.outcome == "committed"
    assert manifest_store.load(index) == after_commit.new_manifest
    assert recovery_store.list_pending(index) == ()
    assert build_directories() == [tmp_path / index.namespace_path /
                                   after_commit.new_manifest.documents[source.document_id].artifact_path]

    force_payload = _payload(tmp_path, source, config, "tx_force", "build_5",
                             manifest_store.load(index), manifest_store.raw(index)[1])
    force_payload = replace(force_payload, operation="replace_collection", document_id=None)
    assert publisher.publish(force_payload).outcome == "committed"
    assert build_directories() == [tmp_path / index.namespace_path /
                                   force_payload.new_manifest.documents[source.document_id].artifact_path]
    force_sha = manifest_store.raw(index)[1]
    assert force_sha is not None

    prune_manifest = replace(force_payload.new_manifest, documents={},
                             updated_at=datetime.now(timezone.utc).isoformat())
    prune = PublicationRequest("tx_prune_before", "prune_document", index,
                               source.document_id, (), (), prune_manifest, force_sha)
    crashing = Publisher(manifest_store, vector, artifacts, recovery_store,
                         lambda step: (_ for _ in ()).throw(SimulatedCrash())
                         if step == "after_artifact_publish" else None)
    with pytest.raises(SimulatedCrash):
        crashing.publish(prune)
    recovered = Recovery(manifest_store, vector, artifacts, recovery_store).recover(
        index, "tx_prune_before")
    assert recovered.outcome == "rolled_back"
    assert manifest_store.raw(index)[1] == force_sha
    assert publisher.publish(replace(prune, transaction_id="tx_prune_final")).outcome == "committed"
    assert manifest_store.load(index).documents == {}
    assert vector.count(index) == 0
    assert build_directories() == []


def test_obsolete_cleanup_failure_blocks_until_recovery_retries(tmp_path: Path) -> None:
    source_root = tmp_path / "documents"
    source_root.mkdir()
    with fitz.open() as pdf:
        pdf.new_page().insert_text((40, 40), "Cleanup retry evidence")
        pdf.save(source_root / "sample.pdf")
    source = LocalPdfRegistry().discover(RegistryRequest(source_root, "sample.pdf"))[0]
    config = builtin_configuration("plain_text")
    index = index_identity(config)
    manifest_store = LocalManifestStore(tmp_path)
    vector = ChromaV3Store(tmp_path)
    artifacts = LocalArtifactStore(tmp_path)
    recovery_store = LocalRecoveryStore(tmp_path)
    publisher = Publisher(manifest_store, vector, artifacts, recovery_store)
    initial = _payload(tmp_path, source, config, "tx_initial", "build_1", None, None)
    assert publisher.publish(initial).outcome == "committed"
    updated = _payload(tmp_path, source, config, "tx_updated", "build_2",
                       manifest_store.load(index), manifest_store.raw(index)[1])

    class FailingCleanup(LocalArtifactStore):
        def cleanup_obsolete(self, target_index, active_records):
            raise ArtifactStoreError("artifact_failed", "injected cleanup failure")

    result = Publisher(manifest_store, vector, FailingCleanup(tmp_path),
                       recovery_store).publish(updated)
    assert result.outcome == "failed"
    assert manifest_store.load(index) == updated.new_manifest
    assert len(recovery_store.list_pending(index)) == 1
    assert not IndexHealth(build_projection(config), replace(default_identity(), dimension=2),
                           PdfSourceProbe(), manifest_store, vector, artifacts,
                           recovery_store).check(index, (source,)).usable
    document_root = tmp_path / index.namespace_path / "artifacts" / "documents"
    assert len(list(document_root.glob("*/*"))) == 2

    assert Recovery(manifest_store, vector, artifacts, recovery_store).recover(
        index, "tx_updated").outcome == "committed"
    assert recovery_store.list_pending(index) == ()
    assert list(document_root.glob("*/*")) == [tmp_path / index.namespace_path /
                                              updated.new_manifest.documents[
                                                  source.document_id].artifact_path]


def test_publication_recovery_from_independent_process(tmp_path: Path) -> None:
    source_root = tmp_path / "documents"
    source_root.mkdir()
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((40, 40), "Cross process publication evidence")
        pdf.save(source_root / "sample.pdf")
    source = LocalPdfRegistry().discover(RegistryRequest(source_root, "sample.pdf"))[0]
    config = builtin_configuration("plain_text")
    index = index_identity(config)
    manifest_store = LocalManifestStore(tmp_path)
    vector = ChromaV3Store(tmp_path)
    artifacts = LocalArtifactStore(tmp_path)
    recovery_store = LocalRecoveryStore(tmp_path)
    assert Publisher(manifest_store, vector, artifacts, recovery_store).publish(
        _payload(tmp_path, source, config, "tx_first", "build_first", None, None)
    ).outcome == "committed"
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONPATH": str(root)}
    child = """
import os, pickle, sys
from pathlib import Path
from rag.v3.adapters.artifact_store import LocalArtifactStore
from rag.v3.adapters.chroma_store import ChromaV3Store
from rag.v3.adapters.manifest_store import LocalManifestStore
from rag.v3.adapters.recovery_store import LocalRecoveryStore
from rag.v3.application.publication import Publisher
base = Path(sys.argv[1])
payload = pickle.loads(Path(sys.argv[2]).read_bytes())
checkpoint = sys.argv[3]
def stop(step):
    if step == checkpoint:
        os._exit(71)
Publisher(LocalManifestStore(base), ChromaV3Store(base), LocalArtifactStore(base),
          LocalRecoveryStore(base), stop).publish(payload)
"""
    recovery = """
import sys
from pathlib import Path
from rag.v3.adapters.artifact_store import LocalArtifactStore
from rag.v3.adapters.chroma_store import ChromaV3Store
from rag.v3.adapters.manifest_store import LocalManifestStore
from rag.v3.adapters.recovery_store import LocalRecoveryStore
from rag.v3.application.publication import Recovery
base = Path(sys.argv[1])
result = Recovery(LocalManifestStore(base), ChromaV3Store(base),
                  LocalArtifactStore(base), LocalRecoveryStore(base)).recover(
                      __import__('pickle').loads(Path(sys.argv[2]).read_bytes()).index_identity,
                      sys.argv[3])
print(result.outcome)
"""

    def crash_and_recover(request, step: str, expected: str):
        tx = request.transaction_id
        before_sha = manifest_store.raw(index)[1]
        payload_path = tmp_path / (tx + ".pkl")
        payload_path.write_bytes(pickle.dumps(request))
        crashed = subprocess.run([sys.executable, "-c", child, str(tmp_path),
                                  str(payload_path), step], cwd=root, env=env,
                                 capture_output=True, text=True)
        assert crashed.returncode == 71, crashed.stderr
        if expected == "untouched":
            assert LocalRecoveryStore(tmp_path).list_pending(index) == ()
            assert manifest_store.raw(index)[1] == before_sha
            return
        if expected == "committed_no_journal":
            assert LocalRecoveryStore(tmp_path).list_pending(index) == ()
            assert manifest_store.load(index) == request.new_manifest
            return
        assert len(LocalRecoveryStore(tmp_path).list_pending(index)) == 1
        resumed = subprocess.run([sys.executable, "-c", recovery, str(tmp_path),
                                  str(payload_path), tx], cwd=root, env=env,
                                 capture_output=True, text=True)
        assert resumed.returncode == 0, resumed.stderr
        assert resumed.stdout.strip() == expected
        assert LocalRecoveryStore(tmp_path).list_pending(index) == ()
        if expected == "rolled_back":
            assert manifest_store.raw(index)[1] == before_sha
            Recovery(manifest_store, vector, artifacts, recovery_store).verifier.verify(
                index, before_sha)
        else:
            assert manifest_store.load(index) == request.new_manifest
            Recovery(manifest_store, vector, artifacts, recovery_store).verifier.verify(
                index, manifest_store.raw(index)[1])

    before_steps = ("before_prepare", "prepared", "before_vector_replace",
                    "after_vector_replace", "before_vector_verify",
                    "after_vector_verify", "before_artifact_publish",
                    "after_artifact_publish", "before_artifact_verify",
                    "after_artifact_verify", "before_manifest_publish")
    after_steps = ("after_manifest_publish", "after_commit_verify",
                   "before_cleanup", "after_cleanup")
    for number, step in enumerate((*before_steps, *after_steps)):
        tx = f"tx_document_{number}"
        expected = ("untouched" if step == "before_prepare" else
                    "rolled_back" if step in before_steps else
                    "committed_no_journal" if step == "after_cleanup" else "committed")
        request = _payload(tmp_path, source, config, tx, "build_" + tx,
                           manifest_store.load(index), manifest_store.raw(index)[1])
        crash_and_recover(request, step, expected)
    for number, step in enumerate((*before_steps, *after_steps)):
        tx = f"tx_force_{number}"
        expected = ("untouched" if step == "before_prepare" else
                    "rolled_back" if step in before_steps else
                    "committed_no_journal" if step == "after_cleanup" else "committed")
        request = _payload(tmp_path, source, config, tx, "build_" + tx,
                           manifest_store.load(index), manifest_store.raw(index)[1])
        crash_and_recover(replace(request, operation="replace_collection", document_id=None),
                          step, expected)
    for number, step in enumerate((*before_steps, *after_steps)):
        tx = f"tx_prune_{number}"
        expected = ("untouched" if step == "before_prepare" else
                    "rolled_back" if step in before_steps else
                    "committed_no_journal" if step == "after_cleanup" else "committed")
        if not manifest_store.load(index).documents:
            seed = _payload(tmp_path, source, config, "tx_restore_" + tx,
                            "build_restore_" + tx, manifest_store.load(index),
                            manifest_store.raw(index)[1])
            assert Publisher(manifest_store, vector, artifacts, recovery_store).publish(
                seed).outcome == "committed"
        active = manifest_store.load(index)
        emptied = replace(active, documents={}, updated_at=datetime.now(timezone.utc).isoformat())
        request = PublicationRequest(tx, "prune_document", index, source.document_id,
                                     (), (), emptied, manifest_store.raw(index)[1])
        crash_and_recover(request, step, expected)


def test_full_force_preserves_invalid_manifest_bytes_on_rollback(tmp_path: Path) -> None:
    source_root = tmp_path / "documents"
    source_root.mkdir()
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((40, 40), "Invalid manifest recovery evidence")
        pdf.save(source_root / "sample.pdf")
    source = LocalPdfRegistry().discover(RegistryRequest(source_root, "sample.pdf"))[0]
    config = builtin_configuration("plain_text")
    index = index_identity(config)
    manifest_store = LocalManifestStore(tmp_path)
    vector = ChromaV3Store(tmp_path)
    artifacts = LocalArtifactStore(tmp_path)
    recovery_store = LocalRecoveryStore(tmp_path)
    publisher = Publisher(manifest_store, vector, artifacts, recovery_store)
    initial = _payload(tmp_path, source, config, "tx_initial", "build_1", None, None)
    assert publisher.publish(initial).outcome == "committed"
    old_vector_ids = tuple(item.chunk_id for item in vector.list_records(index))
    old_active = tmp_path / index.namespace_path / initial.new_manifest.documents[
        source.document_id].artifact_path / "chunks.json"
    old_chunks = old_active.read_bytes()
    manifest_path = tmp_path / index.namespace_path / "manifest.json"
    malformed = b"{invalid manifest bytes"
    manifest_path.write_bytes(malformed)
    old_sha = manifest_store.raw_unvalidated(index)[1]
    request = _payload(tmp_path, source, config, "tx_crash", "build_2", None, old_sha)
    request = replace(request, operation="replace_collection", document_id=None)
    target = tmp_path / index.namespace_path / request.new_manifest.documents[
        source.document_id].artifact_path
    crashing = Publisher(manifest_store, vector, artifacts, recovery_store,
        lambda step: (_ for _ in ()).throw(SimulatedCrash())
        if step == "after_artifact_publish" else None)
    with pytest.raises(SimulatedCrash):
        crashing.publish(request)
    assert target.exists()
    recovered = Recovery(manifest_store, vector, artifacts, recovery_store).recover(
        index, "tx_crash")
    assert recovered.outcome == "rolled_back", recovery_store.read(index, "tx_crash")
    assert manifest_store.raw_unvalidated(index) == (malformed, old_sha)
    assert tuple(item.chunk_id for item in vector.list_records(index)) == old_vector_ids
    assert old_active.read_bytes() == old_chunks
    assert not target.exists()
    repaired = _payload(tmp_path, source, config, "tx_repaired", "build_3", None, old_sha)
    repaired = replace(repaired, operation="replace_collection", document_id=None)
    assert publisher.publish(repaired).outcome == "committed"
    assert Recovery(manifest_store, vector, artifacts, recovery_store).verifier.verify(
        index, manifest_store.raw(index)[1]) == repaired.new_manifest


def test_rollback_restores_preexisting_corrupt_new_artifact_target(tmp_path: Path) -> None:
    source_root = tmp_path / "documents"
    source_root.mkdir()
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((40, 40), "Artifact collision evidence")
        pdf.save(source_root / "sample.pdf")
    source = LocalPdfRegistry().discover(RegistryRequest(source_root, "sample.pdf"))[0]
    config = builtin_configuration("plain_text")
    index = index_identity(config)
    manifest_store = LocalManifestStore(tmp_path)
    vector = ChromaV3Store(tmp_path)
    artifacts = LocalArtifactStore(tmp_path)
    recovery_store = LocalRecoveryStore(tmp_path)
    initial = _payload(tmp_path, source, config, "tx_initial", "build_1", None, None)
    assert Publisher(manifest_store, vector, artifacts, recovery_store).publish(
        initial).outcome == "committed"
    old_bytes, old_sha = manifest_store.raw(index)
    old_vector_ids = tuple(item.chunk_id for item in vector.list_records(index))
    request = _payload(tmp_path, source, config, "tx_collision", "build_2",
                       manifest_store.load(index), old_sha)
    target = tmp_path / index.namespace_path / request.new_manifest.documents[
        source.document_id].artifact_path
    target.mkdir(parents=True)
    corrupt = b"preexisting corrupt artifact evidence"
    (target / "orphan.bin").write_bytes(corrupt)
    crashing = Publisher(manifest_store, vector, artifacts, recovery_store,
        lambda step: (_ for _ in ()).throw(SimulatedCrash())
        if step == "after_artifact_publish" else None)
    with pytest.raises(SimulatedCrash):
        crashing.publish(request)
    recovered = Recovery(manifest_store, vector, artifacts, recovery_store).recover(
        index, "tx_collision")
    assert recovered.outcome == "rolled_back", recovery_store.read(index, "tx_collision")
    assert manifest_store.raw(index) == (old_bytes, old_sha)
    assert tuple(item.chunk_id for item in vector.list_records(index)) == old_vector_ids
    assert tuple(path.name for path in target.iterdir()) == ("orphan.bin",)
    assert (target / "orphan.bin").read_bytes() == corrupt


def test_recovery_actions_survive_independent_process_crashes(tmp_path: Path) -> None:
    source_root = tmp_path / "documents"
    source_root.mkdir()
    with fitz.open() as pdf:
        pdf.new_page().insert_text((40, 40), "Recovery step interruption evidence")
        pdf.save(source_root / "sample.pdf")
    source = LocalPdfRegistry().discover(RegistryRequest(source_root, "sample.pdf"))[0]
    config = builtin_configuration("plain_text")
    index = index_identity(config)
    manifest_store = LocalManifestStore(tmp_path)
    vector = ChromaV3Store(tmp_path)
    artifacts = LocalArtifactStore(tmp_path)
    recovery_store = LocalRecoveryStore(tmp_path)
    initial = _payload(tmp_path, source, config, "tx_initial", "build_1", None, None)
    assert Publisher(manifest_store, vector, artifacts, recovery_store).publish(
        initial).outcome == "committed"
    old_bytes, old_sha = manifest_store.raw(index)
    old_vector_ids = tuple(item.chunk_id for item in vector.list_records(index))
    old_path = tmp_path / index.namespace_path / initial.new_manifest.documents[
        source.document_id].artifact_path / "chunks.json"
    old_chunks = old_path.read_bytes()
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONPATH": str(root)}
    child = """
import os, pickle, sys
from pathlib import Path
from rag.v3.adapters.artifact_store import LocalArtifactStore
from rag.v3.adapters.chroma_store import ChromaV3Store
from rag.v3.adapters.manifest_store import LocalManifestStore
from rag.v3.adapters.recovery_store import LocalRecoveryStore
from rag.v3.application.publication import Recovery
base = Path(sys.argv[1])
request = pickle.loads(Path(sys.argv[2]).read_bytes())
def stop(step):
    if step == sys.argv[3]:
        os._exit(71)
result = Recovery(LocalManifestStore(base), ChromaV3Store(base),
    LocalArtifactStore(base), LocalRecoveryStore(base), stop).recover(
        request.index_identity, request.transaction_id)
print(result.outcome)
"""
    steps = ("before_vector_restore", "after_vector_restore",
             "before_artifact_restore", "after_artifact_restore",
             "before_manifest_restore", "after_manifest_restore")
    for number, step in enumerate(steps, 1):
        tx = f"tx_recovery_{number}"
        request = _payload(tmp_path, source, config, tx, f"build_{number + 1}",
                           manifest_store.load(index), old_sha)
        crashing = Publisher(manifest_store, vector, artifacts, recovery_store,
            lambda point: (_ for _ in ()).throw(SimulatedCrash())
            if point == "after_artifact_publish" else None)
        with pytest.raises(SimulatedCrash):
            crashing.publish(request)
        payload_path = tmp_path / (tx + ".pkl")
        payload_path.write_bytes(pickle.dumps(request))
        interrupted = subprocess.run([sys.executable, "-c", child,
            str(tmp_path), str(payload_path), step], cwd=root, env=env,
            capture_output=True, text=True)
        assert interrupted.returncode == 71, (step, interrupted.stderr)
        assert len(recovery_store.list_pending(index)) == 1
        resumed = subprocess.run([sys.executable, "-c", child,
            str(tmp_path), str(payload_path), "none"], cwd=root, env=env,
            capture_output=True, text=True)
        assert resumed.returncode == 0, (step, resumed.stderr)
        assert resumed.stdout.strip() == "rolled_back", step
        assert recovery_store.list_pending(index) == ()
        assert manifest_store.raw(index) == (old_bytes, old_sha)
        assert tuple(item.chunk_id for item in vector.list_records(index)) == old_vector_ids
        assert old_path.read_bytes() == old_chunks


def test_first_publication_crash_matrix_restores_absence_or_commits(tmp_path: Path) -> None:
    source_root = tmp_path / "documents"
    source_root.mkdir()
    with fitz.open() as pdf:
        pdf.new_page().insert_text((40, 40), "First publication matrix evidence")
        pdf.save(source_root / "sample.pdf")
    source = LocalPdfRegistry().discover(RegistryRequest(source_root, "sample.pdf"))[0]
    config = builtin_configuration("plain_text")
    index = index_identity(config)
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONPATH": str(root)}
    child = """
import os, pickle, sys
from pathlib import Path
from rag.v3.adapters.artifact_store import LocalArtifactStore
from rag.v3.adapters.chroma_store import ChromaV3Store
from rag.v3.adapters.manifest_store import LocalManifestStore
from rag.v3.adapters.recovery_store import LocalRecoveryStore
from rag.v3.application.publication import Publisher, Recovery
base = Path(sys.argv[1])
request = pickle.loads(Path(sys.argv[2]).read_bytes())
if sys.argv[3] == 'publish':
    def stop(step):
        if step == sys.argv[4]:
            os._exit(71)
    Publisher(LocalManifestStore(base), ChromaV3Store(base),
        LocalArtifactStore(base), LocalRecoveryStore(base), stop).publish(request)
else:
    result = Recovery(LocalManifestStore(base), ChromaV3Store(base),
        LocalArtifactStore(base), LocalRecoveryStore(base)).recover(
            request.index_identity, request.transaction_id)
    print(result.outcome)
"""
    before_steps = ("before_prepare", "prepared", "before_vector_replace",
                    "after_vector_replace", "before_vector_verify",
                    "after_vector_verify", "before_artifact_publish",
                    "after_artifact_publish", "before_artifact_verify",
                    "after_artifact_verify", "before_manifest_publish")
    after_steps = ("after_manifest_publish", "after_commit_verify",
                   "before_cleanup", "after_cleanup")
    for number, step in enumerate((*before_steps, *after_steps)):
        case_root = tmp_path / f"case_{number}"
        case_root.mkdir()
        request = _payload(case_root, source, config, f"tx_{number}",
                           f"build_{number}", None, None)
        payload_path = case_root / "request.pkl"
        payload_path.write_bytes(pickle.dumps(request))
        crashed = subprocess.run([sys.executable, "-c", child,
            str(case_root), str(payload_path), "publish", step], cwd=root, env=env,
            capture_output=True, text=True)
        assert crashed.returncode == 71, (step, crashed.stderr)
        manifest = LocalManifestStore(case_root)
        vector = ChromaV3Store(case_root)
        artifacts = LocalArtifactStore(case_root)
        recovery_store = LocalRecoveryStore(case_root)
        if step == "before_prepare":
            assert recovery_store.list_pending(index) == ()
            assert manifest.raw(index) == (None, None)
            assert not vector.snapshot(index).collection_existed
            continue
        if step == "after_cleanup":
            assert recovery_store.list_pending(index) == ()
            Recovery(manifest, vector, artifacts, recovery_store).verifier.verify(
                index, manifest.raw(index)[1])
            continue
        assert len(recovery_store.list_pending(index)) == 1
        resumed = subprocess.run([sys.executable, "-c", child,
            str(case_root), str(payload_path), "recover"], cwd=root, env=env,
            capture_output=True, text=True)
        assert resumed.returncode == 0, (step, resumed.stderr)
        expected = "rolled_back" if step in before_steps else "committed"
        assert resumed.stdout.strip() == expected, step
        assert recovery_store.list_pending(index) == ()
        if expected == "rolled_back":
            assert manifest.raw(index) == (None, None)
            assert not vector.snapshot(index).collection_existed
            target = case_root / index.namespace_path / request.new_manifest.documents[
                source.document_id].artifact_path
            assert not target.exists()
        else:
            Recovery(manifest, vector, artifacts, recovery_store).verifier.verify(
                index, manifest.raw(index)[1])


def test_corrupt_recovery_snapshot_keeps_journal_and_blocks_health(tmp_path: Path) -> None:
    source_root = tmp_path / "documents"
    source_root.mkdir()
    with fitz.open() as pdf:
        pdf.new_page().insert_text((40, 40), "Unverifiable recovery evidence")
        pdf.save(source_root / "sample.pdf")
    source = LocalPdfRegistry().discover(RegistryRequest(source_root, "sample.pdf"))[0]
    config = builtin_configuration("plain_text")
    index = index_identity(config)
    manifest = LocalManifestStore(tmp_path)
    vector = ChromaV3Store(tmp_path)
    artifacts = LocalArtifactStore(tmp_path)
    recovery_store = LocalRecoveryStore(tmp_path)
    first = _payload(tmp_path, source, config, "tx_first", "build_1", None, None)
    assert Publisher(manifest, vector, artifacts, recovery_store).publish(
        first).outcome == "committed"
    request = _payload(tmp_path, source, config, "tx_broken", "build_2",
                       manifest.load(index), manifest.raw(index)[1])
    crashing = Publisher(manifest, vector, artifacts, recovery_store,
        lambda point: (_ for _ in ()).throw(SimulatedCrash())
        if point == "after_vector_replace" else None)
    with pytest.raises(SimulatedCrash):
        crashing.publish(request)
    snapshot = tmp_path / index.namespace_path / "recovery/tx_broken/old-vector.json"
    snapshot.write_bytes(b"invalid recovery evidence")
    result = Recovery(manifest, vector, artifacts, recovery_store).recover(
        index, "tx_broken")
    assert result.outcome == "failed"
    pending = recovery_store.list_pending(index)
    assert len(pending) == 1
    assert pending[0].state == "recovery_failed"
    assert pending[0].error_code == "snapshot_unreadable"
    health = IndexHealth(build_projection(config), replace(default_identity(), dimension=2),
                         PdfSourceProbe(), manifest, vector, artifacts,
                         recovery_store).check(index, (source,))
    assert not health.usable
    assert health.document_statuses[0].state == "unassessed"
    with pytest.raises(ReadGateError, match="pending recovery"):
        ManifestReadGate(manifest, recovery_store).begin(index)


def test_multi_document_force_recovers_after_only_first_artifact_publishes(tmp_path: Path) -> None:
    docs = tmp_path / "documents"
    docs.mkdir()
    for name in ("a.pdf", "b.pdf"):
        with fitz.open() as pdf:
            pdf.new_page().insert_text((40, 40), f"Multi document {name}")
            pdf.save(docs / name)
    sources = LocalPdfRegistry().discover(RegistryRequest(docs, None))
    config = builtin_configuration("plain_text")
    index = index_identity(config)
    manifest = LocalManifestStore(tmp_path)
    vector = ChromaV3Store(tmp_path)
    artifacts = LocalArtifactStore(tmp_path)
    recovery_store = LocalRecoveryStore(tmp_path)

    def collection_request(tx: str, build: str, old: Manifest | None,
                           old_sha: str | None) -> PublicationRequest:
        parts = tuple(_payload(tmp_path, source, config, tx, build + "_" + str(number),
                               old, old_sha) for number, source in enumerate(sources))
        combined = replace(parts[0].new_manifest, documents={
            key: value for part in parts for key, value in part.new_manifest.documents.items()})
        return PublicationRequest(tx, "replace_collection", index, None,
            tuple(item for part in parts for item in part.records),
            tuple(item for part in parts for item in part.staged_artifacts),
            combined, old_sha)

    initial = collection_request("tx_initial", "build_1", None, None)
    assert Publisher(manifest, vector, artifacts, recovery_store).publish(
        initial).outcome == "committed"
    old_bytes, old_sha = manifest.raw(index)
    old_vectors = vector.snapshot(index)
    old_chunks = {source.document_id: (tmp_path / index.namespace_path /
        initial.new_manifest.documents[source.document_id].artifact_path /
        "chunks.json").read_bytes() for source in sources}
    request = collection_request("tx_partial", "build_2", manifest.load(index), old_sha)

    class CrashAfterFirstArtifact(LocalArtifactStore):
        published = 0
        def publish(self, target_index, staged, record):
            result = super().publish(target_index, staged, record)
            self.published += 1
            if self.published == 1:
                raise SimulatedCrash()
            return result

    with pytest.raises(SimulatedCrash):
        Publisher(manifest, vector, CrashAfterFirstArtifact(tmp_path),
                  recovery_store).publish(request)
    assert len(recovery_store.list_pending(index)) == 1
    result = Recovery(manifest, vector, LocalArtifactStore(tmp_path),
                      recovery_store).recover(index, "tx_partial")
    assert result.outcome == "rolled_back", recovery_store.read(index, "tx_partial")
    assert manifest.raw(index) == (old_bytes, old_sha)
    assert snapshots_equal(old_vectors, vector.snapshot(index))
    for source in sources:
        old_target = tmp_path / index.namespace_path / initial.new_manifest.documents[
            source.document_id].artifact_path / "chunks.json"
        new_target = tmp_path / index.namespace_path / request.new_manifest.documents[
            source.document_id].artifact_path
        assert old_target.read_bytes() == old_chunks[source.document_id]
        assert not new_target.exists()
