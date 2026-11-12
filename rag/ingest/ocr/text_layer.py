"""Derive the text-layer PDF from the source PDF and its OCR facts, and check it reads back exactly.

Text-layer and blank pages are copied unchanged. A scanned page becomes the recognised image filling
the page, with every OCR word written as invisible text (render mode 3) followed by a space: the font
size makes ascender-to-descender equal the box height, and horizontal scaling makes the word as wide
as its box, so PyMuPDF reads each word back in the OCR box.
"""

from __future__ import annotations

from pathlib import Path

import fitz

from rag.ingest.ocr.models import OcrFacts, PageOcr
from rag.ingest.ocr.recognize import prepare_image

BOX_TOLERANCE = 1.0   # points


class OcrSelfCheckError(RuntimeError):
    pass


def _font() -> fitz.Font:
    return fitz.Font("cjk")


def page_size(page: fitz.Page, ocr: PageOcr) -> tuple[float, float]:
    """The displayed size; width and height swap when the image was turned by 90° or 270°."""
    width, height = float(page.rect.width), float(page.rect.height)
    return (height, width) if ocr.orientation in (90, 270) else (width, height)


FONT_NAME = "ocr"


def _write_words(target: fitz.Page, ocr: PageOcr, font: fitz.Font) -> None:
    # One font resource per page that every word refers to: a resource per word makes parsers
    # load the font hundreds of times per page (Docling took ~11× longer and gigabytes of memory).
    target.insert_font(fontname=FONT_NAME, fontbuffer=font.buffer)
    span = font.ascender - font.descender
    for word in ocr.words:
        box = word.box
        size = (box.y1 - box.y0) / span
        natural = font.text_length(word.text, fontsize=size)
        if size <= 0 or natural <= 0:
            raise OcrSelfCheckError(f"第 {ocr.page_number} 页的词 {word.text!r} 无法写入（框高或字宽为 0）")
        baseline = fitz.Point(box.x0, box.y0 + size * font.ascender)
        target.insert_text(baseline, word.text + " ", fontname=FONT_NAME, fontsize=size, render_mode=3,
                           morph=(baseline, fitz.Matrix((box.x1 - box.x0) / natural, 1)))


def build_text_layer_pdf(source_pdf: Path, facts: OcrFacts, output: Path) -> None:
    by_page = {item.page_number: item for item in facts.ocr_pages}
    font = _font()
    with fitz.open(source_pdf) as source, fitz.open() as derived:
        for index, page in enumerate(source):
            ocr = by_page.get(index + 1)
            if ocr is None:
                derived.insert_pdf(source, from_page=index, to_page=index)
                continue
            width, height = page_size(page, ocr)
            target = derived.new_page(width=width, height=height)
            image = prepare_image(page, ocr.dpi, ocr.orientation, ocr.skew_degrees, ocr.deskewed)
            pixmap = fitz.Pixmap(fitz.csGRAY, image.shape[1], image.shape[0], image.tobytes(), False)
            target.insert_image(target.rect, pixmap=pixmap)
            _write_words(target, ocr, font)
        derived.subset_fonts()
        output.parent.mkdir(parents=True, exist_ok=True)
        derived.save(output, garbage=3, deflate=True)


def self_check(pdf_path: Path, facts: OcrFacts) -> list[str]:
    """Problems found; empty when every scanned page reads back the OCR words in their boxes."""
    problems: list[str] = []
    with fitz.open(pdf_path) as pdf:
        if len(pdf) != len(facts.pages):
            return [f"页数 {len(pdf)} 与原文件 {len(facts.pages)} 不同"]
        for ocr in facts.ocr_pages:
            read = pdf[ocr.page_number - 1].get_text("words", sort=False)
            expected = ocr.words
            if [item[4] for item in read] != [word.text for word in expected]:
                first = next((i for i, (a, b) in enumerate(zip(read, expected)) if a[4] != b.text),
                             min(len(read), len(expected)))
                problems.append(f"第 {ocr.page_number} 页读回 {len(read)} 词、OCR {len(expected)} 词，"
                                f"第 {first + 1} 个词起不同")
                continue
            worst = max((max(abs(item[0] - word.box.x0), abs(item[1] - word.box.y0),
                             abs(item[2] - word.box.x1), abs(item[3] - word.box.y1))
                         for item, word in zip(read, expected)), default=0.0)
            if worst > BOX_TOLERANCE:
                problems.append(f"第 {ocr.page_number} 页词框最大偏差 {worst:.2f} 点，超过 {BOX_TOLERANCE} 点")
    return problems
