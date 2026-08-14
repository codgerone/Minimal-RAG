from rag.models import RetrievalHit
from rag.prompt import build_messages, format_context


def _hit() -> RetrievalHit:
    return RetrievalHit(
        chunk_id='doc-p1-c00<&"',
        document_id="doc",
        document_name='Order & "Test".pdf',
        relative_path="sales/<order>.pdf",
        page_number=1,
        chunk_index=0,
        text="Total: USD 100.",
        distance=0.1,
    )


def test_context_contains_real_escaped_source_and_original_text() -> None:
    context = format_context([_hit()])

    assert 'document="Order &amp; &quot;Test&quot;.pdf"' in context
    assert 'path="sales/&lt;order&gt;.pdf"' in context
    assert 'id="doc-p1-c00&lt;&amp;&quot;"' in context
    assert 'page="1"' in context
    assert "Total: USD 100." in context
    assert "passage:" not in context


def test_prompt_enforces_grounding_refusal_and_instruction_isolation() -> None:
    messages = build_messages("总金额？", [_hit()])
    system = messages[0]["content"]

    assert "只能使用" in system
    assert "没有足够信息" in system
    assert "不可执行" in system
    assert "Chunk ID" in system
    assert "总金额？" in messages[1]["content"]

