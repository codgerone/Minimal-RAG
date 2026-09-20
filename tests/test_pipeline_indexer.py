from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from rag.build_config import (
    EmbeddingConfig, TokenizerConfig, V1BuildConfig, V1ChunkerConfig,
    V1DependencyVersions, V1ParserConfig,
)
from rag.models import DocumentBuildResult, DocumentBuildStats, TextChunk
from rag.errors import ManifestError
from rag.pipeline_indexer import RuntimeIndexer
from rag.pipeline_registry import build_config_fingerprint
from tests.test_publication import FakeStore


class Processor:
    def process(self, source, build_id):
        chunk = TextChunk(f"{source.document_id}-p1-c00", source.document_id, source.document_name,
                          source.relative_path, 1, 0, "text", source.file_hash)
        return DocumentBuildResult(source, "v1", build_id, (chunk,), DocumentBuildStats(1, 4, 1), None)


class Embedder:
    def embed_passages(self, texts):
        return [[1.0, 0.0] for _ in texts]


class FailingProcessor:
    def process(self, _source, _build_id):
        raise ManifestError("injected winner review failure")


def test_runtime_indexer_uses_runtime_processor_fingerprint_and_transaction(tmp_path: Path) -> None:
    documents = tmp_path / "documents"
    documents.mkdir()
    (documents / "a.pdf").write_bytes(b"pdf")
    config = V1BuildConfig(V1ParserConfig(), V1ChunkerConfig(700, 100),
                           TokenizerConfig("model", "revision"), EmbeddingConfig("model", "revision", 2),
                           V1DependencyVersions("1", "1", "1", "1", "1"))
    runtime = SimpleNamespace(
        pipeline_id="v1", collection_name="minimal_rag_documents_v1",
        manifest_path=tmp_path / ".rag/system-v2/pipelines/v1/manifest.json", build_config=config,
        build_config_fingerprint=build_config_fingerprint(config), document_processor=Processor(),
    )
    settings = SimpleNamespace(project_root=tmp_path, documents_dir=documents)
    store = FakeStore()
    store.count_document = lambda document_id: len(store.snapshot(document_id)["ids"])
    store.count_all = lambda: len(store.records)
    indexer = RuntimeIndexer(settings, runtime, Embedder(), store,
                             clock=lambda: datetime(2026, 9, 16, tzinfo=UTC))

    summary = indexer.ingest_one("a.pdf")
    assert summary.added == 1
    assert len(store.records) == 1
    assert runtime.manifest_path.is_file()
    skipped = indexer.ingest_one("a.pdf")
    assert skipped.skipped == 1


def test_prepublication_build_failure_preserves_old_vectors_and_manifest(tmp_path: Path) -> None:
    documents = tmp_path / "documents"
    documents.mkdir()
    pdf = documents / "a.pdf"
    pdf.write_bytes(b"old")
    config = V1BuildConfig(V1ParserConfig(), V1ChunkerConfig(700, 100),
                           TokenizerConfig("model", "revision"), EmbeddingConfig("model", "revision", 2),
                           V1DependencyVersions("1", "1", "1", "1", "1"))
    runtime = SimpleNamespace(
        pipeline_id="v1", collection_name="minimal_rag_documents_v1",
        manifest_path=tmp_path / ".rag/system-v2/pipelines/v1/manifest.json", build_config=config,
        build_config_fingerprint=build_config_fingerprint(config), document_processor=Processor(),
    )
    settings = SimpleNamespace(project_root=tmp_path, documents_dir=documents)
    store = FakeStore()
    indexer = RuntimeIndexer(settings, runtime, Embedder(), store,
                             clock=lambda: datetime(2026, 9, 16, tzinfo=UTC))
    assert indexer.ingest_all().added == 1
    old_records = dict(store.records)
    old_manifest = runtime.manifest_path.read_bytes()

    pdf.write_bytes(b"changed")
    runtime.document_processor = FailingProcessor()
    summary = indexer.ingest_all()
    assert summary.failed == 1
    assert store.records == old_records
    assert runtime.manifest_path.read_bytes() == old_manifest
