"""TableExtractor interface and the four built-in tools.

Each extractor reads the source PDF and returns every candidate table it finds,
with per-strategy and per-page execution facts. Selection happens later.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import fitz

from rag.ingest.parsers.docling_layout import DoclingConversion
from rag.ingest.tables import camelot_tables, pymupdf_tables, unstructured_tables
from rag.ingest.tables.extract import StrategyAdapter, run_extractor
from rag.ingest.tables.models import TableExtractionReport, TableStrategy, ToolName
from rag.models import SourceDocument


# Libraries each tool calls; part of the extractor identity.
LIBRARIES: dict[str, tuple[str, ...]] = {
    "pymupdf": ("pymupdf",),
    "camelot": ("camelot-py", "pypdfium2", "pdfminer.six", "opencv-python"),
    "unstructured": ("unstructured", "unstructured-inference", "pdfminer.six"),
    "docling": ("docling", "docling-core", "docling-ibm-models", "docling-parse"),
}


def _identity(tool: str, strategies: tuple[str, ...]) -> dict[str, object]:
    from rag.ingest.libraries import versions
    from rag.ingest.tables import extract, camelot_tables, docling_tables, pymupdf_tables, unstructured_tables
    module = {"pymupdf": pymupdf_tables, "camelot": camelot_tables, "unstructured": unstructured_tables,
              "docling": docling_tables}[tool]
    return {"tool": tool, "strategies": list(strategies),
            "rules": {"extract": extract.RULES_VERSION, tool: module.RULES_VERSION},
            "libraries": versions(*LIBRARIES[tool])}


class TableExtractor(Protocol):
    tool: ToolName

    def identity(self) -> dict[str, object]: ...

    def extract(self, source: SourceDocument, shared: object | None) -> TableExtractionReport: ...


@dataclass(frozen=True)
class PageStrategyExtractor:
    """Runs each configured strategy page by page; a failed page does not stop the others."""
    tool: ToolName
    strategies: tuple[TableStrategy, ...]
    adapters: dict[tuple[str, str], StrategyAdapter]

    def identity(self) -> dict[str, object]:
        return _identity(self.tool, self.strategies)

    def extract(self, source: SourceDocument, shared: object | None) -> TableExtractionReport:
        with fitz.open(source.pdf_path) as pdf:
            page_count = len(pdf)
        return run_extractor(source, page_count, tool=self.tool, strategies=self.strategies,
                             adapters={strategy: self.adapters[(self.tool, strategy)]
                                       for strategy in self.strategies})


class DoclingTableExtractor:
    """Reuses the parser's Docling conversion instead of converting the PDF twice."""
    tool: ToolName = "docling"

    def identity(self) -> dict[str, object]:
        return _identity(self.tool, ("accurate",))

    def extract(self, source: SourceDocument, shared: object | None) -> TableExtractionReport:
        if not isinstance(shared, DoclingConversion) or shared.source != source:
            raise ValueError("docling 表格提取器需要与 docling_layout 解析器搭配使用")
        return shared.table_report()


def pymupdf_extractor() -> PageStrategyExtractor:
    return PageStrategyExtractor("pymupdf", ("lines", "lines_strict", "text"),
                                 pymupdf_tables.make_adapters())


def camelot_extractor() -> PageStrategyExtractor:
    return PageStrategyExtractor("camelot", ("lattice", "stream", "network", "hybrid"),
                                 camelot_tables.make_adapters())


def unstructured_extractor() -> PageStrategyExtractor:
    work_root = Path(tempfile.gettempdir()) / "minimal-rag-unstructured"
    return PageStrategyExtractor("unstructured", ("hi_res",),
                                 {("unstructured", "hi_res"): unstructured_tables.make_adapter(work_root)})


def docling_extractor() -> DoclingTableExtractor:
    return DoclingTableExtractor()
