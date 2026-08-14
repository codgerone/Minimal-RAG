from pathlib import Path

from rag.evaluator import Evaluator, load_evaluation_cases
from rag.models import RetrievalHit


class FakeRetriever:
    def search(self, question: str) -> list[RetrievalHit]:
        return [
            RetrievalHit(
                "doc-p1-c00",
                "doc",
                "order.pdf",
                "order.pdf",
                1,
                0,
                "evidence",
                0.1,
            )
        ]


def test_retrieval_eval_scores_answerable_and_reports_unanswerable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "questions.json"
    path.write_text(
        """[
          {
            "id": "answerable",
            "question_zh": "问题",
            "question_en": "question",
            "expected_documents": ["order.pdf"],
            "expected_pages": [1],
            "expected_terms": ["value"],
            "answerable": true
          },
          {
            "id": "unknown",
            "question_zh": "未知",
            "question_en": "unknown",
            "expected_documents": [],
            "expected_pages": [],
            "expected_terms": [],
            "answerable": false
          }
        ]""",
        encoding="utf-8",
    )

    results = Evaluator(FakeRetriever()).evaluate_retrieval(  # type: ignore[arg-type]
        load_evaluation_cases(path)
    )

    assert [result.passed for result in results] == [True, True, None, None]

