"""Nine-strategy extraction orchestration with page-level failure isolation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from rag.build_config import TableStrategy, ToolName
from rag.v2.common import ProcessingWarning
from rag.v2.table_models import (
    ExtractionReport, PageExecution, StrategyExecution, TableCandidate,
)


ExtractPage = Callable[[Path, int], tuple[TableCandidate, ...]]


@dataclass(frozen=True)
class StrategyAdapter:
    tool: ToolName
    strategy: TableStrategy
    extract_page: ExtractPage


def run_strategy(
    source_pdf: Path,
    page_count: int,
    adapter: StrategyAdapter | None,
    *,
    tool: ToolName,
    strategy: TableStrategy,
    initialization_error: Exception | None = None,
) -> tuple[StrategyExecution, tuple[TableCandidate, ...], tuple[ProcessingWarning, ...]]:
    if adapter is None:
        message = str(initialization_error or "adapter unavailable")
        execution = StrategyExecution(
            tool, strategy, "not_started", (), (),
            type(initialization_error).__name__ if initialization_error else "AdapterUnavailable",
            message,
        )
        warning = ProcessingWarning(
            "optional_tool_unavailable", "table_extraction", message, (),
        )
        return execution, (), (warning,)
    if page_count <= 0:
        raise ValueError("page_count 必须为正数。")
    if adapter.tool != tool or adapter.strategy != strategy:
        raise ValueError("StrategyAdapter 身份与注册键不一致。")

    pages: list[PageExecution] = []
    candidates: list[TableCandidate] = []
    warnings: list[ProcessingWarning] = []
    for page_number in range(1, page_count + 1):
        try:
            page_candidates = adapter.extract_page(source_pdf, page_number)
            if any(
                candidate.tool != tool or candidate.strategy != strategy
                for candidate in page_candidates
            ):
                raise ValueError("适配器返回了错误工具或策略身份的候选。")
            identifiers = tuple(item.candidate_id for item in page_candidates)
            if len(identifiers) != len(set(identifiers)):
                raise ValueError("适配器在同页返回重复 candidate_id。")
            pages.append(PageExecution(page_number, "completed", identifiers, None, None))
            candidates.extend(page_candidates)
        except Exception as exc:
            pages.append(PageExecution(
                page_number, "failed", (), type(exc).__name__, str(exc),
            ))
            warnings.append(ProcessingWarning(
                "optional_tool_page_failed", "table_extraction",
                f"{tool}/{strategy} 第 {page_number} 页失败：{type(exc).__name__}: {exc}",
                (f"page:{page_number}",),
            ))
    completed = tuple(item for item in pages if item.status == "completed")
    failed = tuple(item for item in pages if item.status == "failed")
    if not completed:
        status = "failed"
        error_type, error_message = "AllPagesFailed", "所有页面均执行失败。"
    elif failed:
        status = "completed_with_page_failures"
        error_type = error_message = None
    elif candidates:
        status = "completed_with_tables"
        error_type = error_message = None
    else:
        status = "completed_no_tables"
        error_type = error_message = None
    execution = StrategyExecution(
        tool, strategy, status, tuple(pages),
        tuple(item.candidate_id for item in candidates), error_type, error_message,
    )
    return execution, tuple(candidates), tuple(warnings)


def run_extraction(
    source_pdf: Path,
    file_hash: str,
    page_count: int,
    adapters: dict[tuple[ToolName, TableStrategy], StrategyAdapter | Exception],
) -> ExtractionReport:
    configured: tuple[tuple[ToolName, TableStrategy], ...] = (
        ("pymupdf", "lines"), ("pymupdf", "lines_strict"), ("pymupdf", "text"),
        ("camelot", "lattice"), ("camelot", "stream"),
        ("camelot", "network"), ("camelot", "hybrid"),
        ("docling", "accurate"), ("unstructured", "hi_res"),
    )
    executions: list[StrategyExecution] = []
    candidates: list[TableCandidate] = []
    warnings: list[ProcessingWarning] = []
    for tool, strategy in configured:
        configured_adapter = adapters.get((tool, strategy))
        adapter = configured_adapter if isinstance(configured_adapter, StrategyAdapter) else None
        error = configured_adapter if isinstance(configured_adapter, Exception) else None
        execution, produced, produced_warnings = run_strategy(
            source_pdf, page_count, adapter, tool=tool, strategy=strategy,
            initialization_error=error,
        )
        executions.append(execution)
        candidates.extend(produced)
        warnings.extend(produced_warnings)
    return ExtractionReport(
        source_pdf.as_posix(), file_hash, tuple(executions), tuple(candidates), tuple(warnings)
    )
