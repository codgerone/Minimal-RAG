"""V4.1 experiment only (E3, E3d; docs/changes/v4.1.md "对照实验"): Docling converts a scanned PDF with its own
OCR step. Removed after the route decision, unless the parser-internal route is chosen.

- "rapidocr_e1": RapidOcrOptions(force_full_page_ocr=True, scale=300/72) with E1's model files, and RapidOCR
  wrapped so every image is read with the line classifier on and off, keeping the higher-confidence reading per
  box (rag/ingest/ocr/rapidocr_engine.py, decision 019).
- "rapidocr_default": RapidOcrOptions() with every default.

The OCR lines Docling received are recorded per page, for the recognition-consistency check, under
<working directory>/.rag/ocr-docling/<mode>/<file_hash[:16]>.json.
"""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np

MODES = ("rapidocr_e1", "rapidocr_default")
SCALE = 300 / 72

_records: dict[int, list[dict]] = {}
_page: list[int] = [0]


def _patch(pick: bool) -> None:
    import rapidocr
    from docling.models.stages.ocr import rapid_ocr_model

    from rag.ingest.ocr import EngineLine
    from rag.ingest.ocr.rapidocr_engine import TEXT_SCORE, higher_confidence

    base = getattr(rapidocr, "_RAG_ORIGINAL", rapidocr.RapidOCR)
    rapidocr._RAG_ORIGINAL = base

    class Recording(base):  # type: ignore[misc, valid-type]
        def __call__(self, img, use_det=None, use_cls=None, use_rec=None, **kwargs):
            if not pick:
                result = super().__call__(img, use_det=use_det, use_cls=use_cls, use_rec=use_rec, **kwargs)
                lines = [] if result is None or result.boxes is None else [
                    (str(t), float(s), [[float(x), float(y)] for x, y in b])
                    for b, t, s in zip(result.boxes.tolist(), result.txts, result.scores)]
            else:
                reads = []
                for flag in (True, False):
                    r = super().__call__(img, use_cls=flag, text_score=TEXT_SCORE)
                    reads.append(() if r is None or r.boxes is None else tuple(
                        EngineLine(str(t), tuple((float(x), float(y)) for x, y in b), float(s), ())
                        for b, t, s in zip(r.boxes.tolist(), r.txts, r.scores)))
                chosen = higher_confidence(*reads)
                lines = [(line.text, line.confidence, [list(p) for p in line.quad]) for line in chosen]
                result = SimpleNamespace(
                    boxes=np.array([q for _, _, q in lines]) if lines else None,
                    txts=tuple(t for t, _, _ in lines), scores=tuple(s for _, s, _ in lines))
            _records.setdefault(_page[0], []).extend({"text": t, "confidence": s} for t, s, _ in lines)
            return result

    rapidocr.RapidOCR = Recording
    model = rapid_ocr_model.RapidOcrModel
    original_call = getattr(model, "_rag_original_call", model.__call__)
    model._rag_original_call = original_call

    def call(self, conv_res, page_batch):
        for page in page_batch:
            _page[0] = page.page_no          # 1-based
            yield from original_call(self, conv_res, [page])

    model.__call__ = call


def convert(pdf_bytes: bytes, mode: str) -> Any:
    from docling.datamodel.base_models import DocumentStream, InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions, TableFormerMode
    from docling.document_converter import DocumentConverter, PdfFormatOption

    if mode not in MODES:
        raise ValueError(f"[parser] ocr 只能是 {MODES}")
    _records.clear()
    _patch(pick=mode == "rapidocr_e1")
    options = PdfPipelineOptions()
    options.do_ocr = True
    if mode == "rapidocr_e1":
        import rapidocr
        from rag.ingest.ocr.rapidocr_engine import MODELS
        folder = Path(rapidocr.__file__).parent / "models"
        options.ocr_options = RapidOcrOptions(
            force_full_page_ocr=True, scale=SCALE, det_model_path=str(folder / MODELS["det"]),
            rec_model_path=str(folder / MODELS["rec"]), cls_model_path=str(folder / MODELS["cls"]))
    else:
        options.ocr_options = RapidOcrOptions()
    options.do_table_structure = True
    options.table_structure_options.mode = TableFormerMode.ACCURATE
    options.table_structure_options.do_cell_matching = True
    options.generate_page_images = False
    options.do_formula_enrichment = False
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
    with BytesIO(pdf_bytes) as stream:
        return converter.convert(DocumentStream(name="source.pdf", stream=stream)).document


def save_records(mode: str, file_hash: str) -> None:
    folder = Path.cwd() / ".rag" / "ocr-docling" / mode
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{file_hash[:16]}.json").write_text(
        json.dumps({str(k): v for k, v in sorted(_records.items())}, ensure_ascii=False), encoding="utf-8")
