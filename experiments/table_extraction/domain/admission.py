"""计算 candidate-slot 评价并推导候选级准入决定。"""

from __future__ import annotations

from collections import defaultdict

from experiments.table_extraction.domain.admission_config import MIN_CANDIDATE_COVERAGE, MIN_SLOT_COVERAGE, RATIO_EPSILON
from experiments.table_extraction.domain.geometry import slot_match_metrics
from experiments.table_extraction.domain.models.selection import (
    CandidateAdmissionResult,
    CandidateView,
    SlotMatchEvaluation,
    TableSlot,
)


def evaluate_slot_matches(
    candidates: list[CandidateView],
    slots: list[TableSlot],
    candidate_threshold: float = MIN_CANDIDATE_COVERAGE,
    slot_threshold: float = MIN_SLOT_COVERAGE,
    *, failed_pages: set[int] | None = None,
) -> list[SlotMatchEvaluation]:
    """为全部同页 comparable candidate 与 eligible slot 生成评价。"""
    evaluations: list[SlotMatchEvaluation] = []
    for candidate in candidates:
        if candidate.processing_status != "comparable" or candidate.page_number in (failed_pages or set()):
            continue
        assert candidate.bbox is not None
        for slot in slots:
            if slot.status != "eligible" or slot.page_number != candidate.page_number:
                continue
            assert slot.bbox is not None
            metrics = slot_match_metrics(candidate.bbox, slot.bbox)
            reasons: list[str] = []
            if metrics.candidate_coverage + RATIO_EPSILON < candidate_threshold:
                reasons.append("candidate_coverage_below_minimum")
            if metrics.slot_coverage + RATIO_EPSILON < slot_threshold:
                reasons.append("slot_coverage_below_minimum")
            decision = "accepted" if not reasons else "rejected"
            evaluations.append(SlotMatchEvaluation(
                evaluation_id=f"evaluation_{len(evaluations) + 1:04d}",
                candidate_id=candidate.candidate_id,
                slot_id=slot.slot_id,
                decision=decision,
                reason_codes=["bidirectional_coverage_passed"] if decision == "accepted" else reasons,
                metrics=metrics,
            ))
    return evaluations


def decide_candidate_admissions(
    candidates: list[CandidateView],
    evaluations: list[SlotMatchEvaluation],
    *, failed_pages: set[int] | None = None,
) -> list[CandidateAdmissionResult]:
    """依据每个候选的全部 slot 评价产生唯一准入结论。"""
    by_candidate: dict[str, list[SlotMatchEvaluation]] = defaultdict(list)
    for evaluation in evaluations:
        by_candidate[evaluation.candidate_id].append(evaluation)

    results: list[CandidateAdmissionResult] = []
    for candidate in candidates:
        if candidate.processing_status == "deferred":
            results.append(CandidateAdmissionResult(
                candidate_id=candidate.candidate_id,
                processing_status="deferred",
                deferred_reason=candidate.deferred_reason,
                admission_decision=None,
                matched_slot_id=None,
                evaluated_slot_ids=[],
                accepted_slot_ids=[],
                reason_codes=[],
            ))
            continue

        if candidate.page_number in (failed_pages or set()):
            results.append(CandidateAdmissionResult(candidate.candidate_id, "deferred",
                "slot_source_page_failed", None, None, [], [], []))
            continue
        candidate_evaluations = by_candidate.get(candidate.candidate_id, [])
        evaluated = [item.slot_id for item in candidate_evaluations]
        accepted = [item.slot_id for item in candidate_evaluations if item.decision == "accepted"]
        if not candidate_evaluations:
            decision, matched, reason = "rejected", None, "no_eligible_table_slot_on_page"
        elif not accepted:
            decision, matched, reason = "rejected", None, "no_table_slot_passed_bidirectional_coverage"
        elif len(accepted) == 1:
            decision, matched, reason = "admitted", accepted[0], "matched_single_table_slot"
        else:
            decision, matched, reason = "rejected", None, "multiple_table_slots_passed_bidirectional_coverage"
        results.append(CandidateAdmissionResult(
            candidate_id=candidate.candidate_id,
            processing_status="completed",
            deferred_reason=None,
            admission_decision=decision,
            matched_slot_id=matched,
            evaluated_slot_ids=evaluated,
            accepted_slot_ids=accepted,
            reason_codes=[reason],
        ))
    return results

