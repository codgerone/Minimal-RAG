import hashlib
from pathlib import Path
from datetime import datetime, timezone

import fitz
import pytest

from rag.v3.adapters.local_registry import LocalPdfRegistry, RegistryRequest
from rag.v3.adapters.processor_plugins import PlainPageMainParser
from rag.v3.adapters.artifact_store import LocalArtifactStore, document_directory
from rag.v3.application.artifacts import ArtifactError, stage_artifacts, verify_staged_artifacts
from rag.v3.application.assembly import build_projection, builtin_configuration, index_identity
from rag.v3.application.document_processor import DocumentProcessor
from rag.v3.application.plain_document import PageCharacterChunker, PageCharacterParameters
from rag.v3.contracts.processing import DocumentRequest
from rag.v3.contracts.storage import ManifestDocumentRecord


def test_plain_snapshot_role_set_and_readback_detects_damage(tmp_path: Path) -> None:
    documents = tmp_path / "documents"
    documents.mkdir()
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((40, 40), "<hello> & world")
        pdf.save(documents / "sample.pdf")
    source = LocalPdfRegistry().discover(RegistryRequest(documents, "sample.pdf"))[0]
    config = builtin_configuration("plain_text")
    result = DocumentProcessor(PlainPageMainParser()).process(
        DocumentRequest(source, index_identity(config), "build_1", False))
    batch = PageCharacterChunker(PageCharacterParameters()).chunk(result.parsed_document)
    staged = stage_artifacts(result, batch, build_projection(config), tmp_path, "transaction_1")
    assert [item.role for item in staged.files] == [
        "main_parser_native", "main_parser", "parsed_document", "chunks", "chunk_review"]
    chunk_bytes = (staged.staging_path / "chunks.json").read_bytes()
    assert chunk_bytes.startswith(b"{\n  ") and chunk_bytes.endswith(b"\n")
    assert next(ref.sha256 for ref in staged.files if ref.role == "chunks") == hashlib.sha256(chunk_bytes).hexdigest()
    snapshot_bytes = (staged.staging_path / "snapshot-manifest.json").read_bytes()
    assert snapshot_bytes.startswith(b"{\n  ") and snapshot_bytes.endswith(b"\n")
    assert staged.snapshot_manifest_sha256 == hashlib.sha256(snapshot_bytes).hexdigest()
    chunk_html = (staged.staging_path / "chunk-review.html").read_text(encoding="utf-8")
    assert "&lt;hello&gt; &amp; world" in chunk_html
    assert "<hello> & world" not in chunk_html
    assert not (staged.staging_path / "table-selector.json").exists()
    verify_staged_artifacts(staged, branch=False)
    (staged.staging_path / "chunks.json").write_bytes(b"{}")
    with pytest.raises(ArtifactError, match="artifact_staging_readback"):
        verify_staged_artifacts(staged, branch=False)


def test_artifact_publish_quarantine_and_restore(tmp_path: Path) -> None:
    documents = tmp_path / "documents"
    documents.mkdir()
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((40, 40), "body")
        pdf.save(documents / "sample.pdf")
    source = LocalPdfRegistry().discover(RegistryRequest(documents, "sample.pdf"))[0]
    config = builtin_configuration("plain_text")
    index = index_identity(config)
    result = DocumentProcessor(PlainPageMainParser()).process(
        DocumentRequest(source, index, "build_1", False))
    batch = PageCharacterChunker(PageCharacterParameters()).chunk(result.parsed_document)
    staged = stage_artifacts(result, batch, build_projection(config), tmp_path, "transaction_2")
    artifact_path = (f"artifacts/documents/"
                     f"{document_directory(source.relative_path, source.document_id)}/build_1")
    files = {item.role: item.sha256 for item in staged.files}
    files["snapshot_manifest"] = staged.snapshot_manifest_sha256
    record = ManifestDocumentRecord(source.document_id, source.document_name,
                                    source.relative_path, source.file_hash, "build_1",
                                    1, batch.character_count, len(batch.chunks),
                                    artifact_path, files, datetime.now(timezone.utc).isoformat())
    store = LocalArtifactStore(tmp_path)
    store.stage(index, "transaction_2", staged)
    assert store.publish(index, staged, record) == artifact_path
    store.verify_active(index, record)
    snapshot = store.snapshot(index, "transaction_2", record)
    quarantine = store.quarantine(index, "transaction_2", record)
    assert not (tmp_path / index.namespace_path / artifact_path).exists()
    store.restore(index, quarantine, record)
    store.verify_active(index, record)
    assert snapshot.files["chunks.json"] == record.artifact_files["chunks"]
