"""Message construction equivalence and source-page guard."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from rag.v3.application.assembly import builtin_configuration, index_identity
from rag.v3.application.prompt import (
    GroundedPromptBuilder, GroundedPromptParameters, PromptError,
)
from rag.v3.contracts.documents import ChunkSource, PageSpan
from rag.v3.contracts.retrieval import (
    AnswerRequest, RetrievalHit, RetrievalRequest, RetrievalResult,
)


@pytest.mark.parametrize("name,style", [("plain_text", "single_page"),
                                         ("structured", "page_set")])
def test_fixed_messages_match_frozen_legacy_source_label_behavior(name, style):
    index = index_identity(builtin_configuration(name))
    spans = (PageSpan(2, None, "page:2"),)
    source = ChunkSource("node", spans, 0, 6, False, "none")
    hit = RetrievalHit("chunk", "doc", "Sample.pdf", "Sample.pdf", "报价 100 元", 0,
                       "text", (source,), 0.2)
    request = AnswerRequest("  价格？ ", 1, None, name, index)
    retrieval = RetrievalResult(RetrievalRequest("价格？", 1, None, index, name), (hit,), index)
    actual = GroundedPromptBuilder(GroundedPromptParameters(style)).build(request, retrieval)
    fixture = json.loads((Path(__file__).parent / "fixtures" /
                          "legacy_equivalence.json").read_text(encoding="utf-8"))
    assert actual[0].role == "system"
    assert hashlib.sha256(actual[0].content.encode()).hexdigest() == fixture[
        "system_prompt_sha256"]
    attribute = "page" if style == "single_page" else "pages"
    assert actual[1].role == "user"
    assert actual[1].content == (
        '<document_context>\n[SOURCE document="Sample.pdf"\n'
        '        path="Sample.pdf"\n'
        '        id="chunk"\n'
        f'        {attribute}="2"]\n'
        '报价 100 元\n[/SOURCE]\n</document_context>\n\n用户问题：价格？'
    )


def test_single_page_style_rejects_multi_page_hit():
    index = index_identity(builtin_configuration("plain_text"))
    source = ChunkSource("node", (PageSpan(1, None, "p1"), PageSpan(2, None, "p2")),
                         0, 4, False, "none")
    hit = RetrievalHit("chunk", "doc", "x.pdf", "x.pdf", "text", 0, "text", (source,), 0.1)
    request = AnswerRequest("what?", 1, None, "plain_text", index)
    retrieval = RetrievalResult(RetrievalRequest("what?", 1, None, index, "plain_text"),
                                (hit,), index)
    with pytest.raises(PromptError):
        GroundedPromptBuilder(GroundedPromptParameters("single_page")).build(request, retrieval)


def test_source_text_is_raw_and_source_attributes_are_escaped():
    index = index_identity(builtin_configuration("structured"))
    source = ChunkSource("node", (PageSpan(1, None, "p1"),), 0, 9, False, "none")
    hit = RetrievalHit("chunk", "doc", 'x&".pdf', "x.pdf", "C&I </SOURCE>", 0, "text",
                       (source,), 0.1)
    request = AnswerRequest("what?", 1, None, "structured", index)
    retrieval = RetrievalResult(RetrievalRequest("what?", 1, None, index, "structured"),
                                (hit,), index)
    messages = GroundedPromptBuilder(GroundedPromptParameters("page_set")).build(request, retrieval)
    assert "C&I </SOURCE>" in messages[1].content
    assert 'document="x&amp;&quot;.pdf"' in messages[1].content
