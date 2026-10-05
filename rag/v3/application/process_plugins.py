"""Built-in selector, table preparation and document assembly port instances."""

from __future__ import annotations

from rag.v3.application.document_composer import compose_document
from rag.v3.application.table_content import prepare_tables
from rag.v3.application.table_header import detect_header
from rag.v3.application.table_selector import PdfEvidencePort, select_tables
from rag.v3.application.table_serialization import serialize_table
from rag.v3.contracts.documents import ParsedDocument, PrimaryDocument, SourceDocument
from rag.v3.contracts.tables import (
    ContentResolution, GroupingReport, HeaderDecision, PreparedTableContent,
    ScoringReport, SerializedTable, StructuredTable, TableExtractionReport,
)


class TableSelectorPlugin:
    def __init__(self, evidence: PdfEvidencePort):
        self.evidence = evidence

    def select(self, source: SourceDocument, primary: PrimaryDocument,
               reports: tuple[TableExtractionReport, ...]
               ) -> tuple[tuple[ContentResolution, ...], GroupingReport, ScoringReport]:
        return select_tables(source, primary, reports, self.evidence)


class HeaderDetectorPlugin:
    def __init__(self, sample_row_budget: int = 8,
                 minimum_independent_observations: int = 2):
        self.sample_row_budget = sample_row_budget
        self.minimum_independent_observations = minimum_independent_observations

    def detect(self, table: StructuredTable) -> HeaderDecision:
        return detect_header(table, sample_row_budget=self.sample_row_budget,
                             minimum_independent_observations=self.minimum_independent_observations)


class TableSerializerPlugin:
    def serialize(self, table: StructuredTable, header: HeaderDecision,
                  *, table_node_id: str) -> SerializedTable:
        return serialize_table(table, header, table_node_id=table_node_id)


class TableContentPreparationPlugin:
    def __init__(self, header: HeaderDetectorPlugin, serializer: TableSerializerPlugin):
        self.header = header
        self.serializer = serializer

    def prepare(self, primary: PrimaryDocument,
                reports: tuple[TableExtractionReport, ...],
                resolutions: tuple[ContentResolution, ...],
                branch_attached: bool) -> tuple[PreparedTableContent, ...]:
        return prepare_tables(primary, reports, resolutions, branch_attached,
                              header_detector=self.header.detect,
                              serializer=self.serializer.serialize)


class DocumentComposerPlugin:
    def compose(self, source: SourceDocument, primary: PrimaryDocument,
                resolutions: tuple[ContentResolution, ...],
                prepared: tuple[PreparedTableContent, ...],
                branch_attached: bool, warnings) -> ParsedDocument:
        return compose_document(source, primary, resolutions, prepared,
                                branch_attached, warnings)


class DocumentAssemblerPlugin:
    def __init__(self, content: TableContentPreparationPlugin | None,
                 composer: DocumentComposerPlugin):
        self.content = content
        self.composer = composer

    def assemble(self, source: SourceDocument, primary: PrimaryDocument,
                 reports: tuple[TableExtractionReport, ...],
                 resolutions: tuple[ContentResolution, ...], branch_attached: bool,
                 warnings) -> tuple[ParsedDocument, tuple[PreparedTableContent, ...]]:
        if (self.content is None) != (not branch_attached):
            raise ValueError("content preparation attachment differs from document branch")
        prepared = (self.content.prepare(primary, reports, resolutions, branch_attached)
                    if self.content is not None else ())
        document = self.composer.compose(source, primary, resolutions, prepared,
                                         branch_attached, warnings)
        return document, prepared
