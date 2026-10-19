"""Correct table structure answers: finding a table's answer by position, and naming differences."""

import json

from rag.eval.table_structure import differences, find_answer, load_answers
from rag.ingest.tables.models import ImplicitMerge, TableAnnotation, TableSection
from rag.models import BoundingBox, PageSpan


def annotation(header=(), sections=(), merges=()):
    return TableAnnotation(tuple(header), tuple(TableSection(tuple(t), last) for t, last in sections),
                           tuple(ImplicitMerge(*m) for m in merges))


def test_a_table_is_found_by_overlapping_region_on_the_same_page(tmp_path):
    (tmp_path / "doc.json").write_text(json.dumps({
        "schema_version": "table_structure_v1", "document_id": "d1", "tables": [
            {"regions": [{"page": 2, "bbox": [100, 100, 400, 300]}], "header_rows": [0], "sections": [],
             "implicit_merges": []},
            {"regions": [{"page": 3, "bbox": [100, 100, 400, 300]}], "header_rows": [0, 1],
             "sections": [{"title_rows": [2], "last_row": 5}], "implicit_merges": []}]}), encoding="utf-8")
    answers = load_answers(tmp_path, "d1")
    assert load_answers(tmp_path, "other") is None
    span = lambda page, box: (PageSpan(page, BoundingBox(*box), "ref"),)
    assert find_answer(answers, span(3, (110, 120, 400, 310))).annotation.header_rows == (0, 1)
    assert find_answer(answers, span(3, (100, 250, 400, 600))) is None      # overlap too small
    assert find_answer(answers, span(4, (100, 100, 400, 300))) is None      # another page


def test_differences_name_what_is_missing_extra_or_mis_ranged():
    right = annotation((0, 1), [((3,), 15), ((16, 17), 20)], [(0, 4, 6)])
    assert differences(right, right) == []
    given = annotation((0,), [((3,), 16), ((21,), 25)], [(0, 4, 6), (6, 1, 2)])
    assert differences(right, given) == [
        "表头行应为 r0–r1，标成了 r0",
        "节 r3 应管到 r15，标成了 r16",
        "漏标节：标题 r16–r17、管到 r20 的节",
        "多标节：标题 r21、管到 r25 的节",
        "多标隐式合并：c6 列 r1 的值沿用到 r2"]
