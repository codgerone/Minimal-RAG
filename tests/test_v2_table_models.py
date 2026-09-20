from dataclasses import replace

import pytest

from rag.v2.common import BoundingBox, validate_bbox
from rag.v2.table_models import (
    ExtractionReport, GridPosition, PageExecution, StrategyExecution,
    TableCandidate, TableCell, TableRegion,
)


def _cell() -> TableCell:
    return TableCell(
        "cell", "value", 0, 1, 0, 1, 1, 1, None, ("body",), "native", ("raw",)
    )


def _candidate() -> TableCandidate:
    return TableCandidate(
        "pymupdf_lines_p0001_t0001", "pymupdf", "lines", "raw",
        (TableRegion(1, 100.0, 100.0, BoundingBox(0, 0, 10, 10), "raw", None),),
        1, 1, (0.0, 10.0), (0.0, 10.0), (_cell(),), (), None, (),
    )


def test_bbox_clips_only_coordinate_epsilon() -> None:
    clipped = validate_bbox(BoundingBox(-0.0000005, 0, 100.0000005, 10), 100, 100)
    assert clipped == BoundingBox(0, 0, 100, 10)
    assert validate_bbox(BoundingBox(-0.001, 0, 10, 10), 100, 100) is None


def test_strategy_execution_distinguishes_page_failure_and_success() -> None:
    execution = StrategyExecution(
        "pymupdf", "lines", "completed_with_page_failures",
        (
            PageExecution(1, "failed", (), "RuntimeError", "failed"),
            PageExecution(2, "completed", ("candidate",), None, None),
        ),
        ("candidate",), None, None,
    )
    assert execution.candidate_ids == ("candidate",)


def test_illegal_tool_strategy_is_rejected() -> None:
    with pytest.raises(ValueError, match="非法工具"):
        replace(_candidate(), strategy="lattice")


def test_candidate_grid_requires_complete_nonoverlapping_coverage() -> None:
    candidate = _candidate()
    report = ExtractionReport(
        "order.pdf", "hash",
        (StrategyExecution(
            "pymupdf", "lines", "completed_with_tables",
            (PageExecution(1, "completed", (candidate.candidate_id,), None, None),),
            (candidate.candidate_id,), None, None,
        ),),
        (candidate,), (),
    )
    assert report.candidates == (candidate,)

    with pytest.raises(ValueError, match="完整且互斥"):
        replace(candidate, cells=(), uncovered_grid_positions=())


def test_unavailable_cell_rejects_partial_offsets() -> None:
    with pytest.raises(ValueError, match="同时存在或同时为空"):
        replace(_cell(), end_row_offset_idx=None)


def test_logical_grid_can_exist_without_unreliable_page_boundaries() -> None:
    candidate = replace(_candidate(), x_boundaries=None, y_boundaries=None)
    assert candidate.row_count == 1
    assert candidate.x_boundaries is None
