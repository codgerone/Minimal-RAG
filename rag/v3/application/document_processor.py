"""Connect parser, bound extractors, selector and assembler for one PDF."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from rag.v3.application.process_plugins import (
    DocumentAssemblerPlugin, DocumentComposerPlugin, HeaderDetectorPlugin,
    TableContentPreparationPlugin, TableSelectorPlugin, TableSerializerPlugin,
)
from rag.v3.contracts.documents import PrimaryDocument, SourceDocument
from rag.v3.contracts.processing import DocumentRequest, NativeParserEvidence, ProcessingResult
from rag.v3.contracts.tables import TableExtractionReport


@dataclass(frozen=True)
class MainParseExecution:
    primary: PrimaryDocument
    native_evidence: NativeParserEvidence
    resource: object | None = None


class MainParser(Protocol):
    def parse(self, source: SourceDocument) -> MainParseExecution: ...


class TableExtractor(Protocol):
    def extract(self, source: SourceDocument, resource: object | None) -> TableExtractionReport: ...


class DocumentProcessor:
    def __init__(self, main_parser: MainParser,
                 extractors: tuple[TableExtractor, ...] = (),
                 selector: TableSelectorPlugin | None = None,
                 assembler: DocumentAssemblerPlugin | None = None) -> None:
        self.main_parser = main_parser
        self.extractors = extractors
        if extractors and selector is None:
            raise ValueError("table selector must be bound with a PDF evidence reader")
        self.selector = selector
        self.assembler = assembler or DocumentAssemblerPlugin(
            TableContentPreparationPlugin(HeaderDetectorPlugin(), TableSerializerPlugin())
            if extractors else None, DocumentComposerPlugin())

    def process(self, request: DocumentRequest) -> ProcessingResult:
        if request.table_branch_attached != bool(self.extractors):
            raise ValueError("request table branch disagrees with validated assembly")
        parsed = self.main_parser.parse(request.source)
        primary = parsed.primary
        if (primary.document_id != request.source.document_id
                or primary.file_hash != request.source.file_hash):
            raise ValueError("main parser returned another source identity")
        if parsed.native_evidence.parser_plugin_id == "":
            raise ValueError("main parser evidence identity absent")
        reports = tuple(extractor.extract(request.source, parsed.resource)
                        for extractor in self.extractors)
        if reports:
            if self.selector is None:
                raise ValueError("table selector binding missing")
            resolutions, grouping, scoring = self.selector.select(request.source, primary, reports)
            stage_warnings = tuple(warning for report in reports for warning in report.warnings)
            stage_warnings += grouping.warnings + scoring.warnings
        else:
            resolutions, grouping, scoring = (), None, None
            stage_warnings = ()
        document, prepared = self.assembler.assemble(
            request.source, primary, reports, resolutions,
            request.table_branch_attached, stage_warnings)
        return ProcessingResult(
            request.source, request.index_identity, request.build_id,
            request.table_branch_attached, parsed.native_evidence, primary,
            reports, resolutions, grouping, scoring, prepared, document,
            document.warnings,
        )
