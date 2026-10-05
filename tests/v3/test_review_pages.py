from rag.v3.application.artifact_paths import (
    build_artifact_path, document_directory, valid_artifact_paths,
)
from rag.v3.application.review_pages import _table_grid
from rag.v3.contracts.storage import ManifestDocumentRecord
from rag.v3.contracts.tables import TableCandidate, TableCell


def test_review_grid_preserves_spans_empty_positions_and_unplaced_text() -> None:
    candidate = TableCandidate(
        candidate_id="candidate_1", tool="docling", strategy="accurate",
        source_ref="raw/document.json#/tables/0", regions=(), row_count=2,
        column_count=3, x_boundaries=None, y_boundaries=None,
        cells=(
            TableCell("merged", "A&B\nsecond", 0, 2, 0, 2, 2, 2, None, (),
                      "geometry_inferred", ()),
            TableCell("unplaced", "<raw>", None, None, None, None, None,
                      None, None, (), "unavailable", ()),
        ),
        uncovered_grid_positions=(), unplaced_text=None, warnings=(),
    )
    markup = _table_grid(candidate)
    assert markup.count("<tr>") == 2
    assert 'rowspan="2" colspan="2"' in markup
    assert 'A&amp;B<br>second' in markup
    assert markup.count('class="uncovered"') == 2
    assert '&lt;raw&gt;' in markup and '<raw>' not in markup


def test_new_review_path_is_short_and_historical_path_remains_valid() -> None:
    name = "Contrato N 105-2025 LP-002-2024-FONAFE Adquisición.pdf"
    directory = document_directory(name, "d359c37e0bc3c87a")
    assert directory == "Contrato--d359c37e0bc3c87a"
    record = ManifestDocumentRecord(
        document_id="d359c37e0bc3c87a", document_name=name, relative_path=name,
        file_hash="a" * 64, build_id="20260929073942-d8700a61b3d7-1ab42635df0a-ef305900",
        page_count=1, character_count=1, chunk_count=1,
        artifact_path=f"artifacts/documents/{directory}/20260929073942-d8700a61b3d7-1ab42635df0a-ef305900",
        artifact_files={"chunks": "a" * 64}, indexed_at="2026-09-29T00:00:00+00:00",
    )
    assert record.artifact_path == build_artifact_path(record)
    assert record.artifact_path in valid_artifact_paths(record)
    assert any("Contrato-N-105" in path for path in valid_artifact_paths(record))
