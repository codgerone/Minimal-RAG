"""Whole-table store: every table with its serialized text and the chunks it was split into."""

import pytest

from rag.index.tables import TableStore, collect_tables, table_id
from rag.ingest.chunkers.structured import chunk_parsed_document
from rag.models import PageSpan, ParsedDocument, TextNode
from test_regions import _table_node


class CharacterCounter:
    def count_passage(self, text: str) -> int:
        return len(text)


def _document() -> ParsedDocument:
    text = TextNode("p", "paragraph", "Delivery schedule below.", (PageSpan(1, None, "p"),), None)
    return ParsedDocument("d", "d.pdf", "d.pdf", "a" * 64, (text, _table_node()), ())


def test_a_split_table_is_stored_once_with_its_full_text_and_all_its_chunks():
    document = _document()
    batch = chunk_parsed_document(document, CharacterCounter(), max_input_tokens=40)
    table_chunks = [c.chunk_id for c in batch.chunks if c.kind == "table"]
    assert len(table_chunks) == 2
    [stored] = collect_tables(document, batch)
    assert stored.table_id == table_id("d", "tbl")
    assert stored.text == document.nodes[1].serialized.text
    assert stored.chunk_ids == tuple(table_chunks)


def test_store_maps_each_table_chunk_to_its_whole_table(tmp_path):
    document = _document()
    batch = chunk_parsed_document(document, CharacterCounter(), max_input_tokens=40)
    store = TableStore(tmp_path / "tables.sqlite")
    store.replace_document("d", collect_tables(document, batch))
    text_chunk, *table_chunks = [c.chunk_id for c in batch.chunks]
    found = store.tables_for_chunks([text_chunk, *table_chunks])
    assert set(found) == set(table_chunks)
    assert {t.chunk_ids for t in found.values()} == {tuple(table_chunks)}
    assert store.counts_by_document() == {"d": 1}
    store.delete_document("d")
    assert store.counts_by_document() == {} and store.tables_for_chunks(table_chunks) == {}


def test_a_table_without_chunks_is_an_error():
    document = _document()
    batch = chunk_parsed_document(ParsedDocument("d", "d.pdf", "d.pdf", "a" * 64, document.nodes[:1], ()),
                                  CharacterCounter(), max_input_tokens=40)
    with pytest.raises(ValueError, match="没有对应的 chunk"):
        collect_tables(document, batch)
