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


def test_stored_word_round_trips():
    from rag.eval.anchors import format_word, parse_word
    w = word("Cl", 60.004, 10.5)
    assert parse_word(format_word(w)) == (1, (60.0, 10.5, 72.0, 18.5))
