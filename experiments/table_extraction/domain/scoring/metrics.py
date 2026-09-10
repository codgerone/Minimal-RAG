"""计算文本、关键值、网格形状和 span-aware 空白异常指标。"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from math import gcd

from experiments.table_extraction.domain.models.scoring import (
    BlankGridMetrics,
    CandidateRawMetrics,
    CandidateToken,
    CriticalTokenMetrics,
    GridShapeMetrics,
    SlotTextReference,
    TextCoverageMetrics,
    TokenCount,
)
from experiments.table_extraction.domain.scoring.reference import token_counts, tokenize_candidate
from experiments.table_extraction.domain.models.tables import TableCandidate, TableCell


def _counter(values: list[TokenCount] | list[CandidateToken]) -> Counter[str]:
    """从审计模型恢复 token 多重集。"""
    if not values:
        return Counter()
    if isinstance(values[0], TokenCount):
        return Counter({item.token: item.count for item in values})
    return Counter(item.normalized_token for item in values)


def _match_count(first: Counter[str], second: Counter[str]) -> int:
    """按多重集交集计算完整 token 匹配数。"""
    return sum(min(count, second[token]) for token, count in first.items())


def compute_text_coverage(
    reference: SlotTextReference,
    candidate_tokens: list[CandidateToken],
    token_reason_codes: list[str] | None = None,
) -> TextCoverageMetrics:
    """计算候选 token 对 Slot 原生 word 的 precision、recall 和 F1。"""
    reference_counter = _counter(reference.token_counts)
    candidate_counter = _counter(candidate_tokens)
    reference_count = sum(reference_counter.values())
    candidate_count = sum(candidate_counter.values())
    reasons = list(token_reason_codes or [])
    if reference_count == 0:
        reasons.append("empty_reference_tokens")
        return TextCoverageMetrics(
            "not_evaluable", reasons, 0, candidate_count, 0, None, None, None,
            [], token_counts(candidate_counter),
        )

    matched = _match_count(reference_counter, candidate_counter)
    precision = matched / candidate_count if candidate_count else 0.0
    recall = matched / reference_count
    f1 = 2 * matched / (reference_count + candidate_count)
    return TextCoverageMetrics(
        "evaluated", reasons, reference_count, candidate_count, matched,
        precision, recall, f1,
        token_counts(reference_counter - candidate_counter),
        token_counts(candidate_counter - reference_counter),
    )


def compute_critical_token_integrity(
    reference: SlotTextReference,
    candidate_tokens: list[CandidateToken],
    token_reason_codes: list[str] | None = None,
) -> CriticalTokenMetrics:
    """计算含数字原生 word 作为完整 token 被候选保留的比例。"""
    critical_counter = _counter(reference.critical_token_counts)
    candidate_counter = _counter(candidate_tokens)
    critical_count = sum(critical_counter.values())
    if critical_count == 0:
        return CriticalTokenMetrics(
            "not_applicable", ["no_critical_reference_tokens"], 0, 0, None, [],
        )
    matched = _match_count(critical_counter, candidate_counter)
    return CriticalTokenMetrics(
        "evaluated", list(token_reason_codes or []), critical_count, matched,
        matched / critical_count,
        token_counts(critical_counter - candidate_counter),
    )


def _positive_int(value: object) -> bool:
    """判断值是否为非 bool 的正整数。"""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def valid_shape(candidate: TableCandidate) -> bool:
    """判断候选是否具有可用的正整数行列数。"""
    return _positive_int(candidate.row_count) and _positive_int(candidate.column_count)


def compute_shape_support(candidates: list[TableCandidate]) -> dict[str, GridShapeMetrics]:
    """以 candidate 为投票单位计算组内网格形状共识度。"""
    total = len(candidates)
    shape_counts = Counter(
        (candidate.row_count, candidate.column_count)
        for candidate in candidates if valid_shape(candidate)
    )
    results: dict[str, GridShapeMetrics] = {}
    for candidate in candidates:
        if not valid_shape(candidate):
            results[candidate.candidate_id] = GridShapeMetrics(
                "evaluated", ["invalid_grid_shape"], candidate.row_count, candidate.column_count,
                0, total, 0.0,
            )
            continue
        shape = (candidate.row_count, candidate.column_count)
        same_shape = shape_counts[shape]
        results[candidate.candidate_id] = GridShapeMetrics(
            "evaluated", [], candidate.row_count, candidate.column_count,
            same_shape, total, same_shape / total,
        )
    return results


def _cell_placement(cell: TableCell, row_count: int, column_count: int) -> set[tuple[int, int]] | None:
    """验证一个 cell 的 offset/span 并展开其逻辑网格覆盖范围。"""
    if cell.span_source == "unavailable":
        return None
    values = (
        cell.start_row_offset_idx, cell.end_row_offset_idx,
        cell.start_col_offset_idx, cell.end_col_offset_idx,
        cell.row_span, cell.col_span,
    )
    if any(not isinstance(value, int) or isinstance(value, bool) for value in values):
        return None
    start_row, end_row, start_col, end_col, row_span, col_span = values
    if not (
        0 <= start_row < end_row <= row_count
        and 0 <= start_col < end_col <= column_count
        and row_span == end_row - start_row > 0
        and col_span == end_col - start_col > 0
    ):
        return None
    return {
        (row_index, column_index)
        for row_index in range(start_row, end_row)
        for column_index in range(start_col, end_col)
    }


def compute_blank_ratio(candidate: TableCandidate) -> tuple[BlankGridMetrics, list[str]]:
    """按非空物理 cell 的 span 并集计算候选逻辑网格空白率。"""
    if not valid_shape(candidate):
        return BlankGridMetrics(
            "not_evaluable", ["invalid_grid_shape"], None, None, None,
            None, None, None, None, [],
        ), []
    row_count = candidate.row_count
    column_count = candidate.column_count
    assert isinstance(row_count, int) and isinstance(column_count, int)

    covered: set[tuple[int, int]] = set()
    invalid_nonempty: list[str] = []
    warnings: list[str] = []
    for cell in candidate.cells:
        is_nonempty = isinstance(cell.text, str) and bool(cell.text.strip())
        placement = _cell_placement(cell, row_count, column_count)
        if placement is None:
            if is_nonempty:
                invalid_nonempty.append(cell.cell_id)
            elif "invalid_empty_cell_placement" not in warnings:
                warnings.append("invalid_empty_cell_placement")
            continue
        if is_nonempty:
            if covered.intersection(placement) and "overlapping_nonempty_cells" not in warnings:
                warnings.append("overlapping_nonempty_cells")
            covered.update(placement)

    if invalid_nonempty:
        return BlankGridMetrics(
            "not_evaluable", ["invalid_nonempty_cell_placement"], None, None, None,
            None, None, None, None, invalid_nonempty,
        ), warnings

    logical_count = row_count * column_count
    nonempty_count = len(covered)
    blank_count = logical_count - nonempty_count
    return BlankGridMetrics(
        "evaluated", [], logical_count, nonempty_count, blank_count,
        blank_count / logical_count,
        None, None, None, [],
    ), warnings


def _reduced_ratio_key(numerator: int, denominator: int) -> tuple[int, int]:
    """用整数约简键表示比例相等性，不把分子分母暴露到评分 JSON。"""
    divisor = gcd(numerator, denominator)
    return numerator // divisor, denominator // divisor


def _minimum_ratio(keys: list[tuple[int, int]]) -> tuple[int, int]:
    """通过交叉相乘从整数比例键中选择最小值。"""
    result = keys[0]
    for candidate in keys[1:]:
        if candidate[0] * result[1] < result[0] * candidate[1]:
            result = candidate
    return result


def apply_blank_reference(
    metrics_by_id: dict[str, BlankGridMetrics],
) -> dict[str, BlankGridMetrics]:
    """选择组内空白率参照，并为可评价候选计算向上偏离量。"""
    ratio_keys = [
        _reduced_ratio_key(metric.blank_position_count, metric.logical_position_count)
        for metric in metrics_by_id.values()
        if metric.blank_ratio is not None
        and metric.blank_position_count is not None
        and metric.logical_position_count is not None
    ]
    if not ratio_keys:
        return metrics_by_id
    frequencies = Counter(ratio_keys)
    highest_count = max(frequencies.values())
    modes = [ratio_key for ratio_key, count in frequencies.items() if count == highest_count]
    if highest_count >= 2 and len(modes) == 1:
        reference_key, source = modes[0], "unique_mode"
    else:
        reference_key, source = _minimum_ratio(ratio_keys), "minimum"
    reference = reference_key[0] / reference_key[1]

    results: dict[str, BlankGridMetrics] = {}
    for identifier, metric in metrics_by_id.items():
        if metric.blank_ratio is None:
            results[identifier] = metric
            continue
        anomaly = max(0.0, metric.blank_ratio - reference)
        results[identifier] = replace(
            metric,
            reference_blank_ratio=reference,
            reference_source=source,
            blank_anomaly=anomaly,
        )
    return results


def compute_group_raw_metrics(
    reference: SlotTextReference,
    candidates: list[TableCandidate],
) -> dict[str, CandidateRawMetrics]:
    """为同组候选计算四项原始指标及其完整审计事实。"""
    shapes = compute_shape_support(candidates)
    blank_metrics: dict[str, BlankGridMetrics] = {}
    blank_warnings: dict[str, list[str]] = {}
    for candidate in candidates:
        blank_metrics[candidate.candidate_id], blank_warnings[candidate.candidate_id] = compute_blank_ratio(candidate)
    blank_metrics = apply_blank_reference(blank_metrics)

    results: dict[str, CandidateRawMetrics] = {}
    for candidate in candidates:
        tokens, token_reasons = tokenize_candidate(candidate)
        warnings = list(dict.fromkeys(
            [str(item) for item in candidate.warnings]
            + token_reasons
            + blank_warnings[candidate.candidate_id]
        ))
        results[candidate.candidate_id] = CandidateRawMetrics(
            tokens=tokens,
            text_coverage=compute_text_coverage(reference, tokens, token_reasons),
            critical_tokens=compute_critical_token_integrity(reference, tokens, token_reasons),
            grid_shape=shapes[candidate.candidate_id],
            blank_grid=blank_metrics[candidate.candidate_id],
            warnings=warnings,
        )
    return results
