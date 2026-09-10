"""编排候选表格评分、唯一选优、校验和导出。"""

from __future__ import annotations

from collections import Counter
from itertools import combinations
from pathlib import Path
import time

from experiments.table_extraction.domain.scoring.config import (
    METRIC_NAMES,
    METRIC_WEIGHTS,
    SCORE_EPSILON,
    SCORING_FORMAT_VERSION,
    SELECTION_REASONS,
    TOOL_TIEBREAK_ORDER,
)
from experiments.table_extraction.domain.scoring.metrics import compute_group_raw_metrics
from experiments.table_extraction.domain.models.scoring import GroupScoringResult, ScoringReport
from experiments.table_extraction.application.models import ScoringManifest
from experiments.table_extraction.domain.scoring.ranking import score_group
from experiments.table_extraction.domain.scoring.reference import build_slot_text_references


def validate_scoring_report_invariants(report: ScoringReport) -> None:
    """验证每个 group 的成员、比较数、相对分和 winner 因果闭合。"""
    group_ids: set[str] = set()
    for group in report.groups:
        if group.group_id in group_ids:
            raise ValueError(f"duplicate scored group_id: {group.group_id}")
        group_ids.add(group.group_id)
        candidate_ids = [candidate.candidate_id for candidate in group.candidates]
        if not candidate_ids or len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError(f"scored group members are empty or duplicated: {group.group_id}")
        if group.selected_candidate_id not in candidate_ids:
            raise ValueError(f"winner is not a group member: {group.group_id}")
        expected_tied = [
            candidate.candidate_id for candidate in group.candidates
            if abs(candidate.total_score - group.highest_total_score) <= SCORE_EPSILON
        ]
        if group.tied_top_candidate_ids != expected_tied:
            raise ValueError(f"top-score audit is inconsistent: {group.group_id}")
        actual_highest = max(candidate.total_score for candidate in group.candidates)
        if abs(group.highest_total_score - actual_highest) > SCORE_EPSILON:
            raise ValueError(f"highest total score is inconsistent: {group.group_id}")
        expected_pairs = len(METRIC_NAMES) * len(candidate_ids) * (len(candidate_ids) - 1) // 2
        if len(group.pair_evaluations) != expected_pairs:
            raise ValueError(f"pair evaluation count is inconsistent: {group.group_id}")
        pair_keys = {
            (item.metric_name, item.first_candidate_id, item.second_candidate_id)
            for item in group.pair_evaluations
        }
        expected_pair_keys = {
            (metric_name, first, second)
            for metric_name in METRIC_NAMES
            for first, second in combinations(candidate_ids, 2)
        }
        if pair_keys != expected_pair_keys:
            raise ValueError(f"pair evaluations are duplicated or misconnected: {group.group_id}")
        for candidate in group.candidates:
            if [score.metric_name for score in candidate.relative_scores] != list(METRIC_NAMES):
                raise ValueError(f"relative score metrics are incomplete: {candidate.candidate_id}")
            for score in candidate.relative_scores:
                if score.wins + score.ties + score.losses != len(candidate_ids) - 1:
                    raise ValueError(f"relative score record is inconsistent: {candidate.candidate_id}")
            expected_total = sum(
                score.score * METRIC_WEIGHTS[score.metric_name] for score in candidate.relative_scores
            )
            if abs(candidate.total_score - expected_total) > SCORE_EPSILON:
                raise ValueError(f"candidate total score is inconsistent: {candidate.candidate_id}")

        candidate_by_id = {candidate.candidate_id: candidate for candidate in group.candidates}
        if len(expected_tied) == 1:
            expected_winner = expected_tied[0]
            expected_reason = "highest_total_score"
        else:
            best_rank = min(TOOL_TIEBREAK_ORDER.index(candidate_by_id[item].tool) for item in expected_tied)
            preferred = [
                item for item in expected_tied
                if TOOL_TIEBREAK_ORDER.index(candidate_by_id[item].tool) == best_rank
            ]
            expected_winner = preferred[0] if len(preferred) == 1 else min(preferred)
            expected_reason = "tool_priority_tiebreak" if len(preferred) == 1 else "candidate_id_tiebreak"
        if group.selected_candidate_id != expected_winner or group.selection_reason != expected_reason:
            raise ValueError(f"winner decision is inconsistent: {group.group_id}")


def _manifest(
    source_pdf: Path,
    report: ScoringReport,
    elapsed_seconds: float,
    manual_review_dir: Path,
) -> ScoringManifest:
    """从最终评分报告汇总运行计数和选择原因。"""
    observed_counts = Counter(group.selection_reason for group in report.groups)
    reason_counts = {reason: observed_counts[reason] for reason in SELECTION_REASONS}
    return ScoringManifest(
        source_pdf=str(source_pdf.resolve()),
        format_version=report.format_version,
        source_grouping_report=report.source_grouping_report,
        ready_group_count=len(report.groups),
        scored_group_count=len(report.groups),
        scored_candidate_count=sum(len(group.candidates) for group in report.groups),
        selected_by_reason_counts=reason_counts,
        warnings=report.warnings,
        elapsed_seconds=round(elapsed_seconds, 3),
        scoring_file="scoring.json",
        manual_review_dir=str(manual_review_dir.resolve()),
    )


def run_scoring(source_pdf: Path, tools_output_root: Path, output_dir: Path, *, services) -> Path:
    """读取既有分组，为每个 ready group 评分并导出唯一 winner。"""
    started = time.perf_counter()
    loaded = services.load_scoring_input(source_pdf, tools_output_root)
    ordered_slots = [loaded.slots_by_id[group.slot_id] for group in loaded.groups]
    references, warnings = build_slot_text_references(services.read_page_words(source_pdf, [slot.page_number for slot in ordered_slots]), ordered_slots)

    scored_groups: list[GroupScoringResult] = []
    for group in loaded.groups:
        candidates = [loaded.candidates_by_id[identifier] for identifier in group.member_candidate_ids]
        reference = references[group.slot_id]
        raw_metrics = compute_group_raw_metrics(reference, candidates)
        scored_groups.append(score_group(
            group.group_id, group.slot_id, reference, candidates, raw_metrics,
        ))

    report = ScoringReport(
        format_version=SCORING_FORMAT_VERSION,
        groups=scored_groups,
        source_pdf=str(source_pdf.resolve()),
        source_grouping_report=loaded.grouping_report_path,
        warnings=warnings,
    )
    validate_scoring_report_invariants(report)
    manual_review_dir = output_dir.parent / "score_views_for_manual_review" / source_pdf.stem
    manifest = _manifest(
        source_pdf, report, time.perf_counter() - started, manual_review_dir,
    )
    return services.export_scoring(
        output_dir, manual_review_dir, source_pdf, tools_output_root,
        loaded.candidates_by_id, report, manifest,
    )
