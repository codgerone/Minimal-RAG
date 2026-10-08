"""One text element per page, read with PyMuPDF; no OCR, no tables. Each word keeps its position."""

from __future__ import annotations

import re

import pymupdf

from rag.ingest.parsers import ParseResult
from rag.models import BoundingBox, PageSpan, PrimaryDocument, PrimaryText, SourceDocument, WordBox


class ParseError(RuntimeError):
    pass


def clean_page_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line.rstrip()) for line in normalized.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def page_word_boxes(page: pymupdf.Page, blocks: list, text: str, page_number: int) -> tuple[WordBox, ...]:
    """Positions of the words of the cleaned page text.

    Cleaning only changes whitespace, so the page text's other characters are, in order, the
    characters of the blocks it was built from; each takes its box from PyMuPDF's character data.
    Returns nothing when the two do not line up (the page then has no word positions).
    """
    # Same flags as get_text("blocks"), so both list the same blocks under the same numbers.
    chars_by_block = {
        block["number"]: [c for line in block.get("lines", []) for span in line["spans"]
                          for c in span["chars"]]
        for block in page.get_text("rawdict", sort=True, flags=pymupdf.TEXTFLAGS_BLOCKS)["blocks"]
        if block["type"] == 0}

    source: list[tuple[str, tuple[float, float, float, float] | None]] = []
    for b in blocks:
        if not str(b[4]).strip():
            continue
        if int(b[6]) != 0:   # image block: its description is in the text but has no character boxes
            source.extend((ch, None) for ch in str(b[4]) if not ch.isspace())
            continue
        chars = chars_by_block.get(int(b[5]))
        if chars is None:
            return ()
        source.extend((c["c"], tuple(c["bbox"])) for c in chars if not c["c"].isspace())
    visible = [(i, ch) for i, ch in enumerate(text) if not ch.isspace()]
    if len(visible) != len(source) or any(ch != src for (_, ch), (src, _) in zip(visible, source)):
        return ()
    box_at = {i: box for (i, _), (_, box) in zip(visible, source)}
    words: list[WordBox] = []
    for match in re.finditer(r"\S+", text):
        boxes = [box_at[i] for i in range(match.start(), match.end()) if box_at.get(i)]
        if boxes:
            words.append(WordBox(match.start(), match.end(), page_number, BoundingBox(
                min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))))
    return tuple(words)


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
                            (PageSpan(page_number, None, f"page:{page_number}"),), None,
                            page_word_boxes(page, blocks, text, page_number)))
        except Exception as exc:
            raise ParseError(f"PDF 无法读取：{source.relative_path}") from exc
        if page_count == 0 or not elements:
            raise ParseError(f"PDF 没有可用文本：{source.relative_path}")
        primary = PrimaryDocument(source.document_id, source.file_hash, page_count,
                                  tuple(elements), (), (), ())
        return ParseResult(primary, {"format": "pymupdf_blocks", "pages": native_pages})
