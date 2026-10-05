"""Run configured table strategies in stable order with page failure isolation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from rag.models import ProcessingWarning, SourceDocument
from rag.ingest.tables.models import (
    PageExecution, StrategyExecution, TableCandidate, TableExtractionReport,
    TableStrategy, ToolName,
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
        return (
            StrategyExecution(tool, strategy, "not_started", (), (),
                              type(initialization_error).__name__ if initialization_error
                              else "AdapterUnavailable", message),
            (),
            (ProcessingWarning("optional_tool_unavailable", "table_extraction", message, ()),),
        )
    if page_count <= 0 or adapter.tool != tool or adapter.strategy != strategy:
        raise ValueError("invalid page count or strategy binding")
    pages: list[PageExecution] = []
    candidates: list[TableCandidate] = []
    warnings: list[ProcessingWarning] = []
    for page_number in range(1, page_count + 1):
        try:
            produced = adapter.extract_page(source_pdf, page_number)
            if any(candidate.tool != tool or candidate.strategy != strategy for candidate in produced):
                raise ValueError("strategy returned a candidate with another binding identity")
            ids = tuple(candidate.candidate_id for candidate in produced)
            if len(ids) != len(set(ids)):
                raise ValueError("duplicate candidate ID on one page")
            pages.append(PageExecution(page_number, "completed", ids, None, None))
            candidates.extend(produced)
        except Exception as exc:
            pages.append(PageExecution(page_number, "failed", (), type(exc).__name__, str(exc)))
            warnings.append(ProcessingWarning(
                "optional_tool_page_failed", "table_extraction",
                f"{tool}/{strategy} page {page_number}: {type(exc).__name__}: {exc}",
                (f"page:{page_number}",),
            ))
    completed = any(page.status == "completed" for page in pages)
    failed = any(page.status == "failed" for page in pages)
    if not completed:
        status, error_type, error_message = "failed", "AllPagesFailed", "all pages failed"
    elif failed:
        status, error_type, error_message = "completed_with_page_failures", None, None
    elif candidates:
        status, error_type, error_message = "completed_with_tables", None, None
    else:
        status, error_type, error_message = "completed_no_tables", None, None
    execution = StrategyExecution(tool, strategy, status, tuple(pages),
                                  tuple(candidate.candidate_id for candidate in candidates),
                                  error_type, error_message)
    return execution, tuple(candidates), tuple(warnings)


def run_extractor(
    source: SourceDocument,
    page_count: int,
    *,
    tool: ToolName,
    strategies: tuple[TableStrategy, ...],
    adapters: dict[TableStrategy, StrategyAdapter | Exception],
) -> TableExtractionReport:
    if not strategies or len(set(strategies)) != len(strategies):
        raise ValueError("extractor needs unique ordered strategies")
    executions: list[StrategyExecution] = []
    candidates: list[TableCandidate] = []
    warnings: list[ProcessingWarning] = []
    for strategy in strategies:
        configured = adapters.get(strategy)
        execution, produced, diagnostics = run_strategy(
            source.absolute_path, page_count,
            configured if isinstance(configured, StrategyAdapter) else None,
            tool=tool, strategy=strategy,
            initialization_error=configured if isinstance(configured, Exception) else None,
        )
        executions.append(execution)
        candidates.extend(produced)
        warnings.extend(diagnostics)
    if len({candidate.candidate_id for candidate in candidates}) != len(candidates):
        raise ValueError("duplicate candidate ID across strategies")
    return TableExtractionReport(source.relative_path, source.file_hash,
                                 tuple(executions), tuple(candidates), tuple(warnings))
