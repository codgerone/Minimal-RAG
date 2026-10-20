"""Process one PDF: parse → document language → (extract → select → merge continuations → rule
header → table structure → table text → index text) → assemble."""

from __future__ import annotations

from dataclasses import dataclass

from rag.ingest.assemble import compose_document
from rag.ingest.language import primary_language
from rag.ingest.parsers import ParseResult, Parser
from rag.ingest.tables.extractors import TableExtractor
from rag.ingest.tables.formatters import TableFormatter
from rag.ingest.tables.markdown_rows import serialize_grid_rows
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
    """Everything one document's processing produced, kept for audit reports."""
    source: SourceDocument
    parse: ParseResult
    extraction_reports: tuple[TableExtractionReport, ...]
    resolutions: tuple[ContentResolution, ...]
    grouping: GroupingReport | None
    scoring: ScoringReport | None
    prepared_tables: tuple[PreparedTableContent, ...]
    continuations: ContinuationReport | None
    document: ParsedDocument

    @property
    def has_table_branch(self) -> bool:
        return bool(self.extraction_reports)


def process_document(source: SourceDocument, parser: Parser,
                     extractors: tuple[TableExtractor, ...],
                     table_steps: TableSteps | None) -> ProcessedDocument:
    parsed = parser.parse(source)
    primary = parsed.primary
    language = primary_language(primary)
    reports = tuple(extractor.extract(source, parsed.shared) for extractor in extractors)
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
    prepared: tuple[PreparedTableContent, ...] = ()
    if adopted:
        assert table_steps is not None
        prepared = table_texts(primary, adopted, language, table_steps)
    document = compose_document(source, primary, resolutions, prepared,
                                bool(primary.table_slots), language, warnings)
    return ProcessedDocument(source, parsed, reports, resolutions, grouping, scoring,
                             prepared, continuations, document)


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
