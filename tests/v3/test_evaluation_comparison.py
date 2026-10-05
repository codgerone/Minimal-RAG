from dataclasses import replace
from pathlib import Path

from rag.v3.adapters.evaluation_history import list_v2_snapshots
from rag.v3.application.evaluation_comparison import comparison_report, pair


ROOT = Path(__file__).resolve().parents[2]


def test_real_completed_v2_history_has_strictly_comparable_metrics():
    snapshots = list_v2_snapshots(ROOT)
    assert len(snapshots) == 2
    report = comparison_report(snapshots, "2026-09-26T00:00:00Z")
    assert len(report.runs) == 2
    comparison, = report.comparisons
    assert comparison.status == "strictly_comparable"
    assert comparison.difference_codes == ()
    assert len(comparison.metric_deltas) == 6
    for metric in comparison.metric_deltas:
        assert metric.absolute_delta == metric.right_value - metric.left_value


def test_dataset_change_precedes_query_change_and_suppresses_deltas():
    original, other = list_v2_snapshots(ROOT)
    changed = replace(other,
        ground_truth=replace(other.ground_truth, dataset_version="3.0.0"),
        query_config=replace(other.query_config, top_k=4))
    result = pair(original, changed)
    assert result.status == "dataset_changed"
    assert result.difference_codes == ("dataset_version", "top_k")
    assert result.metric_deltas == ()


def test_protocol_change_suppresses_deltas():
    original, other = list_v2_snapshots(ROOT)
    result = pair(original, replace(other, annotation_rule_version="new"))
    assert result.status == "protocol_changed"
    assert result.difference_codes == ("annotation_rule_version",)
    assert result.metric_deltas == ()
