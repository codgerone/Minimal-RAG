"""Extract page-scoped text from PDFs using PyMuPDF."""

from __future__ import annotations

import re

import pymupdf

from rag.errors import PdfParseError
from rag.models import PageText, SourceDocument


def clean_page_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line.rstrip()) for line in normalized.split("\n")]
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def parse_pdf(document: SourceDocument) -> list[PageText]:
    pages: list[PageText] = []
    try:
        with pymupdf.open(document.absolute_path) as pdf:
            for page_number, page in enumerate(pdf, start=1):
                blocks = page.get_text("blocks", sort=True)
                raw_text = "\n".join(
                    str(block[4]) for block in blocks if str(block[4]).strip()
                )
                text = clean_page_text(raw_text)
                if text:
                    pages.append(
                        PageText(
                            document_id=document.document_id,
                            document_name=document.document_name,
                            relative_path=document.relative_path,
                            page_number=page_number,
                            text=text,
                        )
                    )
    except Exception as exc:
        raise PdfParseError(
            f"无法解析 PDF：{document.relative_path}",
            "请确认文件未损坏；若 PDF 是扫描件，V1 可能需要 OCR 才能处理。",
            cause=exc,
        ) from exc

    if not pages:
        raise PdfParseError(
            f"PDF 没有可提取的文字：{document.relative_path}",
            "该文件可能是扫描件，需要 OCR；V1 不支持 OCR。",
        )
    return pages

