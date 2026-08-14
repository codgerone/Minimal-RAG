from pathlib import Path

import pymupdf
import pytest

from rag.document_registry import discover_documents
from rag.errors import PdfParseError
from rag.pdf_parser import clean_page_text, parse_pdf


def _create_pdf(path: Path, page_texts: list[str | None]) -> None:
    with pymupdf.open() as pdf:
        for text in page_texts:
            page = pdf.new_page()
            if text:
                page.insert_text((72, 72), text)
        pdf.save(path)


def test_parse_pdf_preserves_page_numbers(tmp_path: Path) -> None:
    documents = tmp_path / "documents"
    documents.mkdir()
    _create_pdf(documents / "two-pages.pdf", ["First page", "Second page"])

    pages = parse_pdf(discover_documents(documents)[0])

    assert [page.page_number for page in pages] == [1, 2]
    assert [page.text for page in pages] == ["First page", "Second page"]


def test_clean_page_text_keeps_paragraphs() -> None:
    assert clean_page_text("A   value  \r\n\r\n\r\nB\tvalue") == (
        "A value\n\nB value"
    )


def test_textless_pdf_is_explicitly_invalid(tmp_path: Path) -> None:
    documents = tmp_path / "documents"
    documents.mkdir()
    _create_pdf(documents / "scan.pdf", [None])

    with pytest.raises(PdfParseError, match="没有可提取"):
        parse_pdf(discover_documents(documents)[0])

