from rag.eval.anchors import PdfWord, locate_excerpt, normalize
from rag.eval.dataset import Excerpt

H = 8.0   # word height in these synthetic pages


def word(text, x, y, page=1):
    return PdfWord(page, (x, y, x + 6.0 * len(text), y + H), text, normalize(text))


def excerpt(text, hint=None, pages=(1,)):
    return Excerpt("x1", "d1", "doc.pdf", pages, text, False, hint)


def texts(anchor):
    return [(w.text, w.bbox[0], w.bbox[1]) for w in anchor.words]


def test_prose_piece_follows_a_line_wrap():
    words = [word("Payment", 100, 10), word("is", 160, 10), word("due", 100, 20), word("soon", 130, 20)]
    anchor = locate_excerpt(excerpt("Payment is due soon."), words)
    assert anchor.status == "located"
    assert len(anchor.words) == 4


def test_repeated_value_is_taken_from_the_labelled_row():
    words = [word("ELCTO", 10, 10), word("500", 200, 10), word("ENSA", 10, 30), word("500", 200, 30)]
    anchor = locate_excerpt(excerpt("ENSA | 500"), words)
    assert anchor.status == "ambiguous"
    assert texts(anchor) == [("ENSA", 10, 30), ("500", 200, 30)]


def test_label_and_value_are_not_joined_across_an_empty_cell():
    # "HXE12ESX 20,000.00" with col 3 and col 4 holding the same value; only the hint tells them apart.
    words = [word("3rd", 200, 0), word("4th", 300, 0),
             word("HXE12ESX", 10, 20), word("20,000.00", 200, 20), word("20,000.00", 300, 20)]
    without = locate_excerpt(excerpt("HXE12ESX 20,000.00"), words)
    assert [p.candidates for p in without.pieces] == [1, 2]
    hinted = locate_excerpt(excerpt("HXE12ESX 20,000.00", hint="4th"), words)
    assert ("20,000.00", 300, 20) in texts(hinted)


def test_a_word_found_nowhere_is_reported_not_guessed():
    words = [word("Meter", 10, 10), word("Cl", 60, 10), word("0.2S", 80, 10)]
    anchor = locate_excerpt(excerpt("Meter CI 0.2S"), words)
    assert anchor.status == "missing"
    assert [m for p in anchor.pieces for m in p.missing] == ["CI"]


def test_a_hint_that_is_not_unique_is_reported():
    words = [word("4th", 10, 0), word("4th", 50, 0), word("7", 10, 20), word("7", 50, 20)]
    anchor = locate_excerpt(excerpt("7", hint="4th"), words)
    assert anchor.status == "missing"


def test_row_label_is_taken_from_the_row_not_from_near_a_section_title():
    # Section title "HXE33K-S1" sits just below an earlier "ENSA" row; the excerpt's ENSA is the
    # one on the same line as its values.
    words = [word("ENSA", 10, 100), word("9,910", 200, 100),
             word("HXE33K-S1", 120, 115),
             word("ENSA", 10, 190), word("5,018", 200, 190), word("1,153", 260, 190)]
    anchor = locate_excerpt(excerpt("ENSA | HXE33K-S1 | 5,018 | 1,153"), words)
    assert ("ENSA", 10, 190) in texts(anchor)


def test_written_anchor_loads_back_and_goes_stale_when_text_changes(tmp_path):
    import json
    from rag.eval.anchors import write_anchors
    from rag.eval.dataset import load_dataset
    x = {"excerpt_id": "x1", "document_id": "d1", "document_name": "doc.pdf", "page_numbers": [1],
         "text": "Meter Cl"}
    data = {"document_id": "d1", "document_name": "doc.pdf", "file_hash": "h", "cases": [
        {"case_id": "C1", "question": "q", "reference_answer": "a", "answerable": True,
         "excerpts": [x], "evidence_groups": []}]}
    (tmp_path / "manifest.json").write_text('{"dataset_version": "t"}', encoding="utf-8")
    (tmp_path / "doc.json").write_text(json.dumps(data), encoding="utf-8")
    words = [word("Meter", 10, 10), word("Cl", 60.004, 10)]
    write_anchors(tmp_path, {("C1", "x1"): locate_excerpt(excerpt("Meter Cl"), words)})
    loaded = load_dataset(tmp_path).cases[0].excerpts[0]
    assert loaded.anchor_state == "ok"
    assert loaded.anchor == (("Meter Cl", ((1, (10.0, 10.0, 40.0, 18.0)), (1, (60.0, 10.0, 72.0, 18.0)))),)
    data = json.loads((tmp_path / "doc.json").read_text(encoding="utf-8"))
    data["cases"][0]["excerpts"][0]["text"] = "Meter CI"
    (tmp_path / "doc.json").write_text(json.dumps(data), encoding="utf-8")
    assert load_dataset(tmp_path).cases[0].excerpts[0].anchor_state == "stale"


def test_a_misread_word_on_a_scanned_page_is_matched_approximately():
    words = [word("Meter", 10, 10), word("Cl", 60, 10), word("0.2S", 80, 10)]
    anchor = locate_excerpt(excerpt("Meter CI 0.2S"), words, scanned=frozenset({1}))
    assert anchor.status == "located"
    assert anchor.approximate and anchor.distances == (1,)
    assert [p.distance for p in anchor.pieces] == [1]
    assert len(anchor.words) == 3


def test_an_approximate_match_needs_a_distance_within_a_fifth_of_the_piece():
    words = [word("Total", 10, 10), word("83,240.74", 60, 10)]
    near = locate_excerpt(excerpt("Total 83,240.14"), words, scanned=frozenset({1}))
    assert near.distances == (1,)
    far = locate_excerpt(excerpt("Total 12,697.74"), words, scanned=frozenset({1}))
    assert far.status == "missing"


def test_an_exact_piece_on_a_scanned_page_stays_exact():
    words = [word("Payment", 100, 10), word("is", 160, 10), word("due", 100, 20)]
    anchor = locate_excerpt(excerpt("Payment is due"), words, scanned=frozenset({1}))
    assert anchor.status == "located" and not anchor.approximate


def test_an_approximate_piece_is_written_with_what_ocr_read(tmp_path):
    import json
    from rag.eval.anchors import write_anchors
    from rag.eval.dataset import load_dataset
    x = {"excerpt_id": "x1", "document_id": "d1", "document_name": "doc.pdf", "page_numbers": [1],
         "text": "Meter CI"}
    data = {"document_id": "d1", "document_name": "doc.pdf", "file_hash": "h", "cases": [
        {"case_id": "C1", "question": "q", "reference_answer": "a", "answerable": True,
         "excerpts": [x], "evidence_groups": []}]}
    (tmp_path / "manifest.json").write_text('{"dataset_version": "t"}', encoding="utf-8")
    (tmp_path / "doc.json").write_text(json.dumps(data), encoding="utf-8")
    words = [word("Meter", 10, 10), word("Cl", 60, 10)]
    write_anchors(tmp_path, {("C1", "x1"): locate_excerpt(excerpt("Meter CI"), words, frozenset({1}))})
    piece = json.loads((tmp_path / "doc.json").read_text(encoding="utf-8"))["cases"][0]["excerpts"][0]["anchor"]["pieces"][0]
    assert piece["approximate"] is True and piece["distance"] == 1 and piece["ocr_text"] == "Meter Cl"
    loaded = load_dataset(tmp_path).cases[0].excerpts[0]
    assert loaded.anchor[0][0] == "Meter Cl"   # chunk text is compared with what OCR read


def test_dataset_fingerprint_ignores_crlf_line_endings(tmp_path):
    from rag.eval.dataset import load_dataset
    data = '{"document_id": "d1", "document_name": "doc.pdf", "file_hash": "h",\n "cases": []}\n'
    (tmp_path / "manifest.json").write_text('{"dataset_version": "t"}', encoding="utf-8")
    (tmp_path / "doc.json").write_bytes(data.encode("utf-8"))
    lf = load_dataset(tmp_path).fingerprint
    (tmp_path / "doc.json").write_bytes(data.replace("\n", "\r\n").encode("utf-8"))
    assert load_dataset(tmp_path).fingerprint == lf
    (tmp_path / "doc.json").write_bytes(data.replace('"h"', '"h2"').encode("utf-8"))
    assert load_dataset(tmp_path).fingerprint != lf
