"""One-pass PyMuPDF page parser and native audit evidence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import pymupdf

from rag.v3.adapters.local_registry import _hash_file
from rag.v3.contracts.documents import PageSpan, PrimaryDocument, PrimaryText, SourceDocument
from rag.v3.contracts.processing import (
    NativeParserEvidence, PyMuPDFNativeBlock, PyMuPDFNativePage, PyMuPDFNativePayload,
)


class MainParserError(ValueError):
    def __init__(self, code: Literal["source_changed", "pdf_unreadable", "pdf_no_pages",
                                     "no_usable_content", "native_schema_invalid"], detail: str):
        self.code = code
        super().__init__(detail)


def clean_page_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line.rstrip()) for line in normalized.split("\n")]
    cleaned = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


@dataclass(frozen=True)
class MainParseResult:
    primary: PrimaryDocument
    native: NativeParserEvidence


class PyMuPDFPagesParser:
    plugin_id = "parser.pymupdf_pages"

    def parse(self, source: SourceDocument) -> MainParseResult:
        if _hash_file(source.absolute_path) != source.file_hash:
            raise MainParserError("source_changed", source.relative_path)
        elements: list[PrimaryText] = []
        native_pages: list[PyMuPDFNativePage] = []
        try:
            with pymupdf.open(source.absolute_path) as pdf:
                page_count = len(pdf)
                if page_count == 0:
                    raise MainParserError("pdf_no_pages", source.relative_path)
                for page_number, page in enumerate(pdf, start=1):
                    raw_blocks = page.get_text("blocks", sort=True)
                    blocks: list[PyMuPDFNativeBlock] = []
                    for raw in raw_blocks:
                        if len(raw) != 7:
                            raise MainParserError("native_schema_invalid", source.relative_path)
                        blocks.append(PyMuPDFNativeBlock(
                            float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]),
                            str(raw[4]), int(raw[5]), int(raw[6])))
                    native_pages.append(PyMuPDFNativePage(page_number, tuple(blocks)))
                    text = clean_page_text("\n".join(block.text for block in blocks if block.text.strip()))
                    if text:
                        elements.append(PrimaryText(
                            f"page-{page_number}", "paragraph", text,
                            (PageSpan(page_number, None, f"page:{page_number}"),), None))
        except MainParserError:
            raise
        except Exception as exc:
            raise MainParserError("pdf_unreadable", source.relative_path) from exc
        if _hash_file(source.absolute_path) != source.file_hash:
            raise MainParserError("source_changed", source.relative_path)
        if not elements:
            raise MainParserError("no_usable_content", source.relative_path)
        primary = PrimaryDocument(source.document_id, source.file_hash, page_count,
                                  tuple(elements), (), (), ())
        native = NativeParserEvidence(self.plugin_id, "pymupdf_pages_v1",
                                      PyMuPDFNativePayload(tuple(native_pages)))
        return MainParseResult(primary, native)
