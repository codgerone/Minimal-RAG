"""Docling layout parsing; the conversion is shared with the Docling table extractor."""

from __future__ import annotations

import json
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Callable

import fitz

from rag.ingest.parsers import ParseResult
from rag.ingest.parsers.docling_mapper import map_docling_document
from rag.ingest.tables.docling_tables import normalize_document_tables
from rag.ingest.tables.models import PageExecution, StrategyExecution, TableExtractionReport
from rag.models import SourceDocument


class DoclingParseError(RuntimeError):
    pass


@dataclass(frozen=True)
class DoclingConversion:
    """The SDK document stays inside this per-PDF object and never leaks into models."""
    source: SourceDocument
    page_sizes: dict[int, tuple[float, float]]
    document: Any

    def table_report(self) -> TableExtractionReport:
        candidates = normalize_document_tables(self.document, self.page_sizes)
        by_page: dict[int, list[str]] = {page: [] for page in sorted(self.page_sizes)}
        for candidate in candidates:
            pages = [region.page_number for region in candidate.regions
                     if region.page_number in by_page]
            if pages and len(pages) == len(candidate.regions) and len(set(pages)) == 1:
                by_page[pages[0]].append(candidate.candidate_id)
        pages = tuple(PageExecution(page, "completed", tuple(by_page[page]), None, None)
                      for page in sorted(by_page))
        ids = tuple(candidate.candidate_id for candidate in candidates)
        execution = StrategyExecution("docling", "accurate",
                                      "completed_with_tables" if ids else "completed_no_tables",
                                      pages, ids, None, None)
        return TableExtractionReport(self.source.relative_path, self.source.file_hash,
                                     (execution,), candidates, ())


def _convert(pdf_bytes: bytes) -> Any:
    from docling.datamodel.base_models import DocumentStream, InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions()
    options.do_ocr = False
    options.do_table_structure = True
    options.table_structure_options.mode = TableFormerMode.ACCURATE
    options.table_structure_options.do_cell_matching = True
    options.generate_page_images = False
    options.do_formula_enrichment = False
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
    with BytesIO(pdf_bytes) as stream:
        return converter.convert(DocumentStream(name="source.pdf", stream=stream)).document


class DoclingLayoutParser:
    provides_table_slots = True

    def __init__(self, converter: Callable[[bytes], Any] = _convert):
        self.converter = converter

    def parse(self, source: SourceDocument) -> ParseResult:
        pdf_bytes = source.absolute_path.read_bytes()
        with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
            page_sizes = {index + 1: (float(page.rect.width), float(page.rect.height))
                          for index, page in enumerate(pdf)}
        if not page_sizes:
            raise DoclingParseError(f"PDF 没有页面：{source.relative_path}")
        document = self.converter(pdf_bytes)
        native = json.loads(json.dumps(document.export_to_dict(), ensure_ascii=False))
        primary = map_docling_document(document, source, page_sizes)
        return ParseResult(primary, native, DoclingConversion(source, page_sizes, document))
