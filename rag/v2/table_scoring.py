"""Auditable four-metric scoring and deterministic winner selection."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from itertools import combinations
from math import gcd
from typing import Literal, TypeAlias
from pathlib import Path

from rag.build_config import TableStrategy, ToolName
from rag.v2.common import BoundingBox, ProcessingWarning
from rag.v2.table_models import TableCandidate


MetricName: TypeAlias = Literal["text_f1", "critical_token_integrity", "shape_support", "blank_anomaly"]
METRICS: tuple[MetricName, ...] = ("text_f1", "critical_token_integrity", "shape_support", "blank_anomaly")
METRIC_DIRECTIONS = {"text_f1": 1, "critical_token_integrity": 1, "shape_support": 1, "blank_anomaly": -1}
TOOL_ORDER: tuple[ToolName, ...] = ("pymupdf", "camelot", "docling", "unstructured")
EPSILON = 1e-12
_NUMERIC_APOSTROPHE = re.compile(r"(?<=\d)[^\S\r\n]*['\u2018\u2019\u02bc\u0301][^\S\r\n]*(?=\d)")


@dataclass(frozen=True)
class TokenCount:
    token: str
    count: int

    def __post_init__(self) -> None:
        if not self.token or self.count <= 0:
            raise ValueError("TokenCount 必须包含非空 token 和正计数。")


@dataclass(frozen=True)
class ReferenceWord:
    word_id: str
    page_number: int
    bbox: BoundingBox
    raw_text: str
    normalized_token: str
    block_index: int
    line_index: int
    word_index: int
    is_critical: bool


@dataclass(frozen=True)
class SlotTextReference:
    slot_id: str
    page_number: int
    slot_bbox: BoundingBox
    words: tuple[ReferenceWord, ...]
    token_counts: tuple[TokenCount, ...]
    critical_token_counts: tuple[TokenCount, ...]
    source: Literal["pymupdf_page_words_v1"] = "pymupdf_page_words_v1"


@dataclass(frozen=True)
class TextCoverageMetrics:
    status: Literal["evaluated", "not_evaluable"]
    reason_codes: tuple[str, ...]
    reference_token_count: int
    candidate_token_count: int
    matched_token_count: int
    precision: float | None
    recall: float | None
    f1: float | None
    unmatched_reference_tokens: tuple[TokenCount, ...]
    unmatched_candidate_tokens: tuple[TokenCount, ...]


@dataclass(frozen=True)
class CriticalTokenMetrics:
    status: Literal["evaluated", "not_applicable"]
    reason_codes: tuple[str, ...]
    reference_token_count: int
    matched_token_count: int
    integrity: float | None
    unmatched_reference_tokens: tuple[TokenCount, ...]


@dataclass(frozen=True)
class GridShapeMetrics:
    status: Literal["evaluated"]
    reason_codes: tuple[str, ...]
    row_count: int | None
    column_count: int | None
    same_shape_candidate_count: int
    group_candidate_count: int
    support: float


@dataclass(frozen=True)
class BlankGridMetrics:
    status: Literal["evaluated", "not_evaluable"]
    reason_codes: tuple[str, ...]
    logical_position_count: int | None
    nonempty_covered_position_count: int | None
    blank_position_count: int | None
    blank_ratio: float | None
    reference_blank_ratio: float | None
    reference_source: Literal["unique_mode", "minimum"] | None
    blank_anomaly: float | None
    invalid_nonempty_cell_ids: tuple[str, ...]


@dataclass(frozen=True)
class CandidateRawMetrics:
    text_coverage: TextCoverageMetrics
    critical_tokens: CriticalTokenMetrics
    grid_shape: GridShapeMetrics
    blank_grid: BlankGridMetrics
    warnings: tuple[ProcessingWarning, ...]


@dataclass(frozen=True)
class RelativeMetricScore:
    metric_name: MetricName
    wins: int
    ties: int
    losses: int
    opponent_count: int
    score: float

    def __post_init__(self) -> None:
        if self.wins + self.ties + self.losses != self.opponent_count or not 0 <= self.score <= 1:
            raise ValueError("RelativeMetricScore 战绩或分数无效。")


@dataclass(frozen=True)
class MetricPairEvaluation:
    metric_name: MetricName
    first_candidate_id: str
    second_candidate_id: str
    first_value: float | None
    second_value: float | None
    decision: Literal["first_wins", "tie", "second_wins"]
    reason: Literal["higher_value", "lower_value", "equal_value", "both_unavailable", "only_first_evaluable", "only_second_evaluable"]


@dataclass(frozen=True)
class CandidateScoringResult:
    candidate_id: str
    tool: ToolName
    strategy: TableStrategy
    raw_metrics: CandidateRawMetrics
    relative_scores: tuple[RelativeMetricScore, ...]
    total_score: float


@dataclass(frozen=True)
class GroupScoringResult:
    group_id: str
    slot_id: str
    text_reference: SlotTextReference
    candidates: tuple[CandidateScoringResult, ...]
    pair_evaluations: tuple[MetricPairEvaluation, ...]
    highest_total_score: float
    tied_top_candidate_ids: tuple[str, ...]
    selected_candidate_id: str
    selection_reason: Literal["highest_total_score", "tool_priority_tiebreak", "candidate_id_tiebreak"]

    def __post_init__(self) -> None:
        ids = {item.candidate_id for item in self.candidates}
        if not ids or self.selected_candidate_id not in ids or not self.tied_top_candidate_ids:
            raise ValueError("GroupScoringResult winner 状态无效。")


@dataclass(frozen=True)
class ScoringReport:
    source_pdf: str
    groups: tuple[GroupScoringResult, ...]
    warnings: tuple[ProcessingWarning, ...]
    schema_version: Literal["table_candidate_scoring_v1"] = "table_candidate_scoring_v1"


def normalize_token(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return _NUMERIC_APOSTROPHE.sub("'", normalized).casefold().strip()


def build_slot_text_reference(source_pdf: Path, slot_id: str, page_number: int, slot_bbox: BoundingBox) -> SlotTextReference:
    import pymupdf

    with pymupdf.open(source_pdf) as document:
        if page_number <= 0 or page_number > len(document):
            raise ValueError("slot page_number 超出 PDF 页数。")
        raw_words = document[page_number - 1].get_text("words", sort=False)
    words: list[ReferenceWord] = []
    for raw in raw_words:
        if len(raw) < 8:
            continue
        x0, y0, x1, y1, raw_text, block, line, word = raw[:8]
        center_x, center_y = (float(x0) + float(x1)) / 2, (float(y0) + float(y1)) / 2
        if not (slot_bbox.x0 <= center_x <= slot_bbox.x1 and slot_bbox.y0 <= center_y <= slot_bbox.y1):
            continue
        normalized = normalize_token(str(raw_text))
        if not normalized:
            continue
        words.append(ReferenceWord(
            f"{slot_id}_word_{len(words) + 1:06d}", page_number,
            BoundingBox(float(x0), float(y0), float(x1), float(y1)), str(raw_text), normalized,
            int(block), int(line), int(word), any(char.isdecimal() for char in normalized),
        ))
    ordered = tuple(sorted(words, key=lambda item: (
        item.block_index, item.line_index, item.word_index, item.bbox.y0, item.bbox.x0,
    )))
    counts = Counter(item.normalized_token for item in ordered)
    critical = Counter(item.normalized_token for item in ordered if item.is_critical)
    return SlotTextReference(slot_id, page_number, slot_bbox, ordered, _counts(counts), _counts(critical))


def _counts(counter: Counter[str]) -> tuple[TokenCount, ...]:
    return tuple(TokenCount(token, counter[token]) for token in sorted(counter) if counter[token] > 0)


def candidate_tokens(candidate: TableCandidate) -> Counter[str]:
    counter: Counter[str] = Counter()
    for cell in candidate.cells:
        if cell.text is None:
            continue
        for fragment in re.split(r"\s+", unicodedata.normalize("NFKC", cell.text).strip()):
            token = normalize_token(fragment)
            if token:
                counter[token] += 1
    if candidate.unplaced_text:
        for fragment in re.split(r"\s+", unicodedata.normalize("NFKC", candidate.unplaced_text).strip()):
            token = normalize_token(fragment)
            if token:
                counter[token] += 1
    return counter


def text_metrics(reference: Counter[str], candidate: Counter[str]) -> TextCoverageMetrics:
    reference_count, candidate_count = sum(reference.values()), sum(candidate.values())
    if not reference_count:
        return TextCoverageMetrics("not_evaluable", ("empty_reference",), 0, candidate_count, 0, None, None, None, (), _counts(candidate))
    matched = sum(min(count, candidate[token]) for token, count in reference.items())
    precision = matched / candidate_count if candidate_count else 0.0
    recall = matched / reference_count
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    reasons = ("empty_candidate",) if not candidate_count else ()
    return TextCoverageMetrics(
        "evaluated", reasons, reference_count, candidate_count, matched, precision, recall, f1,
        _counts(reference - candidate), _counts(candidate - reference),
    )


def critical_metrics(reference: Counter[str], candidate: Counter[str]) -> CriticalTokenMetrics:
    critical = Counter({token: count for token, count in reference.items() if any(char.isdecimal() for char in token)})
    total = sum(critical.values())
    if not total:
        return CriticalTokenMetrics("not_applicable", ("no_critical_reference_tokens",), 0, 0, None, ())
    matched = sum(min(count, candidate[token]) for token, count in critical.items())
    return CriticalTokenMetrics("evaluated", (), total, matched, matched / total, _counts(critical - candidate))


def shape_metrics(candidates: tuple[TableCandidate, ...]) -> dict[str, GridShapeMetrics]:
    total = len(candidates)
    valid = Counter((item.row_count, item.column_count) for item in candidates if item.row_count and item.column_count)
    result = {}
    for item in candidates:
        if not item.row_count or not item.column_count:
            result[item.candidate_id] = GridShapeMetrics("evaluated", ("invalid_or_missing_dimensions",), item.row_count, item.column_count, 0, total, 0.0)
        else:
            count = valid[(item.row_count, item.column_count)]
            result[item.candidate_id] = GridShapeMetrics("evaluated", (), item.row_count, item.column_count, count, total, count / total)
    return result


def blank_metrics(candidate: TableCandidate) -> BlankGridMetrics:
    if not candidate.row_count or not candidate.column_count:
        return BlankGridMetrics("not_evaluable", ("invalid_or_missing_dimensions",), None, None, None, None, None, None, None, ())
    covered: set[tuple[int, int]] = set()
    for cell in candidate.cells:
        if not isinstance(cell.text, str) or not cell.text.strip():
            continue
        if cell.start_row_offset_idx is None:
            return BlankGridMetrics("not_evaluable", ("invalid_cell_interval",), None, None, None, None, None, None, None, (cell.cell_id,))
        assert cell.end_row_offset_idx is not None and cell.start_col_offset_idx is not None and cell.end_col_offset_idx is not None
        positions = {(r, c) for r in range(cell.start_row_offset_idx, cell.end_row_offset_idx) for c in range(cell.start_col_offset_idx, cell.end_col_offset_idx)}
        if covered & positions:
            return BlankGridMetrics("not_evaluable", ("overlapping_cells",), None, None, None, None, None, None, None, ())
        covered.update(positions)
    logical = candidate.row_count * candidate.column_count
    blanks = logical - len(covered)
    return BlankGridMetrics("evaluated", (), logical, len(covered), blanks, blanks / logical, None, None, None, ())


def apply_blank_reference(metrics: dict[str, BlankGridMetrics]) -> dict[str, BlankGridMetrics]:
    ratios = [(item.blank_position_count, item.logical_position_count) for item in metrics.values() if item.blank_position_count is not None and item.logical_position_count is not None]
    if not ratios:
        return metrics
    reduced = [(a // gcd(a, b), b // gcd(a, b)) for a, b in ratios]
    frequencies = Counter(reduced)
    highest = max(frequencies.values())
    modes = [key for key, count in frequencies.items() if count == highest]
    if highest >= 2 and len(modes) == 1:
        selected, source = modes[0], "unique_mode"
    else:
        selected, source = min(reduced, key=lambda value: value[0] / value[1]), "minimum"
    reference = selected[0] / selected[1]
    return {key: value if value.blank_ratio is None else replace(value, reference_blank_ratio=reference, reference_source=source, blank_anomaly=max(0.0, value.blank_ratio - reference)) for key, value in metrics.items()}


def raw_metrics(reference: SlotTextReference, candidates: tuple[TableCandidate, ...]) -> dict[str, CandidateRawMetrics]:
    reference_counter = Counter({item.token: item.count for item in reference.token_counts})
    shapes = shape_metrics(candidates)
    blanks = apply_blank_reference({item.candidate_id: blank_metrics(item) for item in candidates})
    result = {}
    for item in candidates:
        tokens = candidate_tokens(item)
        result[item.candidate_id] = CandidateRawMetrics(text_metrics(reference_counter, tokens), critical_metrics(reference_counter, tokens), shapes[item.candidate_id], blanks[item.candidate_id], item.warnings)
    return result


def metric_value(raw: CandidateRawMetrics, name: MetricName) -> float | None:
    return {"text_f1": raw.text_coverage.f1, "critical_token_integrity": raw.critical_tokens.integrity, "shape_support": raw.grid_shape.support, "blank_anomaly": raw.blank_grid.blank_anomaly}[name]


def compare_metric(name: MetricName, first_id: str, second_id: str, first: float | None, second: float | None) -> MetricPairEvaluation:
    if first is None and second is None:
        decision, reason = "tie", "both_unavailable"
    elif second is None:
        decision, reason = "first_wins", "only_first_evaluable"
    elif first is None:
        decision, reason = "second_wins", "only_second_evaluable"
    elif abs(first - second) <= EPSILON:
        decision, reason = "tie", "equal_value"
    else:
        first_wins = first > second if METRIC_DIRECTIONS[name] > 0 else first < second
        decision = "first_wins" if first_wins else "second_wins"
        reason = "higher_value" if METRIC_DIRECTIONS[name] > 0 else "lower_value"
    return MetricPairEvaluation(name, first_id, second_id, first, second, decision, reason)


def score_group(group_id: str, slot_id: str, reference: SlotTextReference, candidates: tuple[TableCandidate, ...]) -> GroupScoringResult:
    if not candidates:
        raise ValueError("不能评分空组。")
    ordered = tuple(sorted(candidates, key=lambda item: item.candidate_id))
    raw = raw_metrics(reference, ordered)
    pairs = tuple(compare_metric(name, first.candidate_id, second.candidate_id, metric_value(raw[first.candidate_id], name), metric_value(raw[second.candidate_id], name)) for name in METRICS for first, second in combinations(ordered, 2))
    records: dict[tuple[str, MetricName], list[int]] = defaultdict(lambda: [0, 0, 0])
    for pair in pairs:
        first, second = records[(pair.first_candidate_id, pair.metric_name)], records[(pair.second_candidate_id, pair.metric_name)]
        if pair.decision == "first_wins": first[0] += 1; second[2] += 1
        elif pair.decision == "second_wins": first[2] += 1; second[0] += 1
        else: first[1] += 1; second[1] += 1
    opponent_count = len(ordered) - 1
    results = []
    for item in ordered:
        relative = []
        for name in METRICS:
            wins, ties, losses = records[(item.candidate_id, name)]
            score = 1.0 if opponent_count == 0 else (wins + 0.5 * ties) / opponent_count
            relative.append(RelativeMetricScore(name, wins, ties, losses, opponent_count, score))
        total = sum(entry.score * 0.25 for entry in relative)
        results.append(CandidateScoringResult(item.candidate_id, item.tool, item.strategy, raw[item.candidate_id], tuple(relative), total))
    highest = max(item.total_score for item in results)
    tied = tuple(item.candidate_id for item in results if abs(item.total_score - highest) <= EPSILON)
    by_id = {item.candidate_id: item for item in ordered}
    if len(tied) == 1:
        selected, reason = tied[0], "highest_total_score"
    else:
        rank = min(TOOL_ORDER.index(by_id[item].tool) for item in tied)
        preferred = tuple(item for item in tied if TOOL_ORDER.index(by_id[item].tool) == rank)
        selected, reason = (preferred[0], "tool_priority_tiebreak") if len(preferred) == 1 else (min(preferred), "candidate_id_tiebreak")
    return GroupScoringResult(group_id, slot_id, reference, tuple(results), pairs, highest, tied, selected, reason)
