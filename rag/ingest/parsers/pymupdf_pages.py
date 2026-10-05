"""One text element per page, read with PyMuPDF; no OCR, no tables."""

from __future__ import annotations

import re

import pymupdf

from rag.ingest.parsers import ParseResult
from rag.models import PageSpan, PrimaryDocument, PrimaryText, SourceDocument


class ParseError(RuntimeError):
    pass


def clean_page_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line.rstrip()) for line in normalized.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


class PyMuPDFPagesParser:
    provides_table_slots = False

    def parse(self, source: SourceDocument) -> ParseResult:
        elements: list[PrimaryText] = []
        native_pages = []
        try:
            with pymupdf.open(source.absolute_path) as pdf:
                page_count = len(pdf)
                for page_number, page in enumerate(pdf, start=1):
                    blocks = page.get_text("blocks", sort=True)
                    native_pages.append({"page_number": page_number, "blocks": [
                        {"bbox": [float(b[0]), float(b[1]), float(b[2]), float(b[3])],
                         "text": str(b[4]), "block_no": int(b[5]), "block_type": int(b[6])}
                        for b in blocks]})
                    text = clean_page_text("\n".join(str(b[4]) for b in blocks if str(b[4]).strip()))
                    if text:
                        elements.append(PrimaryText(
                            f"page-{page_number}", "paragraph", text,
                            (PageSpan(page_number, None, f"page:{page_number}"),), None))
        except Exception as exc:
            raise ParseError(f"PDF 无法读取：{source.relative_path}") from exc
        if page_count == 0 or not elements:
            raise ParseError(f"PDF 没有可用文本：{source.relative_path}")
        primary = PrimaryDocument(source.document_id, source.file_hash, page_count,
                                  tuple(elements), (), (), ())
        return ParseResult(primary, {"format": "pymupdf_blocks", "pages": native_pages})
