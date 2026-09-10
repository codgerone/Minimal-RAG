"""把四项原始指标转换为组内两两战绩、相对分和唯一 winner。"""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations

from experiments.table_extraction.domain.scoring.config import (
    METRIC_DIRECTIONS,
    METRIC_NAMES,
    METRIC_WEIGHTS,
    SCORE_EPSILON,
    TOOL_TIEBREAK_ORDER,
)
from experiments.table_extraction.domain.models.scoring import (
    CandidateRawMetrics,
    CandidateScoringResult,
    GroupScoringResult,
    MetricName,
    MetricPairEvaluation,
    RelativeMetricScore,
    SlotTextReference,
)
from experiments.table_extraction.domain.models.tables import TableCandidate


def metric_value(raw_metrics: CandidateRawMetrics, metric_name: MetricName) -> float | None:
    """读取一项参与排序的原始指标值。"""
    if metric_name == "text_f1":
        return raw_metrics.text_coverage.f1
    if metric_name == "critical_token_integrity":
        return raw_metrics.critical_tokens.integrity
    if metric_name == "shape_support":
        return raw_metrics.grid_shape.support
    if metric_name == "blank_anomaly":
        return raw_metrics.blank_grid.blank_anomaly
    raise ValueError(f"unsupported metric: {metric_name}")


def compare_metric_pair(
    metric_name: MetricName,
    first_candidate_id: str,
    second_candidate_id: str,
    first_value: float | None,
    second_value: float | None,
) -> MetricPairEvaluation:
    """按指标方向、可评价性和统一浮点容差比较两个候选。"""
    if first_value is None and second_value is None:
        decision, reason = "tie", "both_unavailable"
    elif second_value is None:
        decision, reason = "first_wins", "only_first_evaluable"
    elif first_value is None:
        decision, reason = "second_wins", "only_second_evaluable"
    else:
        if abs(first_value - second_value) <= SCORE_EPSILON:
            decision, reason = "tie", "equal_value"
        else:
            direction = METRIC_DIRECTIONS[metric_name]
            first_wins = (
                first_value > second_value
                if direction == "higher_is_better"
                else first_value < second_value
            )
            decision = "first_wins" if first_wins else "second_wins"
            reason = "higher_value" if direction == "higher_is_better" else "lower_value"
    return MetricPairEvaluation(
        metric_name, first_candidate_id, second_candidate_id,
        first_value, second_value, decision, reason,
    )


def build_metric_pair_evaluations(
    candidates: list[TableCandidate],
    raw_metrics_by_id: dict[str, CandidateRawMetrics],
) -> list[MetricPairEvaluation]:
    """为每项指标生成组内所有无序 candidate pair 的唯一评价。"""
    evaluations: list[MetricPairEvaluation] = []
    for raw_name in METRIC_NAMES:
        metric_name: MetricName = raw_name  # type: ignore[assignment]
        for first, second in combinations(candidates, 2):
            evaluations.append(compare_metric_pair(
                metric_name,
                first.candidate_id,
                second.candidate_id,
                metric_value(raw_metrics_by_id[first.candidate_id], metric_name),
                metric_value(raw_metrics_by_id[second.candidate_id], metric_name),
            ))
    return evaluations


def aggregate_relative_scores(
    candidates: list[TableCandidate],
    evaluations: list[MetricPairEvaluation],
) -> dict[str, list[RelativeMetricScore]]:
    """按胜 2、平 1、负 0 汇总每个候选的四项组内相对分。"""
    records: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0, 0])
    for evaluation in evaluations:
        first = records[(evaluation.first_candidate_id, evaluation.metric_name)]
        second = records[(evaluation.second_candidate_id, evaluation.metric_name)]
        if evaluation.decision == "first_wins":
            first[0] += 1
            second[2] += 1
        elif evaluation.decision == "second_wins":
            first[2] += 1
            second[0] += 1
        else:
            first[1] += 1
            second[1] += 1

    opponent_count = len(candidates) - 1
    results: dict[str, list[RelativeMetricScore]] = {}
    for candidate in candidates:
        scores: list[RelativeMetricScore] = []
        for raw_name in METRIC_NAMES:
            metric_name: MetricName = raw_name  # type: ignore[assignment]
            wins, ties, losses = records[(candidate.candidate_id, metric_name)]
            score = (
                1.0
                if opponent_count == 0
                else (2 * wins + ties) / (2 * opponent_count)
            )
            scores.append(RelativeMetricScore(
                metric_name, wins, ties, losses, opponent_count, score,
            ))
        results[candidate.candidate_id] = scores
    return results


def _total_score(relative_scores: list[RelativeMetricScore]) -> float:
    """按四项各四分之一权重计算浮点总分。"""
    return sum(
        score.score * METRIC_WEIGHTS[score.metric_name] for score in relative_scores
    )


def _select_winner(
    candidates: list[TableCandidate],
    totals: dict[str, float],
) -> tuple[str, float, list[str], str]:
    """先按总分，再按工具优先级和 candidate_id 得到唯一 winner。"""
    highest = max(totals.values())
    tied = [
        candidate.candidate_id for candidate in candidates
        if abs(totals[candidate.candidate_id] - highest) <= SCORE_EPSILON
    ]
    if len(tied) == 1:
        return tied[0], highest, tied, "highest_total_score"

    candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    best_rank = min(TOOL_TIEBREAK_ORDER.index(candidate_by_id[identifier].tool) for identifier in tied)
    preferred = [
        identifier for identifier in tied
        if TOOL_TIEBREAK_ORDER.index(candidate_by_id[identifier].tool) == best_rank
    ]
    if len(preferred) == 1:
        return preferred[0], highest, tied, "tool_priority_tiebreak"
    return min(preferred), highest, tied, "candidate_id_tiebreak"


def score_group(
    group_id: str,
    slot_id: str,
    reference: SlotTextReference,
    candidates: list[TableCandidate],
    raw_metrics_by_id: dict[str, CandidateRawMetrics],
) -> GroupScoringResult:
    """完成一个 ready group 的两两比较、汇总和 winner 决策。"""
    if not candidates:
        raise ValueError(f"cannot score an empty group: {group_id}")
    pair_evaluations = build_metric_pair_evaluations(candidates, raw_metrics_by_id)
    relative_by_id = aggregate_relative_scores(candidates, pair_evaluations)
    totals = {
        candidate.candidate_id: _total_score(relative_by_id[candidate.candidate_id])
        for candidate in candidates
    }
    selected, highest, tied, reason = _select_winner(candidates, totals)
    results = [
        CandidateScoringResult(
            candidate_id=candidate.candidate_id,
            tool=candidate.tool,
            strategy=candidate.strategy,
            raw_metrics=raw_metrics_by_id[candidate.candidate_id],
            relative_scores=relative_by_id[candidate.candidate_id],
            total_score=totals[candidate.candidate_id],
        )
        for candidate in candidates
    ]
    return GroupScoringResult(
        group_id=group_id,
        slot_id=slot_id,
        text_reference=reference,
        candidates=results,
        pair_evaluations=pair_evaluations,
        highest_total_score=highest,
        tied_top_candidate_ids=tied,
        selected_candidate_id=selected,
        selection_reason=reason,  # type: ignore[arg-type]
    )
