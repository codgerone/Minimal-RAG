from dataclasses import replace

import pytest

from rag.v2.common import BoundingBox
from rag.v2.table_models import TableCandidate, TableCell, TableRegion
from rag.v2.table_scoring import (
    ReferenceWord, SlotTextReference, TokenCount, candidate_tokens,
    normalize_token, score_group,
)


def _candidate(identifier: str, tool="pymupdf", text="A 1’ 131", rows=1, columns=1):
    cell = TableCell(
        f"{identifier}_cell", text, 0, rows, 0, columns, rows, columns,
        None, ("body",), "native", ("raw",),
    )
    return TableCandidate(
        identifier, tool, "lines" if tool == "pymupdf" else "accurate", "raw",
        (TableRegion(1, 100, 100, BoundingBox(0, 0, 10, 10), "raw", None),),
        rows, columns, tuple(range(columns + 1)), tuple(range(rows + 1)),
        (cell,), (), None, (),
    )


def _reference(*tokens: str) -> SlotTextReference:
    counts = {}
    for token in tokens:
        counts[token] = counts.get(token, 0) + 1
    return SlotTextReference(
        "slot_001", 1, BoundingBox(0, 0, 10, 10), (),
        tuple(TokenCount(token, count) for token, count in sorted(counts.items())),
        tuple(TokenCount(token, count) for token, count in sorted(counts.items()) if any(c.isdecimal() for c in token)),
    )


def test_numeric_apostrophe_normalization_does_not_join_plain_spaces() -> None:
    assert normalize_token("1´ 131") == "1'131"
    assert normalize_token("1’131") == "1'131"
    assert normalize_token("5 00") == "5 00"


def test_candidate_tokens_preserve_duplicate_physical_cell_values() -> None:
    candidate = _candidate("a", text="x x")
    assert candidate_tokens(candidate)["x"] == 2


def test_single_candidate_gets_four_perfect_relative_scores() -> None:
    result = score_group("group_001", "slot_001", _reference("a", "1'131"), (_candidate("a"),))
    assert result.selected_candidate_id == "a"
    assert result.selection_reason == "highest_total_score"
    assert result.highest_total_score == 1.0
    assert [item.score for item in result.candidates[0].relative_scores] == [1.0] * 4


def test_equal_candidates_use_tool_then_candidate_id_tiebreak() -> None:
    pymupdf = _candidate("z", "pymupdf")
    docling = _candidate("a", "docling")
    result = score_group("group_001", "slot_001", _reference("a", "1'131"), (docling, pymupdf))
    assert result.selected_candidate_id == "z"
    assert result.selection_reason == "tool_priority_tiebreak"

    first = _candidate("a", "pymupdf")
    second = _candidate("b", "pymupdf")
    result = score_group("group_001", "slot_001", _reference("a", "1'131"), (second, first))
    assert result.selected_candidate_id == "a"
    assert result.selection_reason == "candidate_id_tiebreak"


def test_blank_reference_uses_unique_mode_else_minimum() -> None:
    base = _candidate("a", text="x", rows=1, columns=2)
    blank = replace(base, candidate_id="c", cells=(replace(base.cells[0], cell_id="c_cell", text=""),))
    same = replace(base, candidate_id="b", cells=(replace(base.cells[0], cell_id="b_cell"),))
    result = score_group("group_001", "slot_001", _reference("x"), (base, same, blank))
    metrics = {item.candidate_id: item.raw_metrics.blank_grid for item in result.candidates}
    assert metrics["a"].reference_source == "unique_mode"
    assert metrics["c"].blank_anomaly == pytest.approx(1.0)
