from pathlib import Path

from rag.v2.common import BoundingBox
from rag.v2.table_extraction import StrategyAdapter, run_extraction, run_strategy
from rag.v2.table_models import TableCandidate, TableCell, TableRegion


def _candidate(page: int) -> TableCandidate:
    identifier = f"pymupdf_lines_p{page:04d}_t0001"
    return TableCandidate(
        identifier, "pymupdf", "lines", "raw",
        (TableRegion(page, 100, 100, BoundingBox(0, 0, 10, 10), "raw", None),),
        1, 1, (0, 10), (0, 10),
        (TableCell(f"{identifier}_cell", "x", 0, 1, 0, 1, 1, 1, None, ("body",), "native", ("raw",)),),
        (), None, (),
    )


def test_page_failure_does_not_stop_later_pages() -> None:
    called = []

    def extract(_path: Path, page: int):
        called.append(page)
        if page == 1:
            raise RuntimeError("broken page")
        return (_candidate(page),)

    execution, candidates, warnings = run_strategy(
        Path("input.pdf"), 2, StrategyAdapter("pymupdf", "lines", extract),
        tool="pymupdf", strategy="lines",
    )
    assert called == [1, 2]
    assert execution.status == "completed_with_page_failures"
    assert execution.candidate_ids == (candidates[0].candidate_id,)
    assert warnings[0].code == "optional_tool_page_failed"


def test_successful_zero_tables_is_distinct_from_failure() -> None:
    adapter = StrategyAdapter("pymupdf", "lines", lambda _path, _page: ())
    execution, candidates, _ = run_strategy(
        Path("input.pdf"), 1, adapter, tool="pymupdf", strategy="lines"
    )
    assert execution.status == "completed_no_tables"
    assert candidates == ()


def test_all_nine_strategies_are_recorded_even_when_unavailable() -> None:
    report = run_extraction(Path("input.pdf"), "hash", 1, {})
    assert len(report.executions) == 9
    assert {item.status for item in report.executions} == {"not_started"}
    assert len(report.warnings) == 9

