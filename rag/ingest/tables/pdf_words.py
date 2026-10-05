"""PyMuPDF raw word facts for the selector's PDF evidence port."""

from __future__ import annotations

import hashlib
import logging

import fitz

from rag.models import BoundingBox, SourceDocument
from rag.ingest.tables.models import PdfPageWord

logger = logging.getLogger(__name__)


class PdfEvidenceError(RuntimeError):
    """PDF word evidence cannot be tied to the selected source version."""


def read_page_words(source: SourceDocument, page_number: int) -> tuple[PdfPageWord, ...]:
    if type(page_number) is not int or page_number <= 0:
        raise PdfEvidenceError("page number must be one-based")
    content = source.absolute_path.read_bytes()
    if hashlib.sha256(content).hexdigest() != source.file_hash:
        raise PdfEvidenceError("PDF evidence source hash differs from registry")
    with fitz.open(stream=content, filetype="pdf") as pdf:
        if page_number > len(pdf):
            raise PdfEvidenceError("PDF evidence page out of range")
        raw_words = pdf[page_number - 1].get_text("words", sort=False)
    result: list[PdfPageWord] = []
    for index, raw in enumerate(raw_words):
        if len(raw) < 8:
            logger.warning("skipped malformed PDF word at page %s index %s", page_number, index)
            continue
        try:
            x0, y0, x1, y1 = (float(raw[item]) for item in range(4))
            result.append(PdfPageWord(page_number, BoundingBox(x0, y0, x1, y1),
                                      str(raw[4]), int(raw[5]), int(raw[6]), int(raw[7])))
        except (TypeError, ValueError) as exc:
            raise PdfEvidenceError("malformed PDF word fact") from exc
    return tuple(result)


def read_page_geometry(source: SourceDocument, page_number: int) -> tuple[float, float]:
    if type(page_number) is not int or page_number <= 0:
        raise PdfEvidenceError("page number must be one-based")
    content = source.absolute_path.read_bytes()
    if hashlib.sha256(content).hexdigest() != source.file_hash:
        raise PdfEvidenceError("PDF evidence source hash differs from registry")
    with fitz.open(stream=content, filetype="pdf") as pdf:
        if page_number > len(pdf):
            raise PdfEvidenceError("PDF evidence page out of range")
        rectangle = pdf[page_number - 1].rect
        return float(rectangle.width), float(rectangle.height)


def read_document_geometry(source: SourceDocument) -> tuple[tuple[float, float], ...]:
    content = source.absolute_path.read_bytes()
    if hashlib.sha256(content).hexdigest() != source.file_hash:
        raise PdfEvidenceError("PDF evidence source hash differs from registry")
    with fitz.open(stream=content, filetype="pdf") as pdf:
        return tuple((float(page.rect.width), float(page.rect.height)) for page in pdf)


class PyMuPdfEvidenceReader:
    """Bound PDF evidence port used by the selected table selector."""

    read_page_words = staticmethod(read_page_words)
    read_document_geometry = staticmethod(read_document_geometry)
