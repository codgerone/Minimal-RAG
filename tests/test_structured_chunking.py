"""V3 structured chunk output agrees with the fixed prior algorithm on text cases."""

from __future__ import annotations

import json
from pathlib import Path

from rag.ingest.chunkers.structured import chunk_parsed_document
from rag.models import ListItem, ListNode, PageSpan, ParsedDocument, TextNode


class CharacterCounter:
    def count_passage(self, text: str) -> int:
        return len("passage: " + text)

    def count_text(self, text: str) -> int:
        return len(text)


def test_long_multilingual_text_retains_boundaries_ids_and_source_ranges():
    text = "订单编号 12345。交付日期 2026-09-25。客户要求保留全部来源。"
    source_new = PageSpan(1, None, "#/texts/0")
    new = ParsedDocument("doc123", "sample.pdf", "sample.pdf", "a" * 64,
                         (TextNode("node1", "paragraph", text, (source_new,), "#/texts/0"),), ())
    result = chunk_parsed_document(new, CharacterCounter(), max_input_tokens=30, overlap_tokens=4)
    fixture = json.loads((Path(__file__).parent / "fixtures" /
                          "legacy_equivalence.json").read_text(encoding="utf-8"))
    assert [[item.chunk_id, item.text, item.token_count, item.fragment_index,
             item.fragment_count,
             [[source.source_text_start, source.source_text_end,
               source.repeated_context] for source in item.sources]]
            for item in result.chunks] == fixture["structured"]


def _node(node_id: str, text: str, kind: str = "paragraph") -> TextNode:
    return TextNode(node_id, kind, text, (PageSpan(1, None, f"#/{node_id}"),), f"#/{node_id}")


def _list(node_id: str, *items: tuple[str, str, int, str | None]) -> ListNode:
    span = (PageSpan(1, None, f"#/{node_id}"),)
    return ListNode(node_id, tuple(ListItem(item_id, text, level, "-", parent, span, f"#/{item_id}")
                                   for item_id, text, level, parent in items), (f"#/{node_id}",), span)


def _document(*nodes) -> ParsedDocument:
    return ParsedDocument("doc123", "sample.pdf", "sample.pdf", "a" * 64, nodes, ())


def test_list_items_merge_with_neighbouring_text():
    document = _document(_node("t1", "Intro:"), _node("t2", "Label:", "title"),
                         _list("l1", ("i1", "first", 0, None), ("i2", "second", 0, None)),
                         _node("t3", "After."))
    chunks = chunk_parsed_document(document, CharacterCounter(), max_input_tokens=100).chunks
    assert [(c.kind, c.text) for c in chunks] == [
        ("list", "Intro:\n\nLabel:\n\n- first\n- second\n\nAfter.")]
    assert [s.node_id for s in chunks[0].sources] == ["t1", "t2", "i1", "i2", "t3"]


def test_list_over_budget_breaks_between_items():
    document = _document(_node("t1", "Intro text:"),
                         _list("l1", ("i1", "aaaaaaaaaa", 0, None), ("i2", "bbbbbbbbbb", 0, None),
                               ("i3", "cccccccccc", 0, None)))
    # "passage: " (9) + "Intro text:\n\n- aaaaaaaaaa\n- bbbbbbbbbb" (38) = 47
    chunks = chunk_parsed_document(document, CharacterCounter(), max_input_tokens=47).chunks
    assert [c.text for c in chunks] == ["Intro text:\n\n- aaaaaaaaaa\n- bbbbbbbbbb", "- cccccccccc"]
    assert all(c.fragment_count == 1 for c in chunks)


def test_nested_item_opening_a_chunk_repeats_its_ancestor():
    document = _document(_node("t1", "x" * 20),
                         _list("l1", ("i1", "parent", 0, None), ("i2", "child one", 1, "i1"),
                               ("i3", "child two", 1, "i1")))
    # second chunk opens at i3, so its ancestor i1 is repeated as context
    chunks = chunk_parsed_document(document, CharacterCounter(), max_input_tokens=60).chunks
    assert [c.text for c in chunks] == ["x" * 20 + "\n\n- parent\n  - child one", "- parent\n  - child two"]
    first, second = chunks[1].sources
    assert (first.node_id, first.repeated_context, first.context_kind) == ("i1", True, "list_ancestor")
    assert (second.node_id, second.repeated_context) == ("i3", False)


def test_separate_lists_are_joined_with_a_blank_line():
    document = _document(_list("l1", ("i1", "one", 0, None)), _list("l2", ("i2", "two", 0, None)))
    chunks = chunk_parsed_document(document, CharacterCounter(), max_input_tokens=100).chunks
    assert [c.text for c in chunks] == ["- one\n\n- two"]
