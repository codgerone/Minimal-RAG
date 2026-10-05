from types import SimpleNamespace

from rag.v3.application.assembly import builtin_configuration, index_identity
from rag.v3.application.live_smoke import run_live_smoke


class Repository:
    def load_ground_truth(self):
        return SimpleNamespace(review_status="approved"), (SimpleNamespace(cases=(
            SimpleNamespace(case_id="q1", question="first"),
            SimpleNamespace(case_id="q2", question="second"),
        )),)


class Chatbot:
    def answer(self, request):
        if request.question == "second":
            raise RuntimeError("network")
        return SimpleNamespace(answer="grounded answer")


def test_live_smoke_reports_execution_without_formal_metrics():
    result = run_live_smoke(Repository(), Chatbot(),
        index_identity(builtin_configuration("plain_text")), "plain_text", 3)
    assert tuple(item.status for item in result) == ("completed", "failed")
    assert result[0].answer == "grounded answer"
    assert result[1].answer is None
    assert result[1].error_code is not None
