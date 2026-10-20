"""LLM structure annotation: validation, rendering, chunk planning, the call loop and context."""

import json
from dataclasses import dataclass

from rag.ingest.chunkers.table_plan import plan_chunks
from rag.ingest.language import document_language
from rag.ingest.tables.llm_tables import (
    Lengths, annotation_issues, render_grid, run_annotation, table_context,
)
from rag.ingest.tables.markdown_rows import serialize_markdown_rows
from rag.ingest.tables.structure import rule_structure
from rag.ingest.tables.llm_tables import annotated_structure
from rag.ingest.tables.models import ImplicitMerge, TableAnnotation, TableSection
from tests.test_markdown_rows import cell, grid


def annotated(header=(), sections=(), merges=()) -> TableAnnotation:
    return TableAnnotation(tuple(header), tuple(TableSection(tuple(t), last) for t, last in sections),
                           tuple(ImplicitMerge(*m) for m in merges))


def lines_of(text: str) -> list[str]:
    return text.split("\n")


# Rows: 0 table title, 1-2 header, 3 group A, 4 sub-group A1 (two title rows 4-5), 6-7 data,
# 8 sub-group A2, 9-10 data, 11 total, 12 group B, 13-14 data.
def budget_table():
    rows = [["Budget", None, None], ["Code", "Price", None], [None, "Unit", "Total"],
            ["A", "Civil works", None], ["A1", "Earthworks", ""], ["", "(incl. disposal)", ""],
            ["A1.1", "Digging", "10"], ["A1.2", "Filling", "20"],
            ["A2", "Concrete", ""], ["A2.1", "Slab", "30"], ["A2.2", "Walls", "40"],
            ["", "Subtotal A", "100"], ["B", "Electrical", None], ["B.1", "Cables", "50"],
            ["B.2", "Panels", "60"]]
    merged = (cell("title", "Budget", 0, 0, cols=3), cell("code", "Code", 1, 0, rows=2),
              cell("price", "Price", 1, 1, cols=2), cell("a", "A", 3, 0), cell("aname", "Civil works", 3, 1, cols=2),
              cell("b", "B", 12, 0), cell("bname", "Electrical", 12, 1, cols=2))
    return grid(rows, merged=merged)


BUDGET = annotated(header=(1, 2), sections=[((0,), 14), ((3,), 11), ((4, 5), 7), ((8,), 10), ((12,), 14)])


def test_markdown_writes_header_rows_as_they_are_and_titles_without_labels():
    table = budget_table()
    text = serialize_markdown_rows(table, annotated_structure(BUDGET), table_node_id="t").text
    assert lines_of(text)[:4] == ["| Budget | ← | ← |", "| Code | Price | ← |", "| ↑ | Unit | Total |",
                                  "| A | Civil works | ← |"]
    labeled = serialize_markdown_rows(table, annotated_structure(BUDGET), table_node_id="t", labeled=True)
    assert labeled.rule_version == "llm_labeled_v1"
    assert lines_of(labeled.text)[:3] == ["| Budget | ← | ← |", "| A | Civil works | ← |", "| A1 | Earthworks |  |"]
    assert "| Code: A1.1 | Price / Unit: Digging | Price / Total: 10 |" in lines_of(labeled.text)


def test_every_chunk_repeats_header_and_the_titles_of_its_sections():
    table = budget_table()
    serialized = serialize_markdown_rows(table, annotated_structure(BUDGET), table_node_id="t")
    chunks = plan_chunks(serialized, annotated_structure(BUDGET), lambda text: len(text.split("\n")) <= 9)
    texts = [lines_of(c.text) for c in chunks]
    for lines in texts:
        assert lines[:3] == ["| Budget | ← | ← |", "| Code | Price | ← |", "| ↑ | Unit | Total |"]
    # Sub-group A2 opens a chunk: its parent A is repeated, its own title is in place.
    a2 = next(lines for lines in texts if "| A2.1 | Slab | 30 |" in lines)
    assert a2[3:6] == ["| A | Civil works | ← |", "| A2 | Concrete |  |", "| A2.1 | Slab | 30 |"]
    # The total row stays under group A's title; group B starts with its own title.
    total = next(lines for lines in texts if "|  | Subtotal A | 100 |" in lines)
    assert "| A | Civil works | ← |" in total
    assert not any(lines[-1] in ("| B | Electrical | ← |", "| A1 | Earthworks |  |") for lines in texts)


def test_a_section_too_big_for_a_chunk_is_split_and_its_two_title_rows_repeated():
    table = budget_table()
    serialized = serialize_markdown_rows(table, annotated_structure(BUDGET), table_node_id="t")
    chunks = plan_chunks(serialized, annotated_structure(BUDGET), lambda text: len(text.split("\n")) <= 7)
    second = next(lines_of(c.text) for c in chunks if "| A1.2 | Filling | 20 |" in lines_of(c.text)
                  and "| A1.1 | Digging | 10 |" not in lines_of(c.text))
    assert second[3:7] == ["| A | Civil works | ← |", "| A1 | Earthworks |  |", "|  | (incl. disposal) |  |",
                           "| A1.2 | Filling | 20 |"]


def test_implicit_merge_reads_up_and_resumes_its_value_when_opening_a_chunk():
    table = grid([["Lot", "Item"], ["Lot 1", "a"], ["", "b"], ["", "c"], ["Lot 2", "d"]])
    annotation = annotated(header=(0,), merges=[(0, 1, 3)])
    serialized = serialize_markdown_rows(table, annotated_structure(annotation), table_node_id="t")
    assert lines_of(serialized.text) == ["| Lot | Item |", "| Lot 1 | a |", "| ↑ | b |", "| ↑ | c |",
                                         "| Lot 2 | d |"]
    chunks = plan_chunks(serialized, annotated_structure(annotation), lambda text: len(text.split("\n")) <= 3)
    assert [lines_of(c.text) for c in chunks] == [
        ["| Lot | Item |", "| Lot 1 | a |", "| ↑ | b |"], ["| Lot | Item |", "| ↑ Lot 1 | c |", "| Lot 2 | d |"]]
    labeled = serialize_markdown_rows(table, annotated_structure(annotation), table_node_id="t", labeled=True)
    resumed = plan_chunks(labeled, annotated_structure(annotation), lambda text: len(text.split("\n")) <= 2)[1]
    assert lines_of(resumed.text)[0] == "| Lot: ↑ Lot 1 | Item: c |"


def test_without_annotation_planning_is_the_rule_packing():
    table = grid([["Model", "Qty"], ["A", "1"], ["B", "2"], ["C", "3"]])
    from rag.ingest.tables.header import detect_header
    serialized = serialize_markdown_rows(table, rule_structure(detect_header(table)), table_node_id="t")
    chunks = plan_chunks(serialized, rule_structure(detect_header(table)), lambda text: len(text.split("\n")) <= 3)
    assert [c.text for c in chunks] == ["| Model | Qty |\n| A | 1 |\n| B | 2 |", "| Model | Qty |\n| C | 3 |"]


def test_validation_names_each_broken_rule():
    table = budget_table()
    g = render_grid(table)
    assert annotation_issues(BUDGET, table, g) == []
    bad = annotated(header=(1, 3), sections=[((4,), 9), ((8,), 12), ((14,), 20)], merges=[(2, 6, 7), (0, 5, 6)])
    issues = annotation_issues(bad, table, g)
    assert any(i.startswith("header_rows: [1, 3] are not consecutive") for i in issues)
    assert any("sections[2]: rows [14] to 20 are outside the table" in i for i in issues)
    assert any(i.startswith("sections[0] and sections[1] overlap without one containing the other") for i in issues)
    assert any(i.startswith("header_rows: rows [0] above the header") for i in issues)
    assert any(i.startswith("implicit_merges[0]: in column 2, rows [7]") for i in issues)
    assert any(i.startswith("implicit_merges[1]: the cell at row 5, column 0 is empty") for i in issues)


def test_a_title_above_the_header_must_run_past_it():
    table = budget_table()
    issues = annotation_issues(annotated(header=(1, 2), sections=[((0,), 2)]), table, render_grid(table))
    assert issues == ["sections[0]: a title above the header rows must run past them (last_row 2, header ends at row 2)"]


@dataclass
class _Done:
    text: str
    reasoning: str | None = "thinking"
    prompt_tokens: int | None = 10
    completion_tokens: int | None = 5
    reasoning_tokens: int | None = 3
    cost_usd: float | None = 0.001


class _FakeLLM:
    model = "fake/model"

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def complete_structured(self, messages, name, schema, reasoning_effort):
        self.calls.append(messages)
        return _Done(json.dumps(self.answers.pop(0)))


def _answer(header, lead, sections=()):
    return {"header_rows": header, "sections": [{"title_rows": t, "last_row": last} for t, last in sections],
            "implicit_merges": [], "lead": lead}


def test_issues_are_fed_back_once_and_annotation_and_lead_pass_separately():
    table = grid([["Model", "Qty"], ["A", "1"], ["B", "2"]])
    g = render_grid(table)
    words = " ".join(["word"] * 70)
    llm = _FakeLLM([_answer([0, 2], "Quantities per model, 99 of them."), _answer([0], words)])
    result = run_annotation(llm, table, g, (), Lengths("english", 500), lambda t: len(t.split()), None)
    first, second = result.attempts
    assert first.issues == ("header_rows: [0, 2] are not consecutive rows in ascending order",
                            "lead: numbers not found in the table or the context: 99")
    assert "Your answer did not pass these checks:\n- header_rows: [0, 2]" in llm.calls[1][-1]["content"]
    assert second.issues == ("lead: 70 words, write at most 24 words",)
    assert result.annotation == annotated(header=(0,))
    assert result.lead is None
    assert first.reasoning == "thinking" and first.reasoning_tokens == 3


def test_context_takes_two_nodes_before_the_table_and_keeps_the_end_of_long_ones():
    nodes = [("table", ""), ("paragraph", "old"), ("title", "3. Prices"), ("header", "Page 2"),
             ("paragraph", " ".join(f"w{i}" for i in range(300))), ("table", "")]
    context = table_context(nodes, 5, lambda text: len(text.split()))
    assert [n.kind for n in context] == ["title", "paragraph"]
    assert context[1].text.startswith("… ") and context[1].text.endswith("w299")
    assert len(context[1].text.split()) <= 200
    assert table_context(nodes, 1, lambda text: 1) == ()


def test_document_language_is_chinese_when_most_letters_are_cjk():
    assert document_language(["买方按下表所列节点支付合同价款 PO 25-19"]) == "chinese"
    assert document_language(["El precio de los medidores"]) == "spanish"


def _llm_document(llm, annotation=None, index_text="rows"):
    from rag.ingest.chunkers.structured import chunk_parsed_document
    from rag.ingest.tables.header import detect_header
    from rag.models import BoundingBox, PageSpan, ParsedDocument, TableNode
    table = budget_table()
    header = detect_header(table)
    structure = annotated_structure(annotation) if annotation else rule_structure(header)
    serialized = serialize_markdown_rows(table, structure, table_node_id="tbl")
    node = TableNode("tbl", "s", "#/tables/0", "selected_winner", table, None, header, structure, serialized,
                     "| whole |", index_text, (PageSpan(1, BoundingBox(0, 0, 200, 300), "#/tables/0"),), (), llm)
    document = ParsedDocument("d", "d.pdf", "d.pdf", "a" * 64, (node,), (), "english")

    class Lines:
        def count_passage(self, text):
            return len(text.split("\n"))
    return chunk_parsed_document(document, Lines(), max_input_tokens=9, overlap_tokens=0)


def _result(task, **fields):
    from rag.ingest.tables.models import TableLLMResult
    return TableLLMResult(task, "m", "v", "medium", (), (), **fields)


def test_chunks_repeat_section_titles_as_context_and_carry_the_lead():
    batch = _llm_document(_result("annotation", annotation=BUDGET, lead="Budget by item."), BUDGET,
                          "rows_with_lead")
    assert all(c.kind == "table" and c.text.startswith("Budget by item.\n| Budget | ← | ← |")
               for c in batch.chunks)
    assert all(c.token_count <= 9 for c in batch.chunks)
    # A chunk opening inside group A repeats the table title, the header rows and A's title.
    inside = next(c for c in batch.chunks if "| A2.1 | Slab | 30 |" in c.text)
    kinds = [s.context_kind for s in inside.sources]
    assert kinds[:4] == ["section_title", "table_header", "table_header", "section_title"]


def test_a_failed_lead_or_no_lead_setting_writes_none():
    for llm, index_text in ((_result("annotation", annotation=BUDGET, lead=None), "rows_with_lead"),
                            (_result("annotation", annotation=BUDGET, lead="Budget."), "rows")):
        assert not any(c.text.startswith("Budget") and not c.text.startswith("| Budget")
                       for c in _llm_document(llm, BUDGET, index_text).chunks)


def test_a_summary_replaces_the_table_chunks_and_covers_the_whole_table():
    [chunk] = _llm_document(_result("summary", summary="Budget of civil and electrical works."),
                            index_text="summary").chunks
    assert chunk.kind == "table_summary" and chunk.text == "Budget of civil and electrical works."
    assert chunk.sources[0].source_text_start is None and not chunk.sources[0].repeated_context
    fallback = _llm_document(_result("summary", summary=None), index_text="summary").chunks
    assert {c.kind for c in fallback} == {"table"}
