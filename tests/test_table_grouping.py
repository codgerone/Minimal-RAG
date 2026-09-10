import json
from dataclasses import replace
from pathlib import Path

import pytest

from experiments.table_extraction.infrastructure.pdf.coordinates import PageGeometry
from experiments.table_extraction.domain.admission import decide_candidate_admissions, evaluate_slot_matches
from experiments.table_extraction.infrastructure.artifacts.candidates import candidate_view, validate_docling_baselines
from experiments.table_extraction.domain.grouping import build_slot_groups, order_slots, validate_report_invariants
from experiments.table_extraction.domain.geometry import slot_match_metrics, validate_bbox
from experiments.table_extraction.domain.models.selection import CandidateView, GroupingReport, TableSlot
from experiments.table_extraction.infrastructure.artifacts.slots import load_table_slots
from experiments.table_extraction.domain.models.tables import BoundingBox


def slot(identifier: str, box: BoundingBox, *, page: int = 1) -> TableSlot:
    """构造准入测试所需的 eligible Table Slot。"""
    return TableSlot(
        identifier, f"#/tables/{int(identifier[-3:]) - 1}", page, 100.0, 100.0,
        box, "eligible", None,
    )


def view(identifier: str, box: BoundingBox | None, *, page: int | None = 1, deferred: str | None = None) -> CandidateView:
    """构造准入测试所需的候选视图。"""
    return CandidateView(
        candidate_id=identifier, tool="pymupdf", strategy="lines", source_ref="", html_file=None,
        page_number=page, page_width=100.0 if page else None, page_height=100.0 if page else None,
        bbox=box, processing_status="deferred" if deferred else "comparable", deferred_reason=deferred,
    )


def test_bbox_validation_only_clips_floating_point_error() -> None:
    """页面边界仅容忍架构规定的微小浮点误差。"""
    clipped = validate_bbox(BoundingBox(-1e-7, 0, 100.0000001, 100), 100, 100)
    assert clipped == BoundingBox(0, 0, 100, 100)
    assert validate_bbox(BoundingBox(-0.01, 0, 100, 100), 100, 100) is None


def test_slot_match_metrics_reports_both_coverage_directions() -> None:
    """面积不等时应分别反映候选覆盖率和卡位覆盖率。"""
    metrics = slot_match_metrics(BoundingBox(0, 0, 20, 10), BoundingBox(0, 0, 10, 10))
    assert metrics.candidate_coverage == 0.5
    assert metrics.slot_coverage == 1.0
    assert metrics.iou == 0.5


def test_evaluation_threshold_uses_only_floating_point_epsilon() -> None:
    """理论等于阈值时应通过，超过 epsilon 的真实差距仍应拒绝。"""
    slots = [slot("slot_001", BoundingBox(0, 0, 80, 80))]
    candidates = [view("candidate_a", BoundingBox(0, 0, 80, 80))]
    exact = evaluate_slot_matches(candidates, slots, candidate_threshold=1.0, slot_threshold=1.0)
    above = evaluate_slot_matches(
        candidates, slots, candidate_threshold=1.0 + 2e-12, slot_threshold=1.0 + 2e-12,
    )
    assert exact[0].decision == "accepted"
    assert above[0].decision == "rejected"
    assert above[0].reason_codes == [
        "candidate_coverage_below_minimum", "slot_coverage_below_minimum",
    ]


def test_same_page_candidate_regions_are_merged() -> None:
    """同页多个来源区域应取最小外接矩形，而不是判为跨页。"""
    candidate = {
        "candidate_id": "candidate_a", "tool": "docling", "strategy": "default", "source_ref": "raw",
        "artifacts": {},
        "regions": [
            {"page_number": 1, "page_width": 100, "page_height": 100,
             "bbox": {"x0": 10, "y0": 10, "x1": 30, "y1": 30},
             "coordinate_transform": {"target_system": "pymupdf_page_top_left_pt_v1"}},
            {"page_number": 1, "page_width": 100, "page_height": 100,
             "bbox": {"x0": 25, "y0": 20, "x1": 60, "y1": 50},
             "coordinate_transform": {"target_system": "pymupdf_page_top_left_pt_v1"}},
        ],
    }
    result = candidate_view(candidate, {1: PageGeometry(1, 100, 100, 0)})
    assert result.processing_status == "comparable"
    assert result.bbox == BoundingBox(10, 10, 60, 50)


def test_candidate_with_unknown_coordinate_system_is_deferred() -> None:
    """没有公共坐标转换证据的候选不得参与覆盖率比较。"""
    candidate = {
        "candidate_id": "candidate_a", "tool": "docling", "strategy": "default", "source_ref": "raw",
        "artifacts": {},
        "regions": [{"page_number": 1, "page_width": 100, "page_height": 100,
                     "bbox": {"x0": 10, "y0": 10, "x1": 30, "y1": 30}}],
    }
    result = candidate_view(candidate, {1: PageGeometry(1, 100, 100, 0)})
    assert result.processing_status == "deferred"
    assert result.deferred_reason == "candidate_coordinate_conversion_failed"


def test_every_slot_requires_one_docling_normalized_baseline() -> None:
    """raw slot 与 Docling normalized candidate 不一致时必须整体失败。"""
    slots = [slot("slot_001", BoundingBox(0, 0, 80, 80))]
    baseline = CandidateView(
        candidate_id="docling_default_p01_t01", tool="docling", strategy="default",
        source_ref="raw/document.json#/tables/0", html_file=None, page_number=1,
        page_width=100, page_height=100, bbox=BoundingBox(0, 0, 80, 80),
        processing_status="comparable", deferred_reason=None,
    )
    validate_docling_baselines(slots, [baseline])
    with pytest.raises(ValueError, match="must map to one"):
        validate_docling_baselines(slots, [])


def test_docling_slots_convert_origins_merge_regions_and_defer_cross_page(tmp_path: Path) -> None:
    """Docling slot 应转换原点、合并同页 provenance 并保留跨页事实。"""
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"pdf")
    docling = tmp_path / "output" / "docling" / "sample"
    (docling / "raw").mkdir(parents=True)
    (docling / "manifest.json").write_text(json.dumps({
        "status": "success", "source_pdf": str(pdf.resolve()), "raw_document_path": "raw/document.json",
    }), encoding="utf-8")
    (docling / "raw" / "document.json").write_text(json.dumps({"tables": [
        {"self_ref": "#/tables/0", "prov": [
            {"page_no": 1, "bbox": {"l": 10, "t": 90, "r": 30, "b": 70, "coord_origin": "BOTTOMLEFT"}},
            {"page_no": 1, "bbox": {"l": 30, "t": 80, "r": 50, "b": 60, "coord_origin": "BOTTOMLEFT"}},
        ]},
        {"self_ref": "#/tables/1", "prov": [
            {"page_no": 1, "bbox": {"l": 10, "t": 10, "r": 20, "b": 20, "coord_origin": "TOPLEFT"}},
            {"page_no": 2, "bbox": {"l": 10, "t": 10, "r": 20, "b": 20, "coord_origin": "TOPLEFT"}},
        ]},
    ]}), encoding="utf-8")
    pages = {1: PageGeometry(1, 100, 100, 0), 2: PageGeometry(2, 100, 100, 0)}

    slots, warnings = load_table_slots(pdf, tmp_path / "output", pages)

    assert len(warnings) == 1 and 'legacy extraction' in warnings[0]
    assert slots[0].bbox == BoundingBox(10, 10, 50, 40)
    assert slots[0].status == "eligible"
    assert slots[1].status == "deferred"
    assert slots[1].deferred_reason == "cross_page_slot"


def test_admission_covers_deferred_none_zero_and_multiple_matches() -> None:
    """候选级结果应区分不可处理、无卡位、零匹配和多匹配。"""
    slots = [slot("slot_001", BoundingBox(0, 0, 80, 80)), slot("slot_002", BoundingBox(0, 0, 80, 80))]
    candidates = [
        view("deferred", None, page=None, deferred="missing_candidate_bbox"),
        view("no_page_slot", BoundingBox(0, 0, 80, 80), page=2),
        view("rejected", BoundingBox(90, 90, 100, 100)),
        view("multiple", BoundingBox(0, 0, 80, 80)),
    ]
    evaluations = evaluate_slot_matches(candidates, slots)
    results = {item.candidate_id: item for item in decide_candidate_admissions(candidates, evaluations)}
    assert results["deferred"].processing_status == "deferred"
    assert results["no_page_slot"].reason_codes == ["no_eligible_table_slot_on_page"]
    assert results["rejected"].reason_codes == ["no_table_slot_passed_bidirectional_coverage"]
    assert results["multiple"].reason_codes == ["multiple_table_slots_passed_bidirectional_coverage"]
    assert results["multiple"].admission_decision == "rejected"


def test_single_accepted_slot_builds_ready_group_and_valid_report() -> None:
    """唯一准入候选应进入对应 slot 的 ready group。"""
    slots = order_slots([
        slot("slot_002", BoundingBox(0, 60, 80, 90)),
        slot("slot_001", BoundingBox(0, 0, 80, 40)),
    ])
    candidates = [view("candidate_a", BoundingBox(0, 0, 80, 40))]
    evaluations = evaluate_slot_matches(candidates, slots)
    admissions = decide_candidate_admissions(candidates, evaluations)
    groups = build_slot_groups(slots, candidates, admissions)
    report = GroupingReport("test", groups, slots, candidates, admissions, evaluations, [])

    validate_report_invariants(report)

    assert groups[0].slot_id == "slot_001"
    assert groups[0].status == "ready_for_scoring"
    assert groups[0].member_candidate_ids == ["candidate_a"]
    assert groups[1].status == "unresolved"
    assert groups[1].unresolved_reason == "no_admitted_candidate"


def test_report_rejects_rejected_candidate_membership() -> None:
    """不变量检查必须阻止 rejected candidate 被写入 group。"""
    slots = [slot("slot_001", BoundingBox(0, 0, 80, 80))]
    candidates = [view("candidate_a", BoundingBox(90, 90, 100, 100))]
    evaluations = evaluate_slot_matches(candidates, slots)
    admissions = decide_candidate_admissions(candidates, evaluations)
    groups = build_slot_groups(slots, candidates, admissions)
    groups[0] = replace(
        groups[0], status="ready_for_scoring", unresolved_reason=None,
        member_candidate_ids=["candidate_a"],
    )
    report = GroupingReport("test", groups, slots, candidates, admissions, evaluations, [])
    with pytest.raises(ValueError, match="rejected or deferred"):
        validate_report_invariants(report)
