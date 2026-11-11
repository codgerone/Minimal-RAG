"""Whole-page check (run before run.py): each scanned page read with the line classifier on, off, and per
box the higher-confidence of the two; simulated scans scored against the words of the digital original
(multiset recall / precision, case-folded). Also a squeeze test: every horizontal crop the classifier turned
(width >= 1.5 x height) re-classified in segments of width 4 x height. On the user scans that crop set also holds
the vertical-text crops, which RapidOCR has already turned to horizontal, so only the simulated scans count.
Usage: text_check.py <workspace> [<originals>] > text-<set>.json
"""
import collections, hashlib, json, sys
from pathlib import Path
import fitz, numpy as np

sys.path.insert(0, r"D:\VS-Code-file\Minimal-RAG")
from rag.ingest.ocr.rapidocr_engine import RapidOcrEngine
from rag.ingest.ocr.recognize import prepare_image

ws = Path(sys.argv[1]); originals = Path(sys.argv[2]) if len(sys.argv) > 2 else None
engine = RapidOcrEngine()._load()
CROPS, CLS = [], []
_cls = engine.text_cls
def _spy(imgs):
    CROPS.append(list(imgs)); out = _cls(imgs); CLS.append(list(out.cls_res)); return out
engine.text_cls = _spy

def words_of(texts):
    return collections.Counter(w.casefold() for t in texts for w in t.split())

def key(box):
    return tuple(np.round(np.array(box), 1).ravel())

squeeze = collections.Counter()
rows = []
by_hash = {hashlib.sha256(p.read_bytes()).hexdigest()[:16]: p for p in (ws / "documents").glob("*.pdf")}
for folder in sorted((ws / ".rag" / "ocr").iterdir()):
    facts = json.loads((folder / "ocr.json").read_text(encoding="utf-8"))
    pdf_path = by_hash[facts["file_hash"][:16]]
    ref_pdf = fitz.open(originals / pdf_path.name) if originals else None
    with fitz.open(pdf_path) as pdf:
        for page in facts["ocr_pages"]:
            n = page["page_number"]
            image = prepare_image(pdf[n - 1], page["dpi"], page["orientation"], page["skew_degrees"], page["deskewed"])
            CROPS.clear(); CLS.clear()
            on = engine(image, use_cls=True)
            crops, labels = CROPS[0], CLS[0]
            off = engine(image, use_cls=False)
            off_by = {key(b): (t, s) for t, s, b in zip(off.txts or (), off.scores or (), off.boxes)}
            on_by = {key(b): (t, s) for t, s, b in zip(on.txts or (), on.scores or (), on.boxes)}
            picked = {}
            for k in set(on_by) | set(off_by):
                cands = [c for c in (on_by.get(k), off_by.get(k)) if c]
                picked[k] = max(cands, key=lambda c: c[1])
            # squeeze test: horizontal crops the classifier turned
            for crop, (label, s) in zip(crops, labels):
                h, w = crop.shape[:2]
                if "180" in label and s > 0.9 and w >= 1.5 * h:
                    seg = 4 * h
                    parts = [crop[:, x:x + seg] for x in range(0, w - h, seg)]
                    out = _cls(parts).cls_res
                    turned = sum(1 for lab, sc in out if "180" in lab and sc > 0.9)
                    squeeze["turned_horizontal"] += 1
                    squeeze["segments"] += len(parts); squeeze["segments_turned"] += turned
            row = {"doc": pdf_path.name, "page": n}
            for name, chosen in (("on", on_by), ("off", off_by), ("pick", picked)):
                texts = [t for t, _ in chosen.values()]
                row[name] = {"lines": len(texts), "valid_chars": sum(ch.isalnum() for t in texts for ch in t),
                             "low_lines": sum(s < 0.8 for _, s in chosen.values())}
                if ref_pdf is not None:
                    ref = words_of([w[4] for w in ref_pdf[n - 1].get_text("words")])
                    ocr = words_of(texts)
                    row[name].update(hit=sum((ocr & ref).values()), ref=sum(ref.values()), ocr=sum(ocr.values()))
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), file=sys.stderr, flush=True)
    if ref_pdf is not None:
        ref_pdf.close()
print(json.dumps({"rows": rows, "squeeze": squeeze}, ensure_ascii=False, indent=1))
