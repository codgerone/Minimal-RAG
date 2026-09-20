from __future__ import annotations

from rag.evaluation_models import (
    CaseEvaluationFact, EvaluationDocumentResult, RetrievedChunkSnapshot,
)
from rag.evaluation_reports import render_document


def test_render_document_shows_text_and_escapes_html_without_embeddings() -> None:
    hit = RetrievedChunkSnapshot(
        1, "chunk-1", "doc-1", "sample.pdf", "sample.pdf", (2,), 0, "text",
        "amount < 100", 0.2, 0.8, (), False, False,
    )
    case = CaseEvaluationFact(
        "Q1", "completed", "question <tag>", False, "unknown", (), (hit,), (), None, None,
    )
    document = EvaluationDocumentResult(
        "run-1", "sample", "doc-1", "sample.pdf", "sample.pdf", "a" * 64, (case,),
    )

    rendered = render_document(document)

    assert "question &lt;tag&gt;" in rendered
    assert "amount &lt; 100" in rendered
    assert "chunk-1" not in rendered
    assert "embedding" not in rendered.casefold()
