from rag.embeddings import E5TokenCounter
from rag.v2.chunking import chunk_parsed_document, normalize_node_text
from rag.v2.document_models import LayoutListItem, ListNode, ParsedDocument, StructuredTable, TableNode, TextNode
from rag.v2.table_header import HeaderDecision
from rag.v2.table_serialization import SerializedTable, SerializedTableLine


class CharacterCounter:
    def count_passage(self, text: str) -> int:
        return len("passage: " + text) + 2

    def count_text(self, text: str) -> int:
        return len(text)


def _document(*nodes):
    return ParsedDocument("doc", "a.pdf", "documents/a.pdf", "hash", tuple(nodes), ())


def test_consecutive_text_nodes_merge_but_list_starts_independent_stream() -> None:
    nodes = (
        TextNode("a", "paragraph", " first ", "#/texts/0", ()),
        TextNode("b", "paragraph", "second", "#/texts/1", ()),
        ListNode("list", (LayoutListItem("i", "#/texts/2", "item", 0, None, None, ()),),
                 ("#/texts/2",), ()),
        TextNode("c", "paragraph", "third", "#/texts/3", ()),
    )
    result = chunk_parsed_document(_document(*nodes), CharacterCounter(), max_input_tokens=50, overlap_tokens=5)
    assert [chunk.kind for chunk in result.chunks] == ["text", "list", "text"]
    assert result.chunks[0].text == "first\n\nsecond"
    assert result.chunks[1].text == "- item"


def test_long_text_uses_boundaries_overlap_and_complete_nonrepeat_coverage() -> None:
    text = "第一句。第二句很长。第三句也很长。第四句结束。"
    node = TextNode("a", "paragraph", text, "#/texts/0", ())
    result = chunk_parsed_document(_document(node), CharacterCounter(), max_input_tokens=24, overlap_tokens=5)
    chunks = result.chunks
    assert len(chunks) > 1
    assert all(chunk.token_count <= 24 for chunk in chunks)
    ranges = [source for chunk in chunks for source in chunk.sources if not source.repeated_context]
    assert "".join(text[source.source_text_start:source.source_text_end] for source in ranges) == text
    assert any(source.repeated_context for chunk in chunks[1:] for source in chunk.sources)


def test_title_has_no_overlap_and_empty_node_emits_warning() -> None:
    empty = TextNode("empty", "paragraph", " \r\n ", "#/texts/0", ())
    title = TextNode("title", "title", "ABCDEFGHIJKLMNO", "#/texts/1", ())
    result = chunk_parsed_document(_document(empty, title), CharacterCounter(),
                                   max_input_tokens=16, overlap_tokens=5)
    assert any(item.code == "empty_text_node" for item in result.warnings)
    assert not any(source.repeated_context for chunk in result.chunks for source in chunk.sources)
    assert normalize_node_text(" a\r\nb\r ") == "a\nb"


def test_nested_list_repeats_parent_as_context_when_split() -> None:
    items = (
        LayoutListItem("parent", "#/texts/0", "Parent", 0, "1.", None, ()),
        LayoutListItem("child1", "#/texts/1", "Child A", 1, "-", "parent", ()),
        LayoutListItem("child2", "#/texts/2", "Child B", 1, "-", "parent", ()),
    )
    node = ListNode("list", items, ("#/texts/0", "#/texts/1", "#/texts/2"), ())
    result = chunk_parsed_document(_document(node), CharacterCounter(), max_input_tokens=32, overlap_tokens=4)
    assert len(result.chunks) == 2
    assert result.chunks[1].text.startswith("1. Parent\n  - Child B")
    assert any(source.context_kind == "list_ancestor" for source in result.chunks[1].sources)


def test_oversized_nested_item_degrades_ancestor_to_stable_position_locator() -> None:
    items = (
        LayoutListItem("parent", "#/texts/0", "Very long parent context", 0, "1.", None, ()),
        LayoutListItem("child", "#/texts/1", "abcdefghijklmno", 1, "-", "parent", ()),
    )
    node = ListNode("list", items, ("#/texts/0", "#/texts/1"), ())
    result = chunk_parsed_document(_document(node), CharacterCounter(),
                                   max_input_tokens=52, overlap_tokens=3)
    child_chunks = [chunk for chunk in result.chunks if any(
        source.node_id == "child" and not source.repeated_context for source in chunk.sources)]
    assert child_chunks
    assert all(chunk.text.startswith(("〔上级项：第1项〕\n", "〔List list，父项路径见完整列表〕\n"))
               for chunk in child_chunks)
    assert all(any(source.context_kind == "fallback_locator" for source in chunk.sources)
               for chunk in child_chunks)


def test_e5_counter_includes_prefix_special_tokens_and_disables_truncation() -> None:
    calls = []

    class Tokenizer:
        def __call__(self, text, **kwargs):
            calls.append((text, kwargs))
            return {"input_ids": [1, 2, 3, 4]}

    counter = E5TokenCounter("model", "revision", tokenizer_factory=lambda *args, **kwargs: Tokenizer())
    assert counter.count_passage("正文") == 4
    assert calls == [("passage: 正文", {"add_special_tokens": True, "truncation": False})]


def test_split_undetermined_table_repeats_header_as_context() -> None:
    lines = (
        SerializedTableLine("l1", "header", "表头未确定。", (), ()),
        SerializedTableLine("l2", "data", "第1行：A。", (0,), ("a",)),
        SerializedTableLine("l3", "data", "第2行：B。", (1,), ("b",)),
    )
    serialized = SerializedTable("\n".join(item.text for item in lines), lines)
    header = HeaderDecision("undetermined", "no_supported_candidate", (), (), (), None, None, ())
    table = StructuredTable("table", "docling", "accurate", 2, 1, (), (), None, (), ())
    node = TableNode("node", "slot_001", "#/tables/0", "docling_native_fallback",
                     table, None, header, serialized, ())
    result = chunk_parsed_document(_document(node), CharacterCounter(), max_input_tokens=25, overlap_tokens=4)
    assert len(result.chunks) == 2
    assert result.chunks[1].text.startswith("表头未确定。\n")
    assert result.chunks[1].sources[0].context_kind == "table_header"


def test_split_table_sources_cover_serialized_lines_without_counting_repeated_header() -> None:
    lines = (
        SerializedTableLine("l1", "header", "表头未确定。", (), ()),
        SerializedTableLine("l2", "data", "第1行：" + "A" * 100 + "。", (0,), ("a",)),
    )
    serialized = SerializedTable("\n".join(item.text for item in lines), lines)
    header = HeaderDecision("undetermined", "no_supported_candidate", (), (), (), None, None, ())
    table = StructuredTable("table", "docling", "accurate", 1, 1, (), (), None, (), ())
    node = TableNode("node", "slot_001", "#/tables/0", "docling_native_fallback",
                     table, None, header, serialized, ())
    result = chunk_parsed_document(_document(node), CharacterCounter(),
                                   max_input_tokens=48, overlap_tokens=4)

    covered = []
    for chunk in result.chunks:
        for source in chunk.sources:
            if not source.repeated_context and source.source_text_start is not None:
                covered.append(serialized.text[source.source_text_start:source.source_text_end])
    assert "".join(covered) == "".join(item.text for item in lines)
    assert all(chunk.token_count <= 48 for chunk in result.chunks)


def test_vertical_merge_fact_is_repeated_when_covered_rows_split() -> None:
    lines = (
        SerializedTableLine("m", "merged", "第1行至第2行共享第1列，内容 = \"区域\"。", (0, 1), ("m",)),
        SerializedTableLine("r1", "data", "第1行：数量 = \"100000\"。", (0,), ("a",)),
        SerializedTableLine("r2", "data", "第2行：数量 = \"200000\"。", (1,), ("b",)),
    )
    serialized = SerializedTable("\n".join(item.text for item in lines), lines)
    header = HeaderDecision("undetermined", "no_supported_candidate", (), (), (), None, None, ())
    table = StructuredTable("table", "docling", "accurate", 2, 2, (), (), None, (), ())
    node = TableNode("node", "slot_001", "#/tables/0", "docling_native_fallback",
                     table, None, header, serialized, ())
    result = chunk_parsed_document(_document(node), CharacterCounter(),
                                   max_input_tokens=58, overlap_tokens=4)
    row_chunks = [chunk for chunk in result.chunks if "数量" in chunk.text]
    assert len(row_chunks) == 2
    assert all("共享第1列" in chunk.text for chunk in row_chunks)
    assert any(source.context_kind == "merged_cell" for source in row_chunks[-1].sources)
