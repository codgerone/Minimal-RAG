"""执行 Docling 的本地 PDF 版面分析。"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
import hashlib
from pathlib import Path
import shutil
import tempfile
from time import perf_counter

from experiments.table_extraction.infrastructure.extractors.docling.models import DoclingRunResult


BASELINE_CONFIGURATION = {
    "do_ocr": False,
    "do_table_structure": True,
    "table_structure_mode": "accurate",
    "generate_page_images": True,
    "do_cell_matching": True,
}


def docling_version() -> str | None:
    """返回已安装 Docling 的版本；未安装时返回空值。"""
    try:
        return version("docling")
    except PackageNotFoundError:
        return None


def requires_ascii_staging(pdf_path: Path) -> bool:
    """判断 Docling 后端是否需要使用纯英文临时路径。"""
    return not str(pdf_path).isascii()


def staged_pdf_path(pdf_path: Path) -> tuple[tempfile.TemporaryDirectory[str] | None, Path]:
    """为非 ASCII 路径创建纯英文 PDF 临时副本，返回清理器与实际输入路径。"""
    if not requires_ascii_staging(pdf_path):
        return None, pdf_path

    project_root = Path(__file__).resolve().parents[5]
    staging_root = project_root / "experiments" / ".tmp"
    staging_root.mkdir(parents=True, exist_ok=True)
    temporary_directory = tempfile.TemporaryDirectory(prefix="docling-input-", dir=staging_root)
    source_hash = hashlib.sha256(str(pdf_path).encode("utf-8")).hexdigest()[:16]
    staged_path = Path(temporary_directory.name) / f"input-{source_hash}.pdf"
    shutil.copy2(pdf_path, staged_path)
    return temporary_directory, staged_path


def run_conversion(pdf_path: Path) -> DoclingRunResult:
    """按固定数字型 PDF 基线配置执行 Docling 转换。"""
    started_at = perf_counter()
    metadata = {
        "configuration": BASELINE_CONFIGURATION,
        "docling_version": docling_version(),
        "input_staged_for_unicode_path": requires_ascii_staging(pdf_path),
    }
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError:
        return DoclingRunResult(
            status="failed",
            error="Docling 未安装。请先运行 uv sync。",
            run_metadata={**metadata, "elapsed_seconds": round(perf_counter() - started_at, 3)},
        )

    try:
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = False
        pipeline_options.do_table_structure = True
        pipeline_options.table_structure_options.mode = TableFormerMode.ACCURATE
        pipeline_options.generate_page_images = True
        pipeline_options.table_structure_options.do_cell_matching = True
        converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
        )
        temporary_directory, input_path = staged_pdf_path(pdf_path)
        try:
            document, page_results, attempts, snapshots = convert_with_page_recovery(converter, input_path)
        finally:
            if temporary_directory is not None:
                temporary_directory.cleanup()
    except Exception as error:  # 模型下载、推理和 PDF 后端的异常类型并不统一。
        return DoclingRunResult(
            status="failed",
            error=f"{type(error).__name__}: {error}",
            run_metadata={**metadata, "elapsed_seconds": round(perf_counter() - started_at, 3)},
        )

    return DoclingRunResult(
        status="success" if document is not None else "failed",
        error=None if document is not None else "No page completed",
        document=document,
        attempt_documents=snapshots,
        run_metadata={
            **metadata,
            "elapsed_seconds": round(perf_counter() - started_at, 3),
            "page_count": len(page_results),
            "page_results": page_results,
            "attempt_errors": attempts,
            "table_count": len(document.tables) if document else 0,
        },
    )


def convert_with_page_recovery(converter, source_pdf):
    """Keep full-document structure; isolate failed pages without losing later pages."""
    from experiments.table_extraction.infrastructure.pdf.pages import requested_pages
    from docling_core.types.doc import DoclingDocument
    page_numbers = requested_pages(source_pdf)
    attempts = []
    snapshots = []
    successful_pages = set()
    partial_document = None
    try:
        result = converter.convert(source_pdf, raises_on_error=False)
        if getattr(result.status, 'value', result.status) == 'success':
            return result.document, [{"page_number": n, "status": "success", "error": None}
                                     for n in page_numbers], attempts, snapshots
        attempts.extend(str(error) for error in result.errors)
        snapshots.append(result.document.export_to_dict())
        failed = {error.page_no for error in result.errors if getattr(error, 'page_no', None)}
        if (getattr(result.status, 'value', result.status) == 'partial_success'
                and failed and all(getattr(error, 'page_no', None) for error in result.errors)):
            partial_document = result.document
            successful_pages = set(result.document.pages) - failed
    except Exception as error:
        attempts.append(f"{type(error).__name__}: {error}")
    # Preserve contiguous confirmed-successful spans; retain the entire partial raw
    # document separately, including cross-page facts that touch a failed page.
    docs = []
    original_pages = []
    pages = []
    consumed = set()
    for number in page_numbers:
        if number in consumed:
            continue
        if number in successful_pages:
            span = [number]
            while span[-1] + 1 in successful_pages:
                span.append(span[-1] + 1)
            docs.append(partial_document.filter(page_nrs=set(span)))
            original_pages.extend(span)
            pages.extend({'page_number': n, 'status': 'success', 'error': None} for n in span)
            consumed.update(span)
            continue
        try:
            result = converter.convert(source_pdf, page_range=(number, number), raises_on_error=False)
            if getattr(result.status, 'value', result.status) != 'success':
                raise RuntimeError('; '.join(str(error) for error in result.errors) or str(result.status))
            doc = result.document
            docs.append(doc)
            original_pages.extend([number] * len(doc.pages))
            pages.append({"page_number": number, "status": "success", "error": None})
        except Exception as error:
            pages.append({"page_number": number, "status": "failed",
                          "error": f"{type(error).__name__}: {error}"})
    if not docs:
        return None, pages, attempts, snapshots
    merged = DoclingDocument.concatenate(docs)
    mapping = dict(zip(sorted(merged.pages), original_pages, strict=True))
    # Concatenation normalizes references and page indices; restore source PDF numbering.
    payload = merged.export_to_dict()
    def remap(value):
        if isinstance(value, dict):
            if 'page_no' in value and value['page_no'] in mapping:
                value['page_no'] = mapping[value['page_no']]
            for child in value.values(): remap(child)
        elif isinstance(value, list):
            for child in value: remap(child)
    remap(payload)
    payload['pages'] = {str(mapping[int(key)]): value for key, value in payload['pages'].items()}
    return DoclingDocument.model_validate(payload), pages, attempts, snapshots
