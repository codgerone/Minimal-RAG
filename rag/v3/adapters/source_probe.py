"""Read-only lightweight PDF admission check for index health."""

from __future__ import annotations

import fitz

from rag.v3.contracts.documents import SourceDocument
from rag.v3.contracts.runtime import SourceProbeResult


class PdfSourceProbe:
    def probe(self, source: SourceDocument) -> SourceProbeResult:
        try:
            with fitz.open(source.absolute_path) as pdf:
                page_count = len(pdf)
                if page_count <= 0:
                    return SourceProbeResult(source.document_id, source.file_hash,
                                             "unprocessable", 0, "pdf_no_content")
                has_content = any(page.get_text("text").strip() for page in pdf)
                return SourceProbeResult(
                    source.document_id, source.file_hash,
                    "processable" if has_content else "unprocessable",
                    page_count, None if has_content else "pdf_no_content",
                )
        except Exception:
            return SourceProbeResult(source.document_id, source.file_hash,
                                     "unprocessable", None, "pdf_unreadable")
