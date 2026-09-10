from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "experiments" / "output" / "extracting" / "pymupdf"
DEFAULT_CAMELOT_OUTPUT_ROOT = PROJECT_ROOT / "experiments" / "output" / "extracting" / "camelot"
DEFAULT_UNSTRUCTURED_OUTPUT_ROOT = PROJECT_ROOT / "experiments" / "output" / "extracting" / "unstructured"
DEFAULT_DOCLING_OUTPUT_ROOT = PROJECT_ROOT / "experiments" / "output" / "extracting" / "docling"
DEFAULT_GROUPING_OUTPUT_ROOT = PROJECT_ROOT / "experiments" / "output" / "grouping"
DEFAULT_SCORING_OUTPUT_ROOT = PROJECT_ROOT / "experiments" / "output" / "scoring"
DEFAULT_ADMISSION_LABELS = PROJECT_ROOT / "experiments" / "data" / "calibration" / "admission-labels.md"
DEFAULT_ADMISSION_CHART = PROJECT_ROOT / "experiments" / "output" / "calibration" / "admission-coverage-chart.html"
TOOLS_OUTPUT_ROOT = PROJECT_ROOT / "experiments" / "output" / "extracting"

