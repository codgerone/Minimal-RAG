"""The OCR step before parsing: classify pages, OCR scanned ones once, derive and check `ocr.pdf`.

Facts and the derived PDF live in `.rag/ocr/<file hash[:16]>-<settings hash[:8]>/`, shared by every
index; the same source with the same OCR settings is never OCR'd twice.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable

import fitz

from rag.ingest.ocr import OcrEngine
from rag.ingest.ocr.models import DocumentPages, OcrFacts, PageClass
from rag.ingest.ocr.pages import classify_pages
from rag.ingest.ocr.recognize import recognize_page
from rag.ingest.ocr.text_layer import OcrSelfCheckError, build_text_layer_pdf, self_check
from rag.jsonio import from_jsonable, read_json, write_json
from rag.models import ParseFile, SourceDocument


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class OcrStep:
    def __init__(self, use: str, engine: OcrEngine, dpi: int, root: Path):
        self.use = use
        self.engine = engine
        self.dpi = dpi
        self.root = root        # .rag/ocr

    def settings(self) -> dict[str, Any]:
        return {"use": self.use, "dpi": self.dpi, "identity": self.engine.identity()}

    def key(self, source: SourceDocument) -> str:
        raw = json.dumps(self.settings(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return f"{source.file_hash[:16]}-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:8]}"

    def folder(self, source: SourceDocument) -> Path:
        return self.root / self.key(source)

    def facts(self, source: SourceDocument, pages: tuple[PageClass, ...],
              progress: Callable[[str], None] = lambda message: None) -> OcrFacts:
        path = self.folder(source) / "ocr.json"
        if path.is_file():
            stored = from_jsonable(OcrFacts, read_json(path))
            if stored.file_hash == source.file_hash and stored.settings == self.settings():
                return stored
        scanned = [page.page_number for page in pages if page.kind == "scanned"]
        results = []
        with fitz.open(source.absolute_path) as pdf:
            for count, number in enumerate(scanned, 1):
                progress(f"OCR {source.relative_path} 第 {number} 页（{count}/{len(scanned)}）…")
                results.append(recognize_page(pdf[number - 1], number, self.engine, self.dpi))
        facts = OcrFacts(source.file_hash, self.settings(), pages, tuple(results))
        write_json(path, facts)
        return facts

    def prepare(self, source: SourceDocument,
                progress: Callable[[str], None] = lambda message: None) -> tuple[SourceDocument, DocumentPages]:
        pages = classify_pages(source.absolute_path)
        if not any(page.kind == "scanned" for page in pages):
            return source, DocumentPages(pages, None)
        facts = self.facts(source, pages, progress)
        output = self.folder(source) / "ocr.pdf"
        if not output.is_file():
            # Only a PDF that passed the self-check is ever moved into place.
            temporary = output.with_name("ocr.pdf.tmp")
            build_text_layer_pdf(source.absolute_path, facts, temporary)
            problems = self_check(temporary, facts)
            if problems:
                raise OcrSelfCheckError(f"{source.relative_path} 的文字层 PDF 自检未通过：" + "；".join(problems))
            os.replace(temporary, output)
        return (dataclasses.replace(source, parse_file=ParseFile(output, _sha256(output))),
                DocumentPages(pages, self.key(source)))
