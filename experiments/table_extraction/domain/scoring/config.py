"""集中保存候选表格评分的固定规则。"""


SCORING_FORMAT_VERSION = "table_candidate_scoring_v1"
SUPPORTED_GROUPING_FORMAT_VERSION = "table_candidate_selection_v2"
METRIC_NAMES = (
    "text_f1",
    "critical_token_integrity",
    "shape_support",
    "blank_anomaly",
)
METRIC_WEIGHTS = {name: 0.25 for name in METRIC_NAMES}
SCORE_EPSILON = 1e-12
METRIC_DIRECTIONS = {
    "text_f1": "higher_is_better",
    "critical_token_integrity": "higher_is_better",
    "shape_support": "higher_is_better",
    "blank_anomaly": "lower_is_better",
}
TOOL_TIEBREAK_ORDER = ("pymupdf", "camelot", "docling", "unstructured")
SELECTION_REASONS = (
    "highest_total_score",
    "tool_priority_tiebreak",
    "candidate_id_tiebreak",
)
REFERENCE_SOURCE = "pymupdf_page_words_v1"
