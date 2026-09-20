from pathlib import Path

from rag.evaluator import Evaluator, load_evaluation_cases
from rag.models import RetrievalHit, V2RetrievalHit


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


def test_retrieval_eval_accepts_any_v2_source_page(tmp_path: Path) -> None:
    path = tmp_path / "questions.json"
    path.write_text(
        '[{"id":"v2","question_zh":"q","question_en":"q",'
        '"expected_documents":["order.pdf"],"expected_pages":[2],'
        '"expected_terms":[],"answerable":true}]', encoding="utf-8")

    class V2Retriever:
        def search(self, _question):
            return [V2RetrievalHit("c", "doc", "order.pdf", "documents/order.pdf", 0,
                                   "table", "evidence", 0.1, (1, 2), ())]

    results = Evaluator(V2Retriever()).evaluate_retrieval(load_evaluation_cases(path))  # type: ignore[arg-type]
    assert [item.passed for item in results] == [True, True]
    assert all("p1,2" in item.detail for item in results)


def test_loader_discovers_per_document_single_question_files(tmp_path: Path) -> None:
    evaluation_dir = tmp_path / "eval"
    evaluation_dir.mkdir()
    payload = ('[{"id":"Q001","question":"问题","expected_documents":["a.pdf"],'
               '"expected_pages":[1],"reference_answer":"答案","answerable":true}]')
    (evaluation_dir / "a.json").write_text(payload, encoding="utf-8")
    (evaluation_dir / "b.json").write_text(payload.replace("a.pdf", "b.pdf"), encoding="utf-8")

    cases = load_evaluation_cases(evaluation_dir / "questions.json")
    assert [item.case_id for item in cases] == ["a:Q001", "b:Q001"]
    assert all(item.question_en == "" and item.expected_terms == () for item in cases)


def test_live_case_without_explicit_expected_terms_is_reported_unscored(tmp_path: Path) -> None:
    path = tmp_path / "questions.json"
    path.write_text('[{"id":"q","question":"问题","expected_documents":["a.pdf"],'
                    '"expected_pages":[1],"answerable":true}]', encoding="utf-8")

    class Pipeline:
        def ask(self, _question):
            return type("Answer", (), {"answer": "模型回答"})()

    results = Evaluator(FakeRetriever(), Pipeline()).evaluate_live(load_evaluation_cases(path))  # type: ignore[arg-type]
    assert len(results) == 1 and results[0].passed is None

