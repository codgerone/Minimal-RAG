from rag.v2.common import BoundingBox
from rag.v2.table_selection import (
    CandidateView, GroupingReport, TableSlot, build_slot_groups,
    decide_candidate_admissions, evaluate_slot_matches,
)


def _slot(slot_id: str, box: BoundingBox, page: int = 1) -> TableSlot:
    return TableSlot(slot_id, f"#/tables/{slot_id}", page, 100, 100, box, "eligible", None, ("raw",))


def _candidate(candidate_id: str, box: BoundingBox, page: int = 1) -> CandidateView:
    return CandidateView(candidate_id, "pymupdf", "lines", page, 100, 100, box, "comparable", None)


def test_bidirectional_coverage_requires_both_thresholds() -> None:
    slots = (_slot("slot_001", BoundingBox(0, 0, 10, 10)),)
    candidates = (
        _candidate("accepted", BoundingBox(0, 0, 10, 10)),
        _candidate("candidate_too_large", BoundingBox(0, 0, 20, 10)),
        _candidate("candidate_too_small", BoundingBox(0, 0, 5, 10)),
    )
    evaluations = evaluate_slot_matches(candidates, slots)

    assert evaluations[0].decision == "accepted"
    assert evaluations[1].reason_codes == ("candidate_coverage_below_minimum",)
    assert evaluations[2].reason_codes == ("slot_coverage_below_minimum",)


def test_admission_distinguishes_zero_one_multiple_and_failed_page() -> None:
    slots = (
        _slot("slot_001", BoundingBox(0, 0, 10, 10)),
        _slot("slot_002", BoundingBox(1, 0, 11, 10)),
        _slot("slot_003", BoundingBox(20, 0, 30, 10), page=2),
    )
    candidates = (
        _candidate("one", BoundingBox(20, 0, 30, 10), page=2),
        _candidate("multiple", BoundingBox(1, 0, 10, 10)),
        _candidate("none", BoundingBox(50, 50, 60, 60)),
        _candidate("page_failed", BoundingBox(0, 0, 10, 10), page=3),
    )
    evaluations = evaluate_slot_matches(candidates, slots)
    admissions = decide_candidate_admissions(candidates, evaluations, frozenset({3}))
    by_id = {item.candidate_id: item for item in admissions}

    assert by_id["one"].admission_decision == "admitted"
    assert by_id["one"].matched_slot_id == "slot_003"
    assert by_id["multiple"].reason_codes == ("multiple_table_slots_passed_bidirectional_coverage",)
    assert by_id["none"].reason_codes == ("no_table_slot_passed_bidirectional_coverage",)
    assert by_id["page_failed"].deferred_reason == "slot_source_page_failed"


def test_every_slot_gets_one_group_and_only_admitted_candidates_are_members() -> None:
    deferred = TableSlot(
        "slot_003", "#/tables/3", None, None, None, None,
        "deferred", "missing_slot_provenance", (),
    )
    slots = (
        _slot("slot_001", BoundingBox(0, 0, 10, 10)),
        _slot("slot_002", BoundingBox(20, 0, 30, 10)),
        deferred,
    )
    candidates = (_candidate("winner", BoundingBox(0, 0, 10, 10)),)
    evaluations = evaluate_slot_matches(candidates, slots)
    admissions = decide_candidate_admissions(candidates, evaluations)
    groups = build_slot_groups(slots, candidates, admissions)
    report = GroupingReport(slots, candidates, evaluations, admissions, groups, ())

    assert [group.group_id for group in report.groups] == ["group_001", "group_002", "group_003"]
    assert report.groups[0].member_candidate_ids == ("winner",)
    assert report.groups[1].unresolved_reason == "no_admitted_candidate"
    assert report.groups[2].unresolved_reason == "missing_slot_provenance"
