from __future__ import annotations
from dataclasses import dataclass
from .models.tables import BoundingBox
from .geometry import slot_match_metrics

@dataclass(frozen=True)
class Box:
    """表示用于覆盖率计算的有效矩形。"""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def area(self) -> float:
        """返回矩形面积。"""
        return (self.x1 - self.x0) * (self.y1 - self.y0)


@dataclass(frozen=True)
class CandidateGeometry:
    """保存一个规范化候选的页面和表级矩形。"""

    candidate_id: str
    page_number: int
    bbox: Box


@dataclass(frozen=True)
class SlotLabel:
    """保存人工标注文件中的 Docling 卡位映射。"""

    pdf_name: str
    slot_id: str
    docling_candidate_id: str


@dataclass(frozen=True)
class CandidateLabel:
    """保存一个候选的人工准入结论和原有审核顺序。"""

    sequence: int
    pdf_name: str
    review_group_id: str
    candidate_id: str
    admission_label: str
    target_slot_id: str


@dataclass(frozen=True)
class CoverageMetrics:
    """保存候选与卡位的双向覆盖率和 IoU。"""

    candidate_coverage: float
    slot_coverage: float
    iou: float


@dataclass(frozen=True)
class CalibrationRow:
    """汇总一个候选的人工标签、比较卡位和覆盖率。"""

    label: CandidateLabel
    comparison_slot_id: str
    metrics: CoverageMetrics | None

def coverage_metrics(candidate: Box, slot: Box) -> CoverageMetrics:
    value = slot_match_metrics(BoundingBox(candidate.x0,candidate.y0,candidate.x1,candidate.y1),
                              BoundingBox(slot.x0,slot.y0,slot.x1,slot.y1))
    return CoverageMetrics(value.candidate_coverage, value.slot_coverage, value.iou)
def _best_slot(
    candidate: CandidateGeometry,
    slots: dict[str, CandidateGeometry],
) -> tuple[str, CoverageMetrics] | None:
    """为 rejected 候选选择双向重叠最强的同页卡位。"""
    comparisons = []
    for slot_id, slot in slots.items():
        if slot.page_number != candidate.page_number:
            continue
        metrics = coverage_metrics(candidate.bbox, slot.bbox)
        comparisons.append((slot_id, metrics))
    if not comparisons:
        return None
    slot_id, metrics = min(
        comparisons,
        key=lambda item: (
            -min(item[1].candidate_coverage, item[1].slot_coverage),
            -item[1].iou,
            -(item[1].candidate_coverage + item[1].slot_coverage),
            item[0],
        ),
    )
    return slot_id, metrics

def calculate_rows(
    labels: list[CandidateLabel],
    slot_labels: list[SlotLabel],
    candidates_by_pdf: dict[str, dict[str, CandidateGeometry]],
) -> list[CalibrationRow]:
    """按人工标签顺序计算每个候选用于校准的覆盖率。"""
    rows: list[CalibrationRow] = []
    labels_by_pdf: dict[str, list[CandidateLabel]] = {}
    for label in labels:
        labels_by_pdf.setdefault(label.pdf_name, []).append(label)
    slots_by_pdf: dict[str, list[SlotLabel]] = {}
    for slot in slot_labels:
        slots_by_pdf.setdefault(slot.pdf_name, []).append(slot)

    for pdf_name, pdf_labels in labels_by_pdf.items():
        candidates = candidates_by_pdf[pdf_name]
        slot_geometries = {
            slot.slot_id: candidates[slot.docling_candidate_id]
            for slot in slots_by_pdf.get(pdf_name, [])
        }
        for label in pdf_labels:
            candidate = candidates.get(label.candidate_id)
            if candidate is None:
                raise ValueError(f"missing candidate in {pdf_name}: {label.candidate_id}")
            if label.admission_label == "admit":
                slot = slot_geometries.get(label.target_slot_id)
                if slot is None:
                    raise ValueError(f"missing target slot for {label.candidate_id}: {label.target_slot_id}")
                if slot.page_number != candidate.page_number:
                    raise ValueError(f"target slot is on another page: {label.candidate_id}")
                rows.append(
                    CalibrationRow(
                        label=label,
                        comparison_slot_id=label.target_slot_id,
                        metrics=coverage_metrics(candidate.bbox, slot.bbox),
                    )
                )
            elif label.admission_label == "reject":
                best = _best_slot(candidate, slot_geometries)
                rows.append(
                    CalibrationRow(
                        label=label,
                        comparison_slot_id=best[0] if best else "none",
                        metrics=best[1] if best else None,
                    )
                )
            else:
                raise ValueError(f"unsupported admission label: {label.admission_label}")
    return sorted(rows, key=lambda row: row.label.sequence)

