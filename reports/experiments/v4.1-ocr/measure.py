"""V4.1 OCR route experiment (docs/changes/v4.1.md "对照实验"): the measures of E0–E3d.

1. OCR quality: per document, the words each route recognised (E1: OCR facts; E3, E3d: the lines Docling received
   from RapidOCR, split on whitespace) against the words of the digital original's text layer; case-folded word
   multisets. Recall = matched / reference words, precision = matched / recognised words, digit recall = the same
   recall over words that contain a digit. Recognition consistency: E3 against E1, matched / E1 words and
   matched / E3 words, per page.
2. Tables: every table E0 adopted is matched to the table of the same route on the same page whose box has the
   highest IoU, accepted when IoU ≥ 0.5 (the box of a table's first segment). Same structure = same row count,
   column count and set of (start row, start column, row span, column span). Character error rate = total edit
   distance / total original characters: for tables of the same structure summed over cells at the same position,
   otherwise over the row-major text of all cells joined by spaces. Cells whose text differs are listed.
3. Retrieval: the evaluation runs (result.json), per route.

Writes measures.json next to this script.
Run: python reports/experiments/v4.1-ocr/measure.py <ws-digital> <ws-sim>
"""

from __future__ import annotations

import collections
import hashlib
import json
import re
import sys
from pathlib import Path

import fitz
from rapidfuzz.distance import Levenshtein

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
ROUTES = {"E0": "v41_e0", "E1": "v41_e1", "E2": "v41_e2", "E3": "v41_e3", "E3d": "v41_e3d"}
OCR_ROUTES = {"E1": None, "E3": "rapidocr_e1", "E3d": "rapidocr_default"}
IOU_MIN = 0.5


def words(texts) -> collections.Counter:
    return collections.Counter(w.casefold() for t in texts for w in t.split())


def score(got: collections.Counter, ref: collections.Counter) -> dict:
    hit = sum((got & ref).values())
    digits_ref = collections.Counter({w: n for w, n in ref.items() if re.search(r"\d", w)})
    digits_hit = sum((got & digits_ref).values())
    return {"matched": hit, "reference": sum(ref.values()), "recognised": sum(got.values()),
            "digit_matched": digits_hit, "digit_reference": sum(digits_ref.values())}


def reference_words(pdf_path: Path) -> dict[int, collections.Counter]:
    with fitz.open(pdf_path) as pdf:
        return {i + 1: words(w[4] for w in page.get_text("words")) for i, page in enumerate(pdf)}


def e1_words(ws_sim: Path, file_hash: str) -> dict[int, collections.Counter]:
    folder = next(p for p in (ws_sim / ".rag" / "ocr").iterdir() if p.name.startswith(file_hash[:16]))
    facts = json.loads((folder / "ocr.json").read_text(encoding="utf-8"))
    return {p["page_number"]: words(w["text"] for line in p["lines"] for w in line["words"])
            for p in facts["ocr_pages"]}


def docling_words(ws_sim: Path, mode: str, file_hash: str) -> dict[int, collections.Counter]:
    data = json.loads((ws_sim / ".rag" / "ocr-docling" / mode / f"{file_hash[:16]}.json").read_text(encoding="utf-8"))
    return {int(page): words(line["text"] for line in lines) for page, lines in data.items()}


def ocr_quality(ws_sim: Path) -> dict:
    out = {}
    for pdf in sorted((ws_sim / "documents").glob("*.pdf")):
        file_hash = hashlib.sha256(pdf.read_bytes()).hexdigest()
        ref = reference_words(ROOT / "documents" / pdf.name)
        routes = {"E1": e1_words(ws_sim, file_hash)}
        for route, mode in OCR_ROUTES.items():
            if mode:
                routes[route] = docling_words(ws_sim, mode, file_hash)
        doc = {}
        for route, pages in routes.items():
            total = collections.Counter()
            per_page = {}
            for n, ref_page in ref.items():
                got = pages.get(n, collections.Counter())
                per_page[n] = score(got, ref_page)
                for k, v in per_page[n].items():
                    total[k] += v
            doc[route] = {"total": dict(total), "pages": per_page}
        consistency = {}
        for n in ref:
            a, b = routes["E1"].get(n, collections.Counter()), routes["E3"].get(n, collections.Counter())
            hit = sum((a & b).values())
            consistency[n] = {"matched": hit, "e1": sum(a.values()), "e3": sum(b.values()),
                              "only_e1": sorted((a - b).elements())[:40], "only_e3": sorted((b - a).elements())[:40]}
        doc["consistency"] = consistency
        out[pdf.name] = doc
    return out


def tables_of(index_doc: Path) -> list[dict]:
    data = json.loads((index_doc / "tables.json").read_text(encoding="utf-8"))
    slots = {s["slot_id"]: s for s in data["slots"]}
    out = []
    for item in data["prepared_tables"]:
        table = item["adopted_table"]
        first = item["segments"][0]
        slot = slots[first["slot_id"]]
        cells = table["cells"]
        layout = sorted((c["start_row_offset_idx"], c["start_col_offset_idx"], c["row_span"], c["col_span"])
                        for c in cells)
        grid = {(c["start_row_offset_idx"], c["start_col_offset_idx"]): " ".join(c["text"].split()) for c in cells}
        out.append({"table_id": table["table_id"], "tool": table["tool"], "page": slot["page_number"],
                    "bbox": [slot["bbox"][k] for k in ("x0", "y0", "x1", "y1")],
                    "rows": table["row_count"], "columns": table["column_count"], "layout": layout,
                    "grid": grid, "pages": sorted({s["page_number"] for s in item["segments"]})})
    return out


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def compare_table(base: dict, other: dict) -> dict:
    same = (base["rows"], base["columns"], base["layout"]) == (other["rows"], other["columns"], other["layout"])
    diffs = []
    if same:
        distance = chars = 0
        for key, text in sorted(base["grid"].items()):
            got = other["grid"].get(key, "")
            distance += Levenshtein.distance(text, got)
            chars += len(text)
            if got != text:
                diffs.append({"cell": list(key), "original": text, "recognised": got})
    else:
        flat = lambda t: " ".join(t["grid"][k] for k in sorted(t["grid"]))
        a, b = flat(base), flat(other)
        distance, chars = Levenshtein.distance(a, b), len(a)
    layout_a, layout_b = set(base["layout"]), set(other["layout"])
    return {"same_structure": same, "cer": distance / chars if chars else None,
            "cer_basis": "cells" if same else "whole_table", "edit_distance": distance, "characters": chars,
            "rows": other["rows"], "columns": other["columns"], "tool": other["tool"], "pages": other["pages"],
            "layout_only_e0": len(layout_a - layout_b), "layout_only_route": len(layout_b - layout_a),
            "cell_diffs": diffs[:40], "cell_diff_count": len(diffs)}


def table_measures(ws_digital: Path, ws_sim: Path) -> dict:
    out = {}
    base_root = ws_digital / ".rag" / "indexes" / ROUTES["E0"] / "documents"
    for doc in sorted(p.name for p in base_root.iterdir()):
        base_tables = tables_of(base_root / doc)
        others = {route: tables_of(ws_sim / ".rag" / "indexes" / name / "documents" / doc)
                  for route, name in ROUTES.items() if route != "E0"}
        rows = []
        for t in base_tables:
            row = {"table_id": t["table_id"], "tool": t["tool"], "page": t["page"], "pages": t["pages"],
                   "rows": t["rows"], "columns": t["columns"], "routes": {}}
            for route, tables in others.items():
                best = max(((iou(t["bbox"], o["bbox"]), o) for o in tables if o["page"] == t["page"]),
                           key=lambda x: x[0], default=(0.0, None))
                row["routes"][route] = ({"iou": best[0], **compare_table(t, best[1])} if best[1] is not None
                                        and best[0] >= IOU_MIN else {"iou": best[0], "matched": False})
            rows.append(row)
        out[doc] = {"tables": rows, "counts": {route: len(tables) for route, tables in others.items()},
                    "e0_count": len(base_tables)}
    return out


def retrieval(ws_digital: Path, ws_sim: Path) -> dict:
    out = {}
    for route, name in ROUTES.items():
        ws = ws_digital if route == "E0" else ws_sim
        run = max((ws / "reports" / "eval").glob(f"*_{name}_k3_*"))
        data = json.loads((run / "result.json").read_text(encoding="utf-8"))
        out[route] = {"run": run.name, "metrics": data["metrics"], "returned_tokens": data["returned_tokens"],
                      "evidence_modes": data["evidence_modes"],
                      "cases": {c["case_id"]: {"complete": c["complete"], "hit": c["hit"],
                                               "rank": c["first_relevant_rank"], "document": c["document_name"]}
                                for c in data["cases"] if c["answerable"]}}
    return out


def main() -> None:
    ws_digital, ws_sim = Path(sys.argv[1]), Path(sys.argv[2])
    result = {"ocr": ocr_quality(ws_sim), "tables": table_measures(ws_digital, ws_sim),
              "retrieval": retrieval(ws_digital, ws_sim)}
    (HERE / "measures.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str),
                                        encoding="utf-8")


if __name__ == "__main__":
    main()
