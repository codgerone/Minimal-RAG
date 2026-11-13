"""Process one PDF: classify pages → (OCR scanned pages → text-layer PDF) → parse → document language → (extract → select → merge continuations → rule
header → table structure → table text → index text) → assemble."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from rag.ingest.assemble import compose_document
from rag.ingest.language import primary_language
from rag.ingest.ocr.models import DocumentPages
from rag.ingest.ocr.pages import classify_pages
from rag.ingest.parsers import ParseResult, Parser
from rag.ingest.tables.extractors import TableExtractor
from rag.ingest.tables.formatters import TableFormatter
from rag.ingest.tables.markdown_rows import serialize_grid_rows
from rag.ingest.timing import StepTimes
from rag.ingest.tables.models import (
    AdoptedTable, ContentResolution, ContinuationReport, GroupingReport, PreparedTableContent,
    ScoringReport, TableExtractionReport, TableIndexText,
)
from rag.ingest.tables.structure import TableStructurer
from rag.ingest.tables.pdf_words import PyMuPdfEvidenceReader
from rag.ingest.tables.prepare import prepare_tables
from rag.ingest.tables.select import select_tables
from rag.models import ParsedDocument, SourceDocument


@dataclass(frozen=True)
class TableSteps:
    """How the adopted tables become indexed text: structure, written form, what the index holds.
    `summarizer` (rag/ingest/tables/llm_tables.py LLMSummary) is set when `index_text` is "summary"."""
    structurer: TableStructurer
    formatter: TableFormatter
    index_text: TableIndexText
    summarizer: object | None = None


@dataclass(frozen=True)
class ProcessedDocument:
    """Everything one document's processing produced, kept for audit reports.
    `source` carries the text-layer PDF as its parse file when the document was OCR'd."""
    source: SourceDocument
    parse: ParseResult
    extraction_reports: tuple[TableExtractionReport, ...]
    resolutions: tuple[ContentResolution, ...]
    grouping: GroupingReport | None
    scoring: ScoringReport | None
    prepared_tables: tuple[PreparedTableContent, ...]
    continuations: ContinuationReport | None
    document: ParsedDocument
    pages: DocumentPages

    @property
    def has_table_branch(self) -> bool:
        return bool(self.extraction_reports)


def process_document(source: SourceDocument, parser: Parser,
                     extractors: tuple[TableExtractor, ...],
                     table_steps: TableSteps | None, ocr: object | None = None,
                     progress: Callable[[str], None] = lambda message: None,
                     times: StepTimes | None = None, cache: object | None = None) -> ProcessedDocument:
    """`ocr` is an OcrStep (rag/ingest/ocr/step.py); without it scanned pages stay unread.
    `times` collects the wall-clock time of each step and whether it was read from `cache`
    (rag/index/step_cache.py StepCache), which keeps the parser's raw output and each table
    extractor's report."""
    times = times if times is not None else StepTimes()
    with times.step("pages_ocr"):
        if ocr is not None:
            source, pages = ocr.prepare(source, progress)  # type: ignore[attr-defined]
        else:
            pages = DocumentPages(classify_pages(source.absolute_path), None)
    with times.step("parse"):
        parsed = parser.parse(source, cache)
    primary = parsed.primary
    language = primary_language(primary)
    reports_list = []
    for extractor in extractors:
        with times.step(f"extract_{extractor.tool}"):
            reports_list.append(_extract(extractor, source, parsed, cache))
    reports = tuple(reports_list)
    with times.step("select_tables"):
        selected = _select(source, primary, reports)
    resolutions, grouping, scoring, adopted, warnings, continuations = selected
    prepared: tuple[PreparedTableContent, ...] = ()
    if adopted:
        assert table_steps is not None
        with times.step("table_text"):
            prepared = table_texts(primary, adopted, language, table_steps)
    document = compose_document(source, primary, resolutions, prepared,
                                bool(primary.table_slots), language, warnings)
    return ProcessedDocument(source, parsed, reports, resolutions, grouping, scoring,
                             prepared, continuations, document, pages)


def _extract(extractor: TableExtractor, source: SourceDocument, parsed: ParseResult,
             cache: object | None) -> TableExtractionReport:
    """The Docling extractor reads the parser's conversion (cached with the parse); every other
    extractor reads the PDF, so its report is cached under the PDF, the source and its identity."""
    if cache is None or extractor.tool == "docling":
        return extractor.extract(source, parsed.shared)
    from rag.jsonio import from_jsonable, to_jsonable
    key = {"tool": extractor.tool, "pdf_sha256": source.pdf_hash, "file_hash": source.file_hash,
           "relative_path": source.relative_path, "identity": extractor.identity()}
    report, _ = cache.get_or_compute(  # type: ignore[attr-defined]
        f"extract_{extractor.tool}", key, lambda: extractor.extract(source, parsed.shared),
        to_jsonable, lambda value: from_jsonable(TableExtractionReport, value))
    return report


def _select(source: SourceDocument, primary, reports: tuple[TableExtractionReport, ...]):
    """Choose each table's content: select among extractor candidates, or keep the parser's own."""
    if reports:
        resolutions, grouping, scoring = select_tables(source, primary, reports,
                                                       PyMuPdfEvidenceReader())
        warnings = tuple(w for report in reports for w in report.warnings)
        warnings += grouping.warnings + scoring.warnings
        adopted, continuations = prepare_tables(primary, reports, resolutions, True)
    elif primary.table_slots:
        # No extractors configured: every table keeps the parser's own structure.
        resolutions = tuple(ContentResolution(slot.slot_id, "docling_native_fallback",
                                              None, None, "no_table_extractors")
                            for slot in primary.table_slots)
        grouping, scoring, warnings = None, None, ()
        adopted, continuations = prepare_tables(primary, (), resolutions, True)
    else:
        resolutions, grouping, scoring, adopted, warnings = (), None, None, (), ()
        continuations = None
    return resolutions, grouping, scoring, adopted, warnings, continuations


def table_texts(primary, adopted: tuple[AdoptedTable, ...], language: str,
                 steps: TableSteps) -> tuple[PreparedTableContent, ...]:
    from rag.models import PrimaryTablePlaceholder
    node_ids = {element.slot_id: element.element_id for element in primary.elements
                if isinstance(element, PrimaryTablePlaceholder)}
    structures = steps.structurer.structure(primary, adopted, language)
    summaries = (steps.summarizer.summarize(primary, adopted, language)  # type: ignore[attr-defined]
                 if steps.index_text == "summary" else (None,) * len(adopted))
    out = []
    for item, (structure, structure_llm), summary_llm in zip(adopted, structures, summaries):
        node_id = node_ids[item.slot_id]
        table = item.adopted_table
        out.append(PreparedTableContent(
            item.slot_id, item.table_id, table, item.header_decision, structure,
            steps.formatter.format(table, structure, table_node_id=node_id),
            serialize_grid_rows(table, table_node_id=node_id).text,
            steps.index_text, item.segments, summary_llm or structure_llm))
    return tuple(out)
