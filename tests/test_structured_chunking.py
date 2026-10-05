"""V3 structured chunk output agrees with the fixed prior algorithm on text cases."""

from __future__ import annotations

import json
from pathlib import Path

from rag.ingest.chunkers.structured import chunk_parsed_document
from rag.models import PageSpan, ParsedDocument, TextNode


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
