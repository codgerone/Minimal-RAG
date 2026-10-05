"""Formal V3 evidence evaluation orchestration over injected ports."""

from __future__ import annotations

from rag.v3.contracts.evaluation import (
    EvaluationDocumentResult, EvaluationError, EvaluationRun,
    EvaluationRunRequest,
)
from rag.v3.contracts.retrieval import RetrievalRequest


class FormalEvidenceEvaluator:
    def __init__(self, repository: object, preflight: object, retriever: object,
                 calculator: object, reporter: object, run_store: object,
                 manifest_store: object, vector_store: object, health: object,
                 sources: tuple):
        self.repository = repository
        self.preflight = preflight
        self.retriever = retriever
        self.calculator = calculator
        self.reporter = reporter
        self.run_store = run_store
        self.manifest_store = manifest_store
        self.vector_store = vector_store
        self.health = health
        self.sources = sources

    @staticmethod
    def _error(stage: str, code: str, exc: Exception,
               document_id: str | None = None,
               case_id: str | None = None) -> EvaluationError:
        scope = "case" if case_id else "document" if document_id else "run"
        return EvaluationError(stage, code, scope, document_id, case_id, str(exc))

    def run(self, request: EvaluationRunRequest) -> EvaluationRun:
        documents: list[EvaluationDocumentResult] = []
        try:
            data = self.repository.load(request.ground_truth, request.test_set)
            health = self.health.check(request.index_identity, self.sources)
            if not health.usable or not health.inspection_complete:
                raise ValueError("target index health gate rejected formal evaluation")
            manifest = self.manifest_store.load(request.index_identity)
            records = self.vector_store.list_records(request.index_identity)
            self.preflight.validate(request, data, manifest, records)
        except Exception as exc:
            code = getattr(exc, "code", None) or "index_identity_mismatch"
            return self.run_store.record_failure(request,
                self._error("preflight", code, exc), ())
        for ground, mapping in zip(data.ground_truth_documents,
                                   data.test_set_documents):
            cases = []
            for case, case_mapping in zip(ground.cases, mapping.cases):
                try:
                    result = self.retriever.retrieve(RetrievalRequest(
                        case.question.strip(), request.query_config.top_k, None,
                        request.index_identity, request.configuration_name))
                except Exception as exc:
                    return self.run_store.record_failure(request,
                        self._error("retrieval", "retrieval_failed", exc,
                                    ground.document_id, case.case_id), tuple(documents))
                try:
                    cases.append(self.calculator.score_case(ground, case,
                                                            case_mapping, result))
                except Exception as exc:
                    return self.run_store.record_failure(request,
                        self._error("metric_calculation", "metric_failed", exc,
                                    ground.document_id, case.case_id), tuple(documents))
            documents.append(EvaluationDocumentResult("evaluation_document_result_v3",
                request.run_id, ground.document_key, ground.document_id,
                ground.document_name, ground.relative_path, ground.file_hash,
                tuple(cases)))
        materialized = tuple(documents)
        try:
            aggregate = self.calculator.aggregate(request.run_id, materialized)
        except Exception as exc:
            return self.run_store.record_failure(request,
                self._error("aggregation", "aggregate_failed", exc), materialized)
        try:
            history = self.run_store.list_comparison_runs()
            baselines = self.run_store.load_baselines()
            rendered = self.reporter.render(request, materialized, aggregate,
                                            history, baselines)
        except Exception as exc:
            code = getattr(exc, "code", None) or "json_render_failed"
            stage = "html_rendering" if code == "html_render_failed" else "json_rendering"
            return self.run_store.record_failure(request,
                self._error(stage, code, exc), materialized)
        return self.run_store.publish(request, rendered)
