"""V4.1 line-orientation experiment: RapidOCR's line classifier (cls) on vs off, on every scanned page.

For every scanned page of the simulated scans (scratchpad ws-sim) and the user's scans (documents/):
- time the current page OCR (recognize_page, cls on) and the candidate (each engine call read twice,
  cls on and off, keeping the higher-confidence reading per detected box);
- on the page's final image, read once with cls on and once off (no confidence filter), and record every detected box whose
  reading differs (the classifier turned it 180°), with both texts and confidences and a crop image;
- judge which reading is right: simulated scans against the digital original's words inside the box
  (word F1); the user's scans by the share of a reading's words found in a vocabulary (digital originals
  plus untouched OCR lines with confidence ≥ 0.95); boxes neither rule settles are left for a person
  (manual.json, filled after looking at the crops).

Writes results.json and crops/ next to this script.
Run: python reports/experiments/v4.1-line-orientation/run.py <ws-sim folder>
"""

from __future__ import annotations

import collections
import hashlib
import json
import re
import sys
import time
from pathlib import Path

import cv2
import fitz
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from rag.ingest.ocr import EngineLine, EngineWord  # noqa: E402
from rag.ingest.ocr.rapidocr_engine import RapidOcrEngine, _quad  # noqa: E402
from rag.ingest.ocr.recognize import prepare_image, recognize_page  # noqa: E402

CLS_THRESH = 0.9          # rapidocr config.yaml Cls.cls_thresh: a crop is turned when its "180" score exceeds it
VERTICAL_RATIO = 1.5      # rapidocr get_rotate_crop_image turns a crop 90° when height / width ≥ 1.5
VOCAB_CONF = 0.95
WORD = re.compile(r"[^\W\d_]{3,}")

base = RapidOcrEngine()
engine = base._load()
CAPTURE: list = []
_cls = engine.text_cls


def _spy(images):
    out = _cls(images)
    CAPTURE.append((list(images), list(out.cls_res)))
    return out


engine.text_cls = _spy
REC: list = []
_rec = engine.text_rec


def _rec_spy(rec_input):
    out = _rec(rec_input)
    REC.append([(str(t), float(sc)) for t, sc in zip(out.txts or (), out.scores or ())])
    return out


engine.text_rec = _rec_spy


def _lines(result) -> list[EngineLine]:
    if not result.txts:
        return []
    return [EngineLine(str(t), _quad(b), float(s),
                       tuple(EngineWord(str(w), _quad(q), float(c)) for w, c, q in words
                             if q is not None and str(w).strip()))
            for t, s, b, words in zip(result.txts, result.scores, result.boxes, result.word_results)]


def _key(quad) -> tuple:
    return tuple(round(v, 1) for point in quad for v in point)


DEFAULT_SCORE = 0.5      # rapidocr config.yaml Global.text_score (the pipeline's filter)


class CurrentEngine:
    """The pipeline's engine; RapidOCR keeps call arguments as state, so cls and the filter are reset first."""
    def identity(self):
        return base.identity()

    def recognize(self, image):
        engine.update_params(use_cls=True, text_score=DEFAULT_SCORE)
        return base.recognize(image)


class PickEngine:
    """Candidate: read with cls on and off; per detected box keep the higher-confidence reading."""
    def identity(self):
        return base.identity()

    def recognize(self, image):
        on = {_key(l.quad): l for l in _lines(engine(image, return_word_box=True, use_cls=True,
                                                      text_score=DEFAULT_SCORE))}
        off = {_key(l.quad): l for l in _lines(engine(image, return_word_box=True, use_cls=False,
                                                       text_score=DEFAULT_SCORE))}
        keys = list(dict.fromkeys([*on, *off]))
        return tuple(max((c for c in (on.get(k), off.get(k)) if c), key=lambda l: l.confidence) for k in keys)


def words(text: str) -> list[str]:
    return text.casefold().split()


def f1(reading: str, reference: list[str]) -> float:
    a, b = collections.Counter(words(reading)), collections.Counter(reference)
    hit = sum((a & b).values())
    return 0.0 if not hit else 2 * hit / (sum(a.values()) + sum(b.values()))


def vocab_share(reading: str, vocab: set[str]) -> float | None:
    tokens = WORD.findall(reading.casefold())
    return None if not tokens else sum(t in vocab for t in tokens) / len(tokens)


def documents(workspace: Path):
    by_hash = {hashlib.sha256(p.read_bytes()).hexdigest()[:16]: p for p in (workspace / "documents").glob("*.pdf")}
    for folder in sorted((workspace / ".rag" / "ocr").iterdir()):
        facts = json.loads((folder / "ocr.json").read_text(encoding="utf-8"))
        yield by_hash[facts["file_hash"][:16]], facts


def run_set(label: str, workspace: Path, originals: Path | None, crops: Path) -> tuple[list, list]:
    timings, boxes = [], []
    for pdf_path, facts in documents(workspace):
        ref_pdf = fitz.open(originals / pdf_path.name) if originals else None
        doc_time = {"set": label, "doc": pdf_path.name, "pages": len(facts["pages"]),
                    "scanned_pages": len(facts["ocr_pages"]), "current_s": 0.0, "pick_s": 0.0,
                    "same_as_stored": 0}
        with fitz.open(pdf_path) as pdf:
            for page in facts["ocr_pages"]:
                n = page["page_number"]
                start = time.perf_counter()
                current = recognize_page(pdf[n - 1], n, CurrentEngine(), page["dpi"])
                doc_time["current_s"] += time.perf_counter() - start
                doc_time["same_as_stored"] += [l.text for l in current.lines] == [l["text"] for l in page["lines"]]
                start = time.perf_counter()
                recognize_page(pdf[n - 1], n, PickEngine(), page["dpi"])
                doc_time["pick_s"] += time.perf_counter() - start

                image = prepare_image(pdf[n - 1], page["dpi"], page["orientation"], page["skew_degrees"],
                                      page["deskewed"])
                # Both runs detect the same boxes in the same order (detection runs before the classifier);
                # the classifier labels and the recogniser outputs are captured per detected box in that
                # order, and each output box is aligned back to its detection index.
                CAPTURE.clear(); REC.clear()
                on_result = engine(image, use_cls=True, text_score=0.0)
                _, labels = CAPTURE[0]
                rec_on = REC[0]
                REC.clear()
                off_result = engine(image, use_cls=False, text_score=0.0)
                rec_off = REC[0]
                if not (len(labels) == len(rec_on) == len(rec_off)):
                    raise RuntimeError(f"{pdf_path.name} p{n}: {len(labels)}/{len(rec_on)}/{len(rec_off)}")
                quads = {}
                for result, rec in ((on_result, rec_on), (off_result, rec_off)):
                    j, out = 0, list(zip(result.txts or (), result.boxes if result.boxes is not None else ()))
                    for i, (text, _) in enumerate(rec):
                        if j < len(out) and out[j][0] == text:
                            quads.setdefault(i, _quad(out[j][1])); j += 1
                    if j != len(out):
                        raise RuntimeError(f"{pdf_path.name} p{n}: output boxes not aligned")
                on = {i: rec_on[i] for i in quads}
                off = {i: rec_off[i] for i in quads}
                all_boxes = quads
                turned_by_key = {i: (labels[i][0], float(labels[i][1])) for i in quads}
                for k, quad in all_boxes.items():
                    a, b = on.get(k), off.get(k)
                    a = a if a and a[0].strip() else None
                    b = b if b and b[0].strip() else None
                    lab, sc = turned_by_key.get(k, ("?", 0.0))
                    turned = "180" in lab and sc > CLS_THRESH
                    if not turned and a == b:
                        continue
                    xs, ys = [p[0] for p in quad], [p[1] for p in quad]
                    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
                    name = f"{label}-{hashlib.sha1(pdf_path.name.encode()).hexdigest()[:6]}-p{n:03d}-{len(boxes):04d}.png"
                    pad = 8
                    crop = image[max(0, int(y0) - pad): int(y1) + pad, max(0, int(x0) - pad): int(x1) + pad]
                    cv2.imencode(".png", crop)[1].tofile(str(crops / name))
                    record = {"set": label, "doc": pdf_path.name, "page": n, "box_px": [x0, y0, x1, y1],
                              "vertical": (y1 - y0) / max(1.0, x1 - x0) >= VERTICAL_RATIO,
                              "cls": [lab, sc], "turned": turned,
                              "on": {"text": a[0], "conf": a[1]} if a else None,
                              "off": {"text": b[0], "conf": b[1]} if b else None, "crop": name}
                    if ref_pdf is not None:
                        scale = 72 / page["dpi"]
                        inside = [w[4].casefold() for w in ref_pdf[n - 1].get_text("words")
                                  if x0 * scale - 2 <= (w[0] + w[2]) / 2 <= x1 * scale + 2
                                  and y0 * scale - 2 <= (w[1] + w[3]) / 2 <= y1 * scale + 2]
                        record["reference"] = " ".join(inside)
                        for side in ("on", "off"):
                            if record[side]:
                                record[side]["f1"] = f1(record[side]["text"], inside)
                    boxes.append(record)
                # vocabulary contribution: untouched confident lines of this page
                for k, (text, score) in on.items():
                    if score >= VOCAB_CONF and on.get(k) == off.get(k) and not turned_by_key[k][0].startswith("180"):
                        VOCAB_OCR.update(WORD.findall(text.casefold()))
        if ref_pdf is not None:
            ref_pdf.close()
        timings.append(doc_time)
        print(json.dumps(doc_time, ensure_ascii=False), flush=True)
    return timings, boxes


VOCAB_OCR: set[str] = set()


def judge(record: dict, vocab: set[str]) -> None:
    on, off = record["on"], record["off"]
    if on is None or off is None:
        record["right"], record["method"] = ("off" if on is None else "on"), "only_one_reading"
        return
    if record["set"] == "sim":
        a, b = on["f1"], off["f1"]
        record["right"], record["method"] = ("on" if a > b else "off" if b > a else None), "reference_f1"
        return
    a, b = vocab_share(on["text"], vocab), vocab_share(off["text"], vocab)
    record["vocab"] = [a, b]
    if a is not None and b is not None and abs(a - b) >= 0.3 and max(a, b) >= 0.5:
        record["right"], record["method"] = ("on" if a > b else "off"), "vocabulary"
    else:
        record["right"], record["method"] = None, "manual"


def main() -> None:
    sim = Path(sys.argv[1])
    crops = HERE / "crops"
    crops.mkdir(exist_ok=True)
    timings, boxes = run_set("sim", sim, ROOT / "documents", crops)
    t2, b2 = run_set("user", ROOT, None, crops)
    timings += t2
    boxes += b2
    vocab = set(VOCAB_OCR)
    for p in (ROOT / "documents").glob("*.pdf"):
        if not p.name.startswith("scanned-"):
            with fitz.open(p) as pdf:
                for pg in pdf:
                    vocab.update(WORD.findall(pg.get_text("text").casefold()))
    for record in boxes:
        judge(record, vocab)
    (HERE / "results.json").write_text(json.dumps({"timings": timings, "boxes": boxes}, ensure_ascii=False,
                                                  indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
