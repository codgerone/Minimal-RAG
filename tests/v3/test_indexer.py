from dataclasses import replace
from pathlib import Path

import fitz
import pytest

from rag.v3.adapters.artifact_store import LocalArtifactStore
from rag.v3.adapters.chroma_store import ChromaV3Store
from rag.v3.adapters.e5 import default_identity
from rag.v3.adapters.local_registry import LocalPdfRegistry, RegistryRequest
from rag.v3.adapters.manifest_store import LocalManifestStore
from rag.v3.adapters.processor_plugins import PlainPageMainParser
from rag.v3.adapters.recovery_store import LocalRecoveryStore
from rag.v3.adapters.source_probe import PdfSourceProbe
from rag.v3.application.assembly import build_projection, builtin_configuration, index_identity
from rag.v3.application.artifacts import SnapshotArtifactBuilder
from rag.v3.application.chunkers import CharacterChunker
from rag.v3.application.document_processor import DocumentProcessor
from rag.v3.application.health import IndexHealth
from rag.v3.application.indexer import Indexer
from rag.v3.application.publication import Publisher, Recovery
from rag.v3.contracts.processing import ChunkingContext, IndexerInput
from rag.v3.contracts.retrieval import PassageEmbeddingBatch


class FixedEmbedder:
    def __init__(self, identity):
        self.identity = identity

    def encode_passages(self, batch, build_id, index, **_):
        return PassageEmbeddingBatch(index, batch.document_id, batch.file_hash, build_id,
            self.identity, tuple(item.chunk_id for item in batch.chunks),
            tuple((0.5, 0.5) for _ in batch.chunks))


def _pdf(path: Path, text: str) -> None:
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((40, 40), text)
        pdf.save(path)


def test_indexer_incremental_force_failure_and_prune(tmp_path: Path) -> None:
    docs = tmp_path / "documents"
    docs.mkdir()
    _pdf(docs / "a.pdf", "alpha")
    _pdf(docs / "b.pdf", "beta")
    registry = LocalPdfRegistry()
    sources = registry.discover(RegistryRequest(docs, None))
    config = builtin_configuration("plain_text")
    index = index_identity(config)
    projection = build_projection(config)
    embedding = replace(default_identity(), dimension=2)
    manifest = LocalManifestStore(tmp_path)
    vector = ChromaV3Store(tmp_path)
    artifacts = LocalArtifactStore(tmp_path)
    recovery_store = LocalRecoveryStore(tmp_path)
    publisher = Publisher(manifest, vector, artifacts, recovery_store)
    recovery = Recovery(manifest, vector, artifacts, recovery_store)
    health = IndexHealth(projection, embedding, PdfSourceProbe(), manifest, vector,
                         artifacts, recovery_store)
    processor = DocumentProcessor(PlainPageMainParser())
    context = ChunkingContext(index, "pending", embedding, 300, 50, None, None)
    indexer = Indexer(config.name, index, projection, context, False, processor,
                      CharacterChunker(), FixedEmbedder(embedding),
                      SnapshotArtifactBuilder(tmp_path), publisher, health, recovery)
    request = IndexerInput(config.name, index, sources, None, False, False, False)
    first = indexer.ingest(request)
    assert [item.state for item in first.documents] == ["added", "added"]
    assert first.collection_count == 2
    assert health.check(index, sources).usable
    second = indexer.ingest(request)
    assert [item.state for item in second.documents] == ["skipped", "skipped"]

    damaged_record = manifest.load(index).documents[sources[0].document_id]
    damaged_chunks = (tmp_path / index.namespace_path / damaged_record.artifact_path
                      / "chunks.json")
    damaged_chunks.write_bytes(b"{}")
    assert health.check(index, sources).document_statuses[0].state == "invalid"
    repaired = indexer.ingest(request)
    assert [item.state for item in repaired.documents] == ["updated", "skipped"]
    assert health.check(index, sources).usable

    old_sha = manifest.raw(index)[1]

    original_publish = publisher.publish
    def fail_before_journal(_request):
        raise RuntimeError("injected publication prevalidation failure")
    publisher.publish = fail_before_journal
    publication_failed = indexer.ingest(replace(request, force=True))
    assert [item.state for item in publication_failed.documents] == ["failed", "failed"]
    assert all(item.error.code == "publication_failed" for item in publication_failed.documents)
    assert manifest.raw(index)[1] == old_sha
    assert list((tmp_path / index.namespace_path / "staging").glob("*")) == []
    publisher.publish = original_publish

    class BrokenProcessor:
        def process(self, document_request):
            if document_request.source.document_name == "b.pdf":
                raise RuntimeError("injected parser failure")
            return processor.process(document_request)

    broken = Indexer(config.name, index, projection, context, False, BrokenProcessor(),
                     CharacterChunker(), FixedEmbedder(embedding),
                     SnapshotArtifactBuilder(tmp_path), publisher, health, recovery)
    failed = broken.ingest(replace(request, force=True))
    assert [item.state for item in failed.documents] == ["not_published", "failed"]
    assert manifest.raw(index)[1] == old_sha
    assert vector.count(index) == 2
    assert list((tmp_path / index.namespace_path / "staging").glob("*")) == []

    damaged_manifest = tmp_path / index.namespace_path / "manifest.json"
    damaged_manifest.write_bytes(b"{invalid manifest bytes")
    assert "manifest_invalid" in {item.code for item in health.check(index, sources).issues}
    with pytest.raises(RuntimeError, match="full force"):
        indexer.ingest(request)
    repaired_manifest = indexer.ingest(replace(request, force=True))
    assert [item.state for item in repaired_manifest.documents] == ["updated", "updated"]
    assert health.check(index, sources).usable

    (docs / "b.pdf").unlink()
    remaining = registry.discover(RegistryRequest(docs, None))
    missing = indexer.ingest(replace(request, sources=remaining))
    assert [item.state for item in missing.documents] == ["skipped", "missing"]
    pruned = indexer.ingest(replace(request, sources=remaining, prune=True,
                                    prune_authorized=True))
    assert [item.state for item in pruned.documents] == ["skipped", "pruned"]
    assert vector.count(index) == 1
    assert health.check(index, remaining).usable


def test_empty_unbuilt_index_is_not_query_ready(tmp_path: Path) -> None:
    config = builtin_configuration("plain_text")
    index = index_identity(config)
    health = IndexHealth(build_projection(config), replace(default_identity(), dimension=2),
        PdfSourceProbe(), LocalManifestStore(tmp_path), ChromaV3Store(tmp_path),
        LocalArtifactStore(tmp_path), LocalRecoveryStore(tmp_path))
    report = health.check(index, ())
    assert not report.usable
    assert report.document_statuses == ()
    assert report.issues == ()


def test_source_probe_does_not_admit_image_only_pdf(tmp_path: Path) -> None:
    docs = tmp_path / "documents"
    docs.mkdir()
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.draw_rect(fitz.Rect(40, 40, 120, 120))
        pdf.save(docs / "scan.pdf")
    source = LocalPdfRegistry().discover(RegistryRequest(docs, None))[0]
    result = PdfSourceProbe().probe(source)
    assert result.status == "unprocessable"
    assert result.reason == "pdf_no_content"
