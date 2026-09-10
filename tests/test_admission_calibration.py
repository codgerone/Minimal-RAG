from experiments.table_extraction.presentation.review_views.calibration import render_chart
from experiments.table_extraction.infrastructure.artifacts.calibration import (
    Box,
    CalibrationRow,
    CandidateLabel,
    CoverageMetrics,
    coverage_metrics,
    parse_labels,
    update_label_markdown,
)


def test_coverage_metrics_reports_both_directions() -> None:
    """双向覆盖率应分别使用候选面积和卡位面积作为分母。"""
    metrics = coverage_metrics(Box(0, 0, 20, 10), Box(0, 0, 10, 10))

    assert metrics.candidate_coverage == 0.5
    assert metrics.slot_coverage == 1.0
    assert metrics.iou == 0.5


def test_parse_labels_reads_slot_and_candidate_tables() -> None:
    """人工标注解析应忽略说明表，只读取卡位和候选数据行。"""
    markdown = """
| PDF | slot_id | 对应 Docling candidate_id | 卡位真实性 | 卡位备注 |
|---|---|---|---|---|
| sample.pdf | slot_001 | docling_default_p01_t01 | table | |
| 序号 | PDF | 当前审核组 | candidate_id | 准入标签 | 比较/目标 slot_id |
|---:|---|---|---|---|---|
| 1 | sample.pdf | group_001 | candidate_1 | admit | slot_001 |
"""

    slots, candidates = parse_labels(markdown)

    assert slots[0].slot_id == "slot_001"
    assert candidates[0].candidate_id == "candidate_1"
    assert candidates[0].admission_label == "admit"


def test_markdown_update_is_idempotent_and_keeps_na() -> None:
    """重复更新不应复制图表链接，无同页卡位时应写入 N/A。"""
    source = "说明\n\n## 标注表\n\n旧表\n"
    row = CalibrationRow(
        label=CandidateLabel(1, "sample.pdf", "group_001", "candidate_1", "reject", "none"),
        comparison_slot_id="none",
        metrics=None,
    )

    first = update_label_markdown(source, [row], "chart.html")
    second = update_label_markdown(first, [row], "chart.html")

    assert second.count("[打开交互式二维覆盖率图](chart.html)") == 1
    assert "| N/A | N/A |" in second


def test_chart_contains_all_candidate_states() -> None:
    """图表应同时嵌入可比较候选和无同页卡位候选。"""
    rows = [
        CalibrationRow(
            CandidateLabel(1, "a.pdf", "group_001", "candidate_a", "admit", "slot_001"),
            "slot_001",
            CoverageMetrics(0.9, 0.8, 0.75),
        ),
        CalibrationRow(
            CandidateLabel(2, "b.pdf", "group_002", "candidate_b", "reject", "none"),
            "none",
            None,
        ),
    ]

    html = render_chart(rows)

    assert "candidate_a" in html
    assert "candidate_b" in html
    assert "candidateCoverage\":null" in html
    assert 'id="noSlotList"' in html
    assert "TP：人工标注为 admit" in html
    assert "覆盖率为 N/A 的候选没有同页 Docling Table Slot" in html
