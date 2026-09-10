"""编排 Docling Table Slot 驱动的候选准入与分组。"""

from __future__ import annotations

from pathlib import Path
import time

from experiments.table_extraction.domain.admission import decide_candidate_admissions, evaluate_slot_matches
from experiments.table_extraction.domain.admission_config import FORMAT_VERSION
from experiments.table_extraction.domain.grouping import build_slot_groups, order_slots, validate_report_invariants
from experiments.table_extraction.domain.models.selection import GroupingReport


def run_selection(source_pdf: Path, tools_output_root: Path, output_dir: Path, *, services) -> Path:
    """执行 Table Slot 建立、候选准入、分组、校验和导出。"""
    started = time.perf_counter()
    pages = services.page_geometries(str(source_pdf))
    slots, slot_warnings = services.load_table_slots(source_pdf, tools_output_root, pages)
    slots = order_slots(slots)
    candidates, candidate_warnings, input_counts = services.load_candidate_views(
        tools_output_root, source_pdf.stem, pages, source_pdf=source_pdf,
    )
    services.validate_docling_baselines(slots, candidates,
        unavailable_refs=services.unavailable_slot_refs(tools_output_root, source_pdf))
    failed_pages = services.failed_slot_pages(tools_output_root, source_pdf)
    evaluations = evaluate_slot_matches(candidates, slots, failed_pages=failed_pages)
    admissions = decide_candidate_admissions(candidates, evaluations, failed_pages=failed_pages)
    groups = build_slot_groups(slots, candidates, admissions)
    report = GroupingReport(
        format_version=FORMAT_VERSION,
        groups=groups,
        table_slots=slots,
        candidate_views=candidates,
        candidate_admission_results=admissions,
        slot_match_evaluations=evaluations,
        warnings=slot_warnings + candidate_warnings,
    )
    validate_report_invariants(report)
    return services.export_selection(
        output_dir, source_pdf, tools_output_root, report, input_counts,
        time.perf_counter() - started,
    )
