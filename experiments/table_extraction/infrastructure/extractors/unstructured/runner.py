"""执行 Unstructured 的本地 PDF 版面分析。"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from time import perf_counter

from experiments.table_extraction.infrastructure.extractors.unstructured.models import UnstructuredRunResult


BASELINE_CONFIGURATION = {
    "strategy": "hi_res",
    "infer_table_structure": True,
}


def unstructured_version() -> str | None:
    """返回已安装 Unstructured 的版本；未安装时返回空值。"""
    try:
        return version("unstructured")
    except PackageNotFoundError:
        return None


def run_partition(pdf_path: Path) -> UnstructuredRunResult:
    """按固定基线调用 partition_pdf，并保留原始 Element 列表。"""
    started_at = perf_counter()
    metadata = {
        "configuration": BASELINE_CONFIGURATION,
        "unstructured_version": unstructured_version(),
    }
    try:
        from unstructured.partition.pdf import partition_pdf
    except ImportError:
        return UnstructuredRunResult(
            status="failed",
            error="Unstructured 本地推理依赖未安装。请先运行 uv sync。",
            run_metadata={**metadata, "elapsed_seconds": round(perf_counter() - started_at, 3)},
        )

    from experiments.table_extraction.infrastructure.pdf.pages import requested_pages
    import fitz
    from tempfile import TemporaryDirectory
    try:
        page_numbers = requested_pages(pdf_path)
        document = fitz.open(pdf_path)
    except Exception as error:
        return UnstructuredRunResult(status="failed", error=str(error),
            run_metadata={**metadata, "startup_error": str(error)})
    elements = []
    page_results = []
    with document, TemporaryDirectory(prefix="unstructured-pages-") as temporary:
        for number in page_numbers:
            try:
                single = Path(temporary) / f"page-{number}.pdf"
                with fitz.open() as page_doc:
                    page_doc.insert_pdf(document, from_page=number-1, to_page=number-1)
                    page_doc.save(single)
                found = list(partition_pdf(filename=str(single), **BASELINE_CONFIGURATION))
                for element in found:
                    element.metadata.page_number = number
                    element.metadata.filename = pdf_path.name
                    element.metadata.file_directory = str(pdf_path.parent)
                elements.extend(found)
                page_results.append({"page_number": number, "status": "success", "error": None})
            except Exception as error:
                page_results.append({"page_number": number, "status": "failed",
                                     "error": f"{type(error).__name__}: {error}"})
    usable = any(page['status'] == 'success' for page in page_results)
    return UnstructuredRunResult(status="success" if usable else "failed", elements=elements,
        error=None if usable else "No page completed",
        run_metadata={**metadata, "page_results": page_results,
                      "elapsed_seconds": perf_counter()-started_at, "element_count": len(elements)})
