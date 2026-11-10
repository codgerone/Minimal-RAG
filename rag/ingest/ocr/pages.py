"""Page classification: text layer, scanned, or blank (docs/rules/ocr.md)."""

from __future__ import annotations

from pathlib import Path

import fitz

from rag.ingest.ocr.models import PageClass, PageKind


def page_kind(page: fitz.Page) -> PageKind:
    if page.get_text("text").strip():
        return "text_layer"
    # get_image_info lists images actually drawn on the page, inline images and form XObjects included.
    return "scanned" if page.get_image_info() else "blank"


def classify_pages(path: Path) -> tuple[PageClass, ...]:
    with fitz.open(path) as pdf:
        return tuple(PageClass(index + 1, page_kind(page), int(page.rotation))
                     for index, page in enumerate(pdf))
