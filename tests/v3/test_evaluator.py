from pathlib import Path

from rag.v3.adapters.evaluation_run_store import LocalImmutableEvaluationRunStore
from rag.v3.application.assembly import (
    build_projection, builtin_configuration, index_identity,
)
from rag.v3.application.evaluation_preflight import EvaluationPreflightError
from rag.v3.application.evaluator import FormalEvidenceEvaluator
from rag.v3.contracts.evaluation import (
    EvaluationQueryConfig, EvaluationRunRequest, GroundTruthIdentity,
    TestSetIdentity as SetIdentity,
)


class _Repository:
    def load(self, *args):
        return object()


class _Health:
    def check(self, *args):
        return type("Health", (), {"usable": True, "inspection_complete": True})()


class _Manifest:
    def load(self, *args):
        return object()


class _Vector:
    def list_records(self, *args):
        return ()


class _Preflight:
    def validate(self, *args):
        raise EvaluationPreflightError("test_set_incomplete", "draft mapping")


class _Retriever:
    def retrieve(self, *args):
        raise AssertionError("draft evaluation called Retriever")


def test_failed_preflight_records_invalid_without_retrieval(tmp_path):
    configuration = builtin_configuration("plain_text")
    request = EvaluationRunRequest("draft1", "plain_text", index_identity(configuration),
        build_projection(configuration), EvaluationQueryConfig(3, "query: ", None,
            "strip_v1", "ascending", "chunk_id_ascending", True),
        GroundTruthIdentity("2.0.0", "a" * 64),
        SetIdentity("3.0.0", "b" * 64, "evidence_chunk_mapping_v2"),
        "retrieval_evaluation_v2", "2026-09-26T00:00:00Z")
    store = LocalImmutableEvaluationRunStore(tmp_path)
    evaluator = FormalEvidenceEvaluator(_Repository(), _Preflight(), _Retriever(),
        object(), object(), store, _Manifest(), _Vector(), _Health(), ())
    result = evaluator.run(request)
    assert result.status == "invalid"
    assert result.error.code == "test_set_incomplete"
    assert store.list_completed(request.ground_truth) == ()
