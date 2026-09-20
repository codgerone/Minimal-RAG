from pathlib import Path

import pymupdf
import pytest

from rag.document_processor import V1DocumentProcessor
from rag.models import DocumentBuildResult, DocumentBuildStats, SourceDocument


def _source(tmp_path: Path) -> SourceDocument:
    path = tmp_path / "order.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), "alpha beta gamma")
        pdf.save(path)
    return SourceDocument("doc", "order.pdf", "order.pdf", path, "hash")


def test_v1_processor_returns_shared_build_result_without_artifacts(tmp_path: Path) -> None:
    source = _source(tmp_path)

    result = V1DocumentProcessor(700, 100).process(source, "build-1")

    assert result.pipeline_id == "v1"
    assert result.build_id == "build-1"
    assert result.artifact_stage is None
    assert result.stats.page_count == 1
    assert result.stats.character_count > 0
    assert result.stats.chunk_count == len(result.chunks) == 1
    assert result.chunks[0].document_id == source.document_id


def test_build_result_rejects_chunk_count_mismatch(tmp_path: Path) -> None:
    source = _source(tmp_path)
    with pytest.raises(ValueError, match="chunk_count"):
        DocumentBuildResult(
            source, "v1", "build", (), DocumentBuildStats(1, 1, 1), None
        )
