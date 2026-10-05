"""Process one PDF: parse → (extract → select → prepare tables) → assemble."""

from __future__ import annotations

from dataclasses import dataclass

from rag.ingest.assemble import compose_document
from rag.ingest.parsers import ParseResult, Parser
from rag.ingest.tables.extractors import TableExtractor
from rag.ingest.tables.formatters import TableFormatter
from rag.ingest.tables.models import (
    ContentResolution, GroupingReport, PreparedTableContent, ScoringReport,
    TableExtractionReport,
)
from rag.ingest.tables.pdf_words import PyMuPdfEvidenceReader
from rag.ingest.tables.prepare import prepare_tables
from rag.ingest.tables.select import select_tables
from rag.models import ParsedDocument, SourceDocument


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
    document: ParsedDocument

    @property
    def has_table_branch(self) -> bool:
        return bool(self.extraction_reports)


def process_document(source: SourceDocument, parser: Parser,
                     extractors: tuple[TableExtractor, ...],
                     formatter: TableFormatter | None) -> ProcessedDocument:
    parsed = parser.parse(source)
    primary = parsed.primary
    reports = tuple(extractor.extract(source, parsed.shared) for extractor in extractors)
    if reports:
        resolutions, grouping, scoring = select_tables(source, primary, reports,
                                                       PyMuPdfEvidenceReader())
        warnings = tuple(w for report in reports for w in report.warnings)
        warnings += grouping.warnings + scoring.warnings
        assert formatter is not None
        prepared = prepare_tables(primary, reports, resolutions, True,
                                  serializer=formatter.format)
    elif primary.table_slots:
        # No extractors configured: every table keeps the parser's own structure.
        resolutions = tuple(ContentResolution(slot.slot_id, "docling_native_fallback",
                                              None, None, "no_table_extractors")
                            for slot in primary.table_slots)
        grouping, scoring, warnings = None, None, ()
        assert formatter is not None
        prepared = prepare_tables(primary, (), resolutions, True, serializer=formatter.format)
    else:
        resolutions, grouping, scoring, prepared, warnings = (), None, None, (), ()
    document = compose_document(source, primary, resolutions, prepared,
                                bool(primary.table_slots), warnings)
    return ProcessedDocument(source, parsed, reports, resolutions, grouping, scoring,
                             prepared, document)
