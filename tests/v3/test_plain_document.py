"""Real PDF comparison for the plain-text V3 data path."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pymupdf

from rag.v3.adapters.local_registry import LocalPdfRegistry, RegistryError, RegistryRequest
from rag.v3.adapters.pymupdf_pages import PyMuPDFPagesParser
from rag.v3.application.plain_document import (
    PageCharacterChunker, PageCharacterParameters, PlainPageComposer,
)


def test_real_pdf_plain_parse_and_chunk_match_published_algorithm():
    root = Path(__file__).resolve().parents[2] / "documents"
    fixture = json.loads((Path(__file__).parent / "fixtures" /
                          "legacy_equivalence.json").read_text(encoding="utf-8"))
    source = LocalPdfRegistry().discover(RegistryRequest(root, "E001-602.pdf"))[0]
    assert source.document_id == fixture["source_id"]
    assert source.file_hash == fixture["source_hash"]
    parsed = PyMuPDFPagesParser().parse(source)
    assert [[item.sources[0].page_number,
             hashlib.sha256(item.text.encode()).hexdigest()]
            for item in parsed.primary.elements] == fixture["pages"]
    assert len(parsed.native.raw_payload.pages) == parsed.primary.page_count
    document = PlainPageComposer().compose(source, parsed.primary)
    batch = PageCharacterChunker(PageCharacterParameters()).chunk(document)
    assert [[item.chunk_id, hashlib.sha256(item.text.encode()).hexdigest(),
             item.sources[0].page_spans[0].page_number]
            for item in batch.chunks] == fixture["chunks"]


def test_native_snapshot_keeps_blank_blocks_and_empty_page(tmp_path):
    root = tmp_path / "documents"
    root.mkdir()
    pdf_path = root / "sample.pdf"
    with pymupdf.open() as pdf:
        first = pdf.new_page()
        first.insert_text((50, 50), "hello")
        pdf.new_page()
        pdf.save(pdf_path)
    source = LocalPdfRegistry().discover(RegistryRequest(root, None))[0]
    result = PyMuPDFPagesParser().parse(source)
    assert result.primary.page_count == 2
    assert len(result.primary.elements) == 1
    assert len(result.native.raw_payload.pages) == 2
    assert result.native.raw_payload.pages[1].blocks == ()


def test_registry_reports_ambiguous_name_and_rejects_escape(tmp_path):
    root = tmp_path / "documents"
    for folder in (root / "a", root / "b"):
        folder.mkdir(parents=True)
        with pymupdf.open() as pdf:
            pdf.new_page()
            pdf.save(folder / "same.pdf")
    registry = LocalPdfRegistry()
    sources = registry.discover(RegistryRequest(root, None))
    assert len(sources) == 2
    try:
        registry.resolve_selector("same.pdf", sources)
    except RegistryError as exc:
        assert exc.code == "selector_ambiguous"
    else:
        raise AssertionError("ambiguous selector was accepted")
    for unsafe in ("../same.pdf", "a//same.pdf", "C:/other.pdf"):
        try:
            registry.resolve_selector(unsafe, sources)
        except RegistryError as exc:
            assert exc.code == "selector_invalid"
        else:
            raise AssertionError(f"unsafe selector accepted: {unsafe}")
