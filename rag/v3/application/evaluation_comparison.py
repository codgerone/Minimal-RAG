"""Compare completed evaluation facts without reading presentation files."""

from __future__ import annotations

from itertools import combinations

from rag.v3.contracts.evaluation import (
    ComparisonMetricDelta, ComparisonPair, ComparisonRunReference,
    ComparisonRunSnapshot, EvaluationComparisonReport,
)


METRIC_NAMES = (
    "question_hit_rate_at_k", "macro_evidence_group_recall_at_k",
    "complete_coverage_rate_at_k", "macro_chunk_precision_at_k",
    "mean_reciprocal_rank_at_k", "macro_cross_document_contamination_at_k",
)
QUERY_FIELDS = (
    "top_k", "query_prefix", "document_filter", "question_normalization",
    "distance_order", "tie_break_order", "strict_top_k",
)


def reference(run: ComparisonRunSnapshot) -> ComparisonRunReference:
    return ComparisonRunReference(run.system_version, run.run_id,
                                  run.configuration_label,
                                  run.build_config_fingerprint, run.summary_path)


def pair(left: ComparisonRunSnapshot, right: ComparisonRunSnapshot) -> ComparisonPair:
    codes: list[str] = []
    if left.ground_truth.dataset_version != right.ground_truth.dataset_version:
        codes.append("dataset_version")
    if (left.ground_truth.ground_truth_fingerprint
            != right.ground_truth.ground_truth_fingerprint):
        codes.append("ground_truth_fingerprint")
    left_files = tuple((item.document_id, item.file_hash) for item in left.documents)
    right_files = tuple((item.document_id, item.file_hash) for item in right.documents)
    if left_files != right_files:
        codes.append("pdf_hash")
    left_questions = tuple((item.document_id, item.cases) for item in left.documents)
    right_questions = tuple((item.document_id, item.cases) for item in right.documents)
    if left_questions != right_questions:
        codes.append("question_text")
    dataset_changed = bool(codes)
    for field in QUERY_FIELDS:
        if getattr(left.query_config, field) != getattr(right.query_config, field):
            codes.append(field)
    if left.evaluation_protocol_version != right.evaluation_protocol_version:
        codes.append("evaluation_protocol_version")
    if left.annotation_rule_version != right.annotation_rule_version:
        codes.append("annotation_rule_version")
    status = ("dataset_changed" if dataset_changed else
              "protocol_changed" if codes else "strictly_comparable")
    deltas = []
    if status == "strictly_comparable":
        for name in METRIC_NAMES:
            before = getattr(left.overall_metrics, name).value
            after = getattr(right.overall_metrics, name).value
            deltas.append(ComparisonMetricDelta(name, before, after,
                after - before if before is not None and after is not None else None))
    return ComparisonPair(reference(left), reference(right), status,
                          tuple(codes), tuple(deltas))


def comparison_report(runs: tuple[ComparisonRunSnapshot, ...],
                      generated_at: str) -> EvaluationComparisonReport:
    ordered = tuple(sorted(runs, key=lambda item: (item.system_version, item.run_id)))
    if len({(item.system_version, item.run_id) for item in ordered}) != len(ordered):
        raise ValueError("duplicate comparison run")
    return EvaluationComparisonReport("evaluation_comparison_v3", generated_at,
                                      tuple(reference(item) for item in ordered),
                                      tuple(pair(left, right)
                                            for left, right in combinations(ordered, 2)))
