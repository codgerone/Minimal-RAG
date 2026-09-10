"""执行 Camelot 的各类表格 parser。"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from time import perf_counter
from typing import Any

from experiments.table_extraction.infrastructure.extractors.camelot.models import CamelotRunResult


SUPPORTED_FLAVORS = ("lattice", "stream", "network", "hybrid")


def camelot_version() -> str | None:
    """返回已安装 Camelot 的版本；未安装时返回空值。"""
    try:
        return version("camelot-py")
    except PackageNotFoundError:
        return None


def run_flavor(pdf_path: Path, flavor: str, pages: str) -> CamelotRunResult:
    """独立运行一种 parser，失败只影响当前 parser。"""
    started_at = perf_counter()
    metadata: dict[str, Any] = {
        "pages": pages,
        "read_pdf_arguments": {"flavor": flavor, "pages": pages},
        "camelot_version": camelot_version(),
    }

    try:
        import camelot
    except ImportError:
        return CamelotRunResult(
            flavor=flavor,
            status="failed",
            error="Camelot 未安装。请先运行 uv sync。",
            run_metadata={**metadata, "elapsed_seconds": round(perf_counter() - started_at, 3)},
        )

    from experiments.table_extraction.infrastructure.pdf.pages import requested_pages
    try:
        page_numbers = requested_pages(pdf_path, pages)
    except Exception as error:
        return CamelotRunResult(flavor, "failed", error=f"{type(error).__name__}: {error}",
                                run_metadata={**metadata, "startup_error": str(error)})
    tables = []
    page_results = []
    for number in page_numbers:
        try:
            arguments = {'copy_text': None, 'shift_text': ['l', 't']} if flavor in {'lattice', 'hybrid'} else {}
            page_tables = list(camelot.read_pdf(str(pdf_path), pages=str(number), flavor=flavor, **arguments))
            tables.extend(page_tables)
            page_results.append({"page_number": number, "status": "success", "error": None})
        except Exception as error:
            page_results.append({"page_number": number, "status": "failed",
                                 "error": f"{type(error).__name__}: {error}"})
    usable = any(page['status'] == 'success' for page in page_results)
    return CamelotRunResult(flavor, "success" if usable else "failed", tables=tables,
        error=None if usable else "No page completed",
        run_metadata={**metadata, "page_results": page_results,
                      "elapsed_seconds": perf_counter() - started_at, "table_count": len(tables)})


def run_flavors(pdf_path: Path, flavors: list[str], pages: str) -> list[CamelotRunResult]:
    """依次运行指定 parser，确保任一失败不会中断其余实验。"""
    return [run_flavor(pdf_path, flavor, pages) for flavor in flavors]
