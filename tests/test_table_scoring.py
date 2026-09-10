import pytest

from experiments.table_extraction.domain.scoring.metrics import (
    apply_blank_reference,
    compute_blank_ratio,
    compute_critical_token_integrity,
    compute_group_raw_metrics,
    compute_shape_support,
    compute_text_coverage,
)
from experiments.table_extraction.presentation.review_views.scoring import _competition_ranks, _format_float
from experiments.table_extraction.domain.models.scoring import (
    BlankGridMetrics,
    SlotTextReference,
    TokenCount,
)
from experiments.table_extraction.domain.scoring.ranking import (
    aggregate_relative_scores,
    compare_metric_pair,
    score_group,
)
from experiments.table_extraction.domain.scoring.reference import normalize_token, tokenize_candidate
from experiments.table_extraction.domain.models.tables import (
    BoundingBox,
    TableArtifacts,
    TableCandidate,
    TableCell,
)


def cell(
    identifier: str,
    text: str | None,
    start_row: int = 0,
    end_row: int = 1,
    start_col: int = 0,
    end_col: int = 1,
) -> TableCell:
    """构造带半开区间 offset 和一致 span 的测试单元格。"""
    return TableCell(
        cell_id=identifier,
        bbox=None,
        row_span=end_row - start_row,
        col_span=end_col - start_col,
        start_row_offset_idx=start_row,
        end_row_offset_idx=end_row,
        start_col_offset_idx=start_col,
        end_col_offset_idx=end_col,
        text=text,
        roles=[],
        span_source="native",
        source_ref=identifier,
    )


def candidate(
    identifier: str,
    cells: list[TableCell],
    *,
    tool: str = "pymupdf",
    strategy: str = "lines",
    rows: int | None = 1,
    columns: int | None = 1,
) -> TableCandidate:
    """构造评分测试所需的公共表格候选。"""
    return TableCandidate(
        candidate_id=identifier,
        tool=tool,
        strategy=strategy,
        source_ref=identifier,
        regions=[],
        row_count=rows,
        column_count=columns,
        x_boundaries=None,
        y_boundaries=None,
        cells=cells,
        uncovered_grid_positions=[],
        artifacts=TableArtifacts(),
    )


def reference(*tokens: str) -> SlotTextReference:
    """构造仅包含评分所需 token 多重集的 Slot 参照。"""
    counts: dict[str, int] = {}
    for token in tokens:
        normalized = normalize_token(token)
        counts[normalized] = counts.get(normalized, 0) + 1
    critical = {token: count for token, count in counts.items() if any(char.isdecimal() for char in token)}
    return SlotTextReference(
        slot_id="slot_001",
        source="pymupdf_page_words_v1",
        page_number=1,
        slot_bbox=BoundingBox(0, 0, 100, 100),
        words=[],
        token_counts=[TokenCount(token, count) for token, count in sorted(counts.items())],
        critical_token_counts=[TokenCount(token, count) for token, count in sorted(critical.items())],
    )


def evaluated_blank(numerator: int, denominator: int) -> BlankGridMetrics:
    """构造用于空白参照选择测试的可评价空白率。"""
    return BlankGridMetrics(
        "evaluated", [], denominator, denominator - numerator, numerator,
        numerator / denominator, None, None, None, [],
    )


def test_candidate_tokens_are_normalized_but_never_joined() -> None:
    """空白分片和跨 cell 文本不得被拼回原生关键值。"""
    item = candidate("a", [cell("c1", "5 00"), cell("c2", "A-B")])
    tokens, reasons = tokenize_candidate(item)
    assert reasons == []
    assert [token.normalized_token for token in tokens] == ["5", "00", "a-b"]
    assert [token.cell_id for token in tokens] == ["c1", "c1", "c2"]
    assert normalize_token(" １２.５％ ") == "12.5%"

    metric = compute_critical_token_integrity(reference("500"), tokens)
    assert metric.integrity == 0.0
    assert metric.unmatched_reference_tokens == [TokenCount("500", 1)]


def test_numeric_apostrophe_variants_match_without_joining_plain_fragments() -> None:
    """数字撇号字形及其噪声空格等价，但普通数字空白仍表示切碎。"""
    item = candidate("a", [
        cell("c1", "1´ 131,056.00"),
        cell("c2", "5'157,615.36"),
        cell("c3", "6ʼ485,009.14"),
        cell("c4", "5 00"),
    ])
    tokens, reasons = tokenize_candidate(item)

    assert reasons == []
    assert [token.normalized_token for token in tokens] == [
        "1'131,056.00",
        "5'157,615.36",
        "6'485,009.14",
        "5",
        "00",
    ]
    text_metric = compute_text_coverage(
        reference("1´131,056.00", "5’157,615.36", "6’485,009.14"),
        tokens[:3],
    )
    critical_metric = compute_critical_token_integrity(
        reference("1´131,056.00", "5’157,615.36", "6’485,009.14", "500"),
        tokens,
    )
    assert text_metric.f1 == 1.0
    assert critical_metric.integrity == 0.75
    assert critical_metric.unmatched_reference_tokens == [TokenCount("500", 1)]


def test_apostrophes_outside_numeric_grouping_are_not_collapsed() -> None:
    """非数字上下文中的撇号保持原字符，避免扩大等价范围。"""
    assert normalize_token("company’s") == "company’s"
    assert normalize_token("company's") == "company's"


def test_text_coverage_uses_multiset_counts() -> None:
    """重复 token 只能按双方较小频次进入匹配数。"""
    item = candidate("a", [cell("c1", "qty qty extra")])
    tokens, _ = tokenize_candidate(item)
    metric = compute_text_coverage(reference("qty", "qty", "qty"), tokens)
    assert metric.matched_token_count == 2
    assert metric.precision == pytest.approx(2 / 3)
    assert metric.recall == pytest.approx(2 / 3)
    assert metric.unmatched_reference_tokens == [TokenCount("qty", 1)]
    assert metric.unmatched_candidate_tokens == [TokenCount("extra", 1)]


def test_unreadable_cell_text_keeps_candidate_with_zero_text_scores() -> None:
    """任一 cell text 类型无效时保留候选，但整份候选文本按空文本评分。"""
    invalid = cell("bad", "500")
    invalid.text = 500  # type: ignore[assignment]
    tokens, reasons = tokenize_candidate(candidate("a", [invalid]))
    text_metric = compute_text_coverage(reference("500"), tokens, reasons)
    critical_metric = compute_critical_token_integrity(reference("500"), tokens, reasons)
    assert tokens == []
    assert reasons == ["candidate_text_unreadable"]
    assert text_metric.f1 == 0.0
    assert critical_metric.integrity == 0.0
    assert text_metric.reason_codes == ["candidate_text_unreadable"]


def test_shape_support_counts_every_strategy_as_one_candidate() -> None:
    """同工具不同策略应分别投票，无效 shape 不形成共识。"""
    items = [
        candidate("a", [], strategy="lines", rows=3, columns=4),
        candidate("b", [], strategy="text", rows=3, columns=4),
        candidate("c", [], tool="camelot", rows=4, columns=4),
        candidate("d", [], tool="docling", rows=None, columns=4),
    ]
    metrics = compute_shape_support(items)
    assert metrics["a"].support == 0.5
    assert metrics["b"].support == 0.5
    assert metrics["c"].support == 0.25
    assert metrics["d"].support == 0.0
    assert metrics["d"].reason_codes == ["invalid_grid_shape"]


def test_blank_ratio_counts_full_merged_cell_span_as_nonempty() -> None:
    """非空合并 cell 的全部跨度位置都应计为非空覆盖。"""
    item = candidate("a", [cell("merged", "MODEL", 0, 2, 0, 2)], rows=2, columns=2)
    metric, warnings = compute_blank_ratio(item)
    assert warnings == []
    assert metric.nonempty_covered_position_count == 4
    assert metric.blank_ratio == 0.0


def test_blank_ratio_rejects_invalid_nonempty_placement_but_warns_for_empty() -> None:
    """非空 cell 位置异常使指标不可评价，空 cell 异常只产生 warning。"""
    invalid_nonempty = cell("bad", "value")
    invalid_nonempty.row_span = 2
    metric, warnings = compute_blank_ratio(candidate("a", [invalid_nonempty]))
    assert metric.status == "not_evaluable"
    assert metric.reason_codes == ["invalid_nonempty_cell_placement"]
    assert metric.invalid_nonempty_cell_ids == ["bad"]
    assert warnings == []

    invalid_empty = cell("empty", None)
    invalid_empty.row_span = 2
    metric, warnings = compute_blank_ratio(candidate("b", [invalid_empty]))
    assert metric.status == "evaluated"
    assert warnings == ["invalid_empty_cell_placement"]


def test_blank_reference_uses_unique_mode_otherwise_minimum() -> None:
    """唯一重复众数优先；全异或并列众数时回退到最小空白率。"""
    unique = apply_blank_reference({
        "a": evaluated_blank(1, 10),
        "b": evaluated_blank(2, 20),
        "c": evaluated_blank(1, 5),
    })
    assert unique["c"].reference_source == "unique_mode"
    assert unique["c"].blank_anomaly == pytest.approx(0.1)

    fallback = apply_blank_reference({
        "a": evaluated_blank(1, 10),
        "b": evaluated_blank(1, 5),
        "c": evaluated_blank(3, 10),
    })
    assert fallback["c"].reference_source == "minimum"
    assert fallback["c"].reference_blank_ratio == 0.1


def test_pairwise_comparison_uses_epsilon_and_respects_metric_direction() -> None:
    """浮点尾差应打平，真实差距仍按指标方向判出胜负。"""
    tied = compare_metric_pair("text_f1", "a", "b", 0.1 + 0.2, 0.3)
    text = compare_metric_pair("text_f1", "a", "b", 1.0, 0.99)
    blank = compare_metric_pair("blank_anomaly", "a", "b", 1.0, 0.99)
    assert (tied.decision, tied.reason) == ("tie", "equal_value")
    assert (text.decision, text.reason) == ("first_wins", "higher_value")
    assert (blank.decision, blank.reason) == ("second_wins", "lower_value")


def test_singleton_gets_full_relative_scores_and_deterministic_winner() -> None:
    """单候选组四项相对分均为满分，并直接成为唯一 winner。"""
    item = candidate("only", [cell("c1", "500")])
    raw = compute_group_raw_metrics(reference("500"), [item])
    result = score_group("group_001", "slot_001", reference("500"), [item], raw)
    assert result.selected_candidate_id == "only"
    assert result.selection_reason == "highest_total_score"
    assert result.pair_evaluations == []
    assert all(score.score == 1.0 for score in result.candidates[0].relative_scores)


def test_equal_scores_use_tool_then_candidate_id_tiebreak() -> None:
    """完全同分时先选高优先级工具，同工具内再按 ID 升序。"""
    unstructured = candidate("unstructured_hi_res_p01_t01", [cell("u", "500")], tool="unstructured")
    pymupdf_b = candidate("pymupdf_lines_p01_t02", [cell("b", "500")])
    pymupdf_a = candidate("pymupdf_lines_p01_t01", [cell("a", "500")])
    items = [unstructured, pymupdf_b, pymupdf_a]
    raw = compute_group_raw_metrics(reference("500"), items)
    result = score_group("group_001", "slot_001", reference("500"), items, raw)
    assert result.tied_top_candidate_ids == [item.candidate_id for item in items]
    assert result.selected_candidate_id == "pymupdf_lines_p01_t01"
    assert result.selection_reason == "candidate_id_tiebreak"


def test_unavailable_metric_pair_gives_both_candidates_a_tie() -> None:
    """双方都不可评价时仍形成平局，并贡献二分之一相对分。"""
    items = [candidate("a", []), candidate("b", [])]
    evaluation = compare_metric_pair("text_f1", "a", "b", None, None)
    scores = aggregate_relative_scores(items, [
        evaluation,
        compare_metric_pair("critical_token_integrity", "a", "b", None, None),
        compare_metric_pair("shape_support", "a", "b", None, None),
        compare_metric_pair("blank_anomaly", "a", "b", None, None),
    ])
    assert evaluation.reason == "both_unavailable"
    assert all(score.score == 0.5 for score in scores["a"])


def test_review_ranks_share_places_within_epsilon() -> None:
    """审核视图排名应让容差内同分共享名次，并保留竞赛排名空位。"""
    ranks = _competition_ranks({"a": 1.0, "b": 1.0 - 5e-13, "c": 0.8})
    assert ranks == {"a": 1, "b": 1, "c": 3}


def test_review_float_format_is_fixed_to_two_decimals() -> None:
    """审核视图只改变显示精度，不改变评分 JSON 数值。"""
    assert _format_float(0.9333333333333333) == "0.93"
    assert _format_float(1.0) == "1.00"
    assert _format_float(None) == "N/A"
