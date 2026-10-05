"""Built-in parser and extractor instances behind V3 processor ports."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path

from rag.v3.adapters import camelot_tables, pymupdf_tables, unstructured_tables
from rag.v3.adapters.docling_parser import DoclingConversionScope, open_docling_scope
from rag.v3.adapters.pymupdf_pages import PyMuPDFPagesParser
from rag.v3.application.document_processor import MainParseExecution
from rag.v3.application.table_extraction import StrategyAdapter, run_extractor
from rag.v3.contracts.documents import SourceDocument
from rag.v3.contracts.tables import TableExtractionReport, TableStrategy, ToolName


class PlainPageMainParser:
    def __init__(self) -> None:
        self.parser = PyMuPDFPagesParser()

    def parse(self, source: SourceDocument) -> MainParseExecution:
        parsed = self.parser.parse(source)
        return MainParseExecution(parsed.primary, parsed.native)


class DoclingLayoutMainParser:
    def parse(self, source: SourceDocument) -> MainParseExecution:
        scope = open_docling_scope(source)
        return MainParseExecution(scope.primary, scope.native_evidence, scope)


@dataclass(frozen=True)
class PageStrategyExtractor:
    tool: ToolName
    strategies: tuple[TableStrategy, ...]
    adapters: dict[tuple[str, str], StrategyAdapter]

    def extract(self, source: SourceDocument, resource: object | None) -> TableExtractionReport:
        import fitz

        if hashlib.sha256(source.absolute_path.read_bytes()).hexdigest() != source.file_hash:
            raise ValueError("table extractor source changed before execution")
        with fitz.open(source.absolute_path) as pdf:
            page_count = len(pdf)
        report = run_extractor(source, page_count, tool=self.tool,
                               strategies=self.strategies,
                               adapters={strategy: self.adapters[(self.tool, strategy)]
                                         for strategy in self.strategies})
        if hashlib.sha256(source.absolute_path.read_bytes()).hexdigest() != source.file_hash:
            raise ValueError("table extractor source changed during execution")
        return report


class DoclingTableExtractor:
    def extract(self, source: SourceDocument, resource: object | None) -> TableExtractionReport:
        if not isinstance(resource, DoclingConversionScope) or resource.source != source:
            raise ValueError("Docling extractor requires the matching main parse resource")
        return resource.table_report()


def pymupdf_extractor() -> PageStrategyExtractor:
    return PageStrategyExtractor("pymupdf", ("lines", "lines_strict", "text"),
                                 pymupdf_tables.make_adapters())


def camelot_extractor() -> PageStrategyExtractor:
    return PageStrategyExtractor("camelot", ("lattice", "stream", "network", "hybrid"),
                                 camelot_tables.make_adapters())


def unstructured_extractor(work_root: Path) -> PageStrategyExtractor:
    adapter = unstructured_tables.make_adapter(work_root)
    return PageStrategyExtractor("unstructured", ("hi_res",),
                                 {("unstructured", "hi_res"): adapter})
