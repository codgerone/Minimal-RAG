from __future__ import annotations

import dataclasses
from pathlib import Path

import fitz
import numpy as np
import pytest

from rag.ingest.ocr import EngineLine, EngineWord
from rag.ingest.ocr.models import DocumentPages, PageClass
from rag.ingest.ocr.pages import classify_pages
from rag.ingest.ocr.rapidocr_engine import RapidOcrEngine
from rag.ingest.ocr.recognize import recognize_page, render, skew_of, straighten
from rag.ingest.ocr.step import OcrStep
from rag.ingest.ocr.text_layer import build_text_layer_pdf, self_check
from rag.ingest.sources import file_hash
from rag.models import SourceDocument

LINES = ["CONTRATO DE SUMINISTRO N° 102-2025", "Precio total: S/ 1,234.50 por año",
         "Plazo de entrega: 120 días calendario"]


def _text_page(document: fitz.Document, width: float = 420, height: float = 300) -> fitz.Page:
    page = document.new_page(width=width, height=height)
    for index, line in enumerate(LINES):
        page.insert_text((30, 60 + 40 * index), line, fontsize=14)
    return page


def _scanned_page(document: fitz.Document, image: np.ndarray, width: float, height: float) -> None:
    page = document.new_page(width=width, height=height)
    page.insert_image(page.rect, pixmap=fitz.Pixmap(fitz.csGRAY, image.shape[1], image.shape[0],
                                                    image.tobytes(), False))


def _image_of_text(width: float = 420, height: float = 300) -> np.ndarray:
    with fitz.open() as scratch:
        return render(_text_page(scratch, width, height), 300)


def _source(path: Path) -> SourceDocument:
    return SourceDocument("doc1", path.name, path.name, path, file_hash(path))


@pytest.fixture(scope="module")
def engine() -> RapidOcrEngine:
    return RapidOcrEngine()


@pytest.fixture()
def mixed_pdf(tmp_path: Path) -> Path:
    """Page 1 text layer, page 2 scanned, page 3 blank."""
    path = tmp_path / "mixed.pdf"
    with fitz.open() as document:
        _text_page(document)
        _scanned_page(document, _image_of_text(), 420, 300)
        document.new_page(width=420, height=300)
        document.save(path)
    return path


def test_classify_pages(mixed_pdf: Path) -> None:
    assert [(p.page_number, p.kind) for p in classify_pages(mixed_pdf)] == [
        (1, "text_layer"), (2, "scanned"), (3, "blank")]


def test_document_type() -> None:
    def pages(*kinds):
        return DocumentPages(tuple(PageClass(i + 1, kind, 0) for i, kind in enumerate(kinds)), None)
    assert pages("text_layer", "blank").document_type == "digital"
    assert pages("scanned", "blank").document_type == "scanned"
    assert pages("text_layer", "scanned").document_type == "partly_scanned"


class _FakeEngine:
    """Reads a vertical line unless the image was turned to landscape, where it reads a good line."""
    def __init__(self) -> None:
        self.calls = 0

    def identity(self) -> dict:
        return {"engine": "fake"}

    def recognize(self, image: np.ndarray) -> tuple[EngineLine, ...]:
        self.calls += 1
        height, width = image.shape
        if width > height:
            quad = ((10.0, 10.0), (210.0, 10.0), (210.0, 40.0), (10.0, 40.0))
            return (EngineLine("Total 1234", quad, 0.95,
                               (EngineWord("Total", ((10.0, 10.0), (90.0, 10.0), (90.0, 40.0), (10.0, 40.0)), 0.95),
                                EngineWord("1234", ((110.0, 10.0), (210.0, 10.0), (210.0, 40.0), (110.0, 40.0)), 0.95))),)
        quad = ((10.0, 10.0), (40.0, 10.0), (40.0, 210.0), (10.0, 210.0))
        return (EngineLine("lx", quad, 0.3, (EngineWord("lx", quad, 0.3),)),)


def test_orientation_search_picks_best_score(tmp_path: Path) -> None:
    path = tmp_path / "portrait.pdf"
    with fitz.open() as document:
        _scanned_page(document, np.full((400, 200), 255, np.uint8), 96, 192)
        document.save(path)
    fake = _FakeEngine()
    with fitz.open(path) as pdf:
        result = recognize_page(pdf[0], 1, fake, 150)
    assert [t.orientation for t in result.trials] == [0, 90, 180, 270]
    assert result.orientation == 90                      # 90 and 270 tie; the earlier one wins
    assert (result.image_width, result.image_height) == (400, 200)
    assert fake.calls == 4
    assert result.lines[0].words[1].box.x1 == pytest.approx(210 * 72 / 150)


def test_straighten_turns_back_measured_skew(engine: RapidOcrEngine) -> None:
    tilted = straighten(_image_of_text(), -1.5)   # introduce a known skew
    measured = skew_of(engine.recognize(tilted))
    assert measured == pytest.approx(1.5, abs=0.4)
    corrected = skew_of(engine.recognize(straighten(tilted, measured)))
    assert abs(corrected) < 0.3


def test_step_ocrs_scanned_pages_once_and_derives_checked_pdf(tmp_path: Path, mixed_pdf: Path,
                                                               engine: RapidOcrEngine) -> None:
    source = _source(mixed_pdf)
    step = OcrStep("rapidocr", engine, 300, tmp_path / "ocr")
    prepared, pages = step.prepare(source)
    assert pages.ocr_key == step.key(source) and pages.document_type == "partly_scanned"
    assert prepared.parse_file is not None and prepared.pdf_path.name == "ocr.pdf"
    assert prepared.file_hash == source.file_hash and prepared.pdf_hash != source.file_hash
    with fitz.open(prepared.pdf_path) as derived, fitz.open(mixed_pdf) as original:
        assert derived[0].get_text("words") == original[0].get_text("words")
        scanned_words = [w[4] for w in derived[1].get_text("words")]
        assert derived[2].get_text("text") == ""
    assert {"102-2025", "1,234.50", "días"} <= set(scanned_words)
    facts = step.facts(source, pages.pages)
    assert [p.page_number for p in facts.ocr_pages] == [2]
    assert self_check(prepared.pdf_path, facts) == []

    calls = []
    original_recognize = engine.recognize
    engine.recognize = lambda image: calls.append(1) or original_recognize(image)  # type: ignore[method-assign]
    try:
        again, _ = step.prepare(source)
    finally:
        del engine.recognize
    assert calls == [] and again.parse_file == prepared.parse_file


def test_self_check_reports_mismatch(tmp_path: Path, mixed_pdf: Path, engine: RapidOcrEngine) -> None:
    source = _source(mixed_pdf)
    step = OcrStep("rapidocr", engine, 300, tmp_path / "ocr")
    facts = step.facts(source, classify_pages(mixed_pdf))
    built = tmp_path / "built.pdf"
    build_text_layer_pdf(mixed_pdf, facts, built)
    page = facts.ocr_pages[0]
    line = page.lines[0]
    word = line.words[0]
    moved = dataclasses.replace(word, box=dataclasses.replace(word.box, x0=word.box.x0 - 3))
    altered = dataclasses.replace(facts, ocr_pages=(dataclasses.replace(
        page, lines=(dataclasses.replace(line, words=(moved,) + line.words[1:]),) + page.lines[1:]),))
    assert self_check(built, facts) == []
    assert "偏差" in self_check(built, altered)[0]


def test_pages_without_scans_skip_ocr(tmp_path: Path, engine: RapidOcrEngine) -> None:
    path = tmp_path / "digital.pdf"
    with fitz.open() as document:
        _text_page(document)
        document.save(path)
    source = _source(path)
    prepared, pages = OcrStep("rapidocr", engine, 300, tmp_path / "ocr").prepare(source)
    assert prepared is source and pages.ocr_key is None and pages.document_type == "digital"
    assert not (tmp_path / "ocr").exists()


# ---- [ocr] config, fingerprint, status, ingest -------------------------------------------------

def test_ocr_config_validation_and_fingerprint(tmp_path: Path) -> None:
    from test_config import write
    from rag.config import ConfigError, load_config
    from rag.registry import assemble
    plain = assemble(load_config(write(tmp_path, name="plain")))
    assert "ocr" not in plain.build_settings() and "ocr_identity" not in plain.build_settings()
    (tmp_path / "configs").mkdir()
    path = write(tmp_path / "configs", name="scan", extra='[ocr]\nuse = "rapidocr"\n')
    with_ocr = assemble(load_config(path))
    assert with_ocr.build_settings()["ocr"] == {"use": "rapidocr", "dpi": 300}
    assert with_ocr.build_settings()["ocr_identity"]["models"]["rec"] == "PP-OCRv6_rec_small.onnx"
    assert with_ocr.ocr.root == tmp_path / ".rag" / "ocr"
    for bad in ('[ocr]\nuse = "rapidocr"\ndpi = 20\n', '[ocr]\nuse = "rapidocr"\nlang = "es"\n'):
        with pytest.raises(ConfigError):
            load_config(write(tmp_path, name="bad", extra=bad))
    with pytest.raises(ConfigError, match="没有名为"):
        assemble(load_config(write(tmp_path / "configs", name="other", extra='[ocr]\nuse = "tesseract"\n')))


def _scanned_only(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with fitz.open() as document:
        _scanned_page(document, _image_of_text(), 420, 300)
        document.save(path)


def test_status_and_ingest_of_scanned_pdf(workspace, fake_assembly, engine: RapidOcrEngine) -> None:
    import dataclasses as dc
    from rag.index.audit import load_audit
    from rag.index.builder import current_status, ingest
    _scanned_only(workspace.documents / "scan.pdf")
    assert current_status(workspace, fake_assembly).documents[0].state == "unreadable"
    assembly = dc.replace(fake_assembly, ocr=OcrStep("rapidocr", engine, 300, workspace.root / ".rag" / "ocr"))
    assert current_status(workspace, assembly).documents[0].state == "new"
    outcome = ingest(workspace, assembly)[0]
    assert outcome.action == "added" and outcome.chunk_count >= 1
    entry = current_status(workspace, assembly).documents[0].entry
    audit = load_audit(workspace.index_dir("fake") / "documents" / entry.folder)
    assert audit.pages is not None and audit.pages.document_type == "scanned" and audit.pages.ocr_key
    text = " ".join(chunk.text for chunk in audit.batch.chunks)
    assert "1,234.50" in text and "102-2025" in text


def test_ingest_writes_ocr_review_page(workspace, fake_assembly, engine: RapidOcrEngine) -> None:
    import dataclasses as dc
    from rag.index.builder import ingest
    _scanned_only(workspace.documents / "scan.pdf")
    assembly = dc.replace(fake_assembly, ocr=OcrStep("rapidocr", engine, 300, workspace.root / ".rag" / "ocr"))
    ingest(workspace, assembly)
    folder = workspace.ingest_reports("fake") / "scan"
    html = (folder / "0-OCR.html").read_text(encoding="utf-8")
    assert "扫描 PDF" in html and "自检通过" in html and "1,234.50" in html
    assert 'href="0-OCR.html"' in (folder / "1-解析.html").read_text(encoding="utf-8")
    assert list((workspace.reports / "ocr").glob("*/p001.jpg"))
