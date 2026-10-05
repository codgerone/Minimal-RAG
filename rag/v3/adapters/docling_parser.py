"""Document-scoped Docling conversion shared by main parse and table extraction."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Callable

import fitz

from rag.v3.adapters.docling_mapper import map_docling_document
from rag.v3.adapters.docling_tables import normalize_document_tables
from rag.v3.contracts.documents import PrimaryDocument, SourceDocument
from rag.v3.contracts.processing import DoclingNativePayload, NativeParserEvidence
from rag.v3.contracts.tables import PageExecution, StrategyExecution, TableExtractionReport


class DoclingParseError(RuntimeError):
    """The configured source cannot yield one consistent Docling conversion."""


@dataclass(frozen=True)
class DoclingConversionScope:
    source: SourceDocument
    primary: PrimaryDocument
    native_evidence: NativeParserEvidence
    page_sizes: dict[int, tuple[float, float]]
    _document: Any  # SDK object stays inside this per-document resource scope.

    def table_report(self) -> TableExtractionReport:
        candidates = normalize_document_tables(self._document, self.page_sizes)
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


def _locked_convert(pdf_bytes: bytes) -> Any:
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
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )
    stream = BytesIO(pdf_bytes)
    try:
        return converter.convert(DocumentStream(name="source.pdf", stream=stream)).document
    finally:
        stream.close()


def open_docling_scope(
    source: SourceDocument,
    *,
    converter: Callable[[bytes], Any] = _locked_convert,
) -> DoclingConversionScope:
    pdf_bytes = source.absolute_path.read_bytes()
    if hashlib.sha256(pdf_bytes).hexdigest() != source.file_hash:
        raise DoclingParseError("source hash changed before Docling conversion")
    with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
        page_sizes = {index + 1: (float(page.rect.width), float(page.rect.height))
                      for index, page in enumerate(pdf)}
    if not page_sizes:
        raise DoclingParseError("PDF contains no physical pages")
    document = converter(pdf_bytes)
    if hashlib.sha256(source.absolute_path.read_bytes()).hexdigest() != source.file_hash:
        raise DoclingParseError("source hash changed during Docling conversion")
    exported = document.export_to_dict()
    if not isinstance(exported, dict):
        raise DoclingParseError("Docling native export is not a dictionary")
    try:
        payload = json.loads(json.dumps(exported, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise DoclingParseError("Docling native export is not finite JSON") from exc
    primary = map_docling_document(document, source, page_sizes)
    evidence = NativeParserEvidence("parser.docling_layout", "docling_document_v1",
                                    DoclingNativePayload(payload))
    return DoclingConversionScope(source, primary, evidence, page_sizes, document)
