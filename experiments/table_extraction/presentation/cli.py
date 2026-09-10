"""表格提取实验的命令行入口。"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from experiments.table_extraction.presentation.workflow import (
    confirm as _confirm,
    ensure_extractions,
    run_all_extractors,
)


# Dependencies and default paths are assigned by bootstrap at the process boundary.


def parse_pymupdf_args() -> argparse.Namespace:
    """读取命令行参数。"""
    parser = argparse.ArgumentParser(description="Run PyMuPDF table extraction and normalization.")
    parser.add_argument("--pdf", type=Path, required=True, help="待测试的 PDF 路径")
    parser.add_argument("--output-dir", type=Path, help="本次实验输出的父目录")
    return parser.parse_args(sys.argv[2:])


def parse_camelot_args() -> argparse.Namespace:
    """读取 Camelot 实验的命令行参数。"""
    parser = argparse.ArgumentParser(description="Run Camelot table extraction experiments.")
    parser.add_argument("--pdf", type=Path, required=True, help="待测试的 PDF 路径")
    parser.add_argument(
        "--flavors",
        default=",".join(SUPPORTED_FLAVORS),
        help="要运行的 parser，以逗号分隔；默认全部",
    )
    parser.add_argument("--pages", default="all", help="Camelot 页码范围，默认 all")
    parser.add_argument("--output-dir", type=Path, help="本次实验输出的父目录")
    return parser.parse_args(sys.argv[2:])


def parse_unstructured_args() -> argparse.Namespace:
    """读取 Unstructured 版面分析实验的命令行参数。"""
    parser = argparse.ArgumentParser(description="Run an Unstructured PDF layout analysis experiment.")
    parser.add_argument("--pdf", type=Path, required=True, help="待测试的 PDF 路径")
    parser.add_argument(
        "--strategy",
        choices=["hi_res"],
        default="hi_res",
        help="初始实验固定使用 hi_res",
    )
    parser.add_argument("--output-dir", type=Path, help="本次实验输出的父目录")
    return parser.parse_args(sys.argv[2:])


def parse_docling_args() -> argparse.Namespace:
    """读取 Docling 版面分析实验的命令行参数。"""
    parser = argparse.ArgumentParser(description="Run a Docling PDF layout analysis experiment.")
    parser.add_argument("--pdf", type=Path, required=True, help="待测试的 PDF 路径")
    parser.add_argument("--output-dir", type=Path, help="本次实验输出的父目录")
    return parser.parse_args(sys.argv[2:])


def parse_extract_all_args() -> argparse.Namespace:
    """读取四工具统一提取命令的参数。"""
    parser = argparse.ArgumentParser(
        prog="python -m experiments.table_extraction extract-all",
        description="Run all local table extraction tools and their supported strategies."
    )
    parser.add_argument("--pdf", type=Path, required=True, help="待测试的 PDF 路径")
    return parser.parse_args(sys.argv[2:])


def parse_grouping_args() -> argparse.Namespace:
    """读取跨工具同表分组的命令行参数。"""
    parser = argparse.ArgumentParser(
        description="Admit normalized table candidates against Docling Table Slots and group them by slot."
    )
    parser.add_argument("--pdf", type=Path, required=True, help="已完成四工具实验的 PDF 路径")
    parser.add_argument("--output-dir", type=Path, help="分组结果输出的父目录")
    return parser.parse_args(sys.argv[2:])


def parse_scoring_args() -> argparse.Namespace:
    """读取候选表格评分与选优的命令行参数。"""
    parser = argparse.ArgumentParser(
        description="Score admitted table candidates and select one winner for every ready group."
    )
    parser.add_argument("--pdf", type=Path, required=True, help="已经完成准入与分组的 PDF 路径")
    parser.add_argument("--output-dir", type=Path, help="评分结果输出的父目录")
    return parser.parse_args(sys.argv[2:])


def parse_admission_calibration_args() -> argparse.Namespace:
    """读取候选准入阈值校准参数。"""
    parser = argparse.ArgumentParser(description="Calculate candidate-to-slot coverage for manual labels.")
    parser.add_argument("--labels", type=Path, default=DEFAULT_ADMISSION_LABELS, help="人工标注 Markdown")
    parser.add_argument("--chart", type=Path, default=DEFAULT_ADMISSION_CHART, help="交互式图表输出路径")
    return parser.parse_args(sys.argv[2:])


def run_pymupdf() -> None:
    """读取参数并执行 PyMuPDF 多策略提取。"""
    args = parse_pymupdf_args()
    pdf_path = args.pdf.resolve()
    if not pdf_path.is_file():
        raise SystemExit(f"PDF not found: {pdf_path}")
    if _execute_pymupdf(pdf_path, args.output_dir) is False:
        raise SystemExit("Extraction incomplete; successful pages and diagnostics were saved.")


def run_camelot() -> None:
    """读取参数并执行 Camelot 表格提取。"""
    args = parse_camelot_args()
    pdf_path = args.pdf.resolve()
    if not pdf_path.is_file():
        raise SystemExit(f"PDF not found: {pdf_path}")

    flavors = [item.strip() for item in args.flavors.split(",") if item.strip()]
    unknown_flavors = sorted(set(flavors) - set(SUPPORTED_FLAVORS))
    if not flavors or unknown_flavors:
        raise SystemExit(
            f"Unsupported flavor(s): {', '.join(unknown_flavors) or '(none)'}. "
            f"Choose from: {', '.join(SUPPORTED_FLAVORS)}"
        )
    if _execute_camelot(pdf_path, flavors, args.pages, args.output_dir) is False:
        raise SystemExit("Extraction incomplete; successful pages and diagnostics were saved.")


def run_unstructured() -> None:
    """读取参数并执行 Unstructured hi_res 版面分析。"""
    args = parse_unstructured_args()
    pdf_path = args.pdf.resolve()
    if not pdf_path.is_file():
        raise SystemExit(f"PDF not found: {pdf_path}")
    if _execute_unstructured(pdf_path, args.output_dir) is False:
        raise SystemExit("Extraction incomplete; successful pages and diagnostics were saved.")


def run_docling() -> None:
    """读取参数并执行 Docling 版面分析与表格恢复。"""
    args = parse_docling_args()
    pdf_path = args.pdf.resolve()
    if not pdf_path.is_file():
        raise SystemExit(f"PDF not found: {pdf_path}")
    if _execute_docling(pdf_path, args.output_dir) is False:
        raise SystemExit("Extraction incomplete; successful pages and diagnostics were saved.")


def _incomplete_extractions(pdf_path: Path) -> list[str]:
    """列出缺失、失败或来源不一致的工具提取结果。"""
    return incomplete_extractions(TOOLS_OUTPUT_ROOT, pdf_path)


def _run_all_extractors(pdf_path: Path) -> list[str]:
    """顺序执行四种工具，单项失败后继续其余工具并汇总失败项。"""
    extractors = (
        ("pymupdf", _execute_pymupdf),
        ("camelot", _execute_camelot),
        ("unstructured", _execute_unstructured),
        ("docling", _execute_docling),
    )
    return run_all_extractors(pdf_path, extractors)


def _ensure_extractions(pdf_path: Path, *, already_confirmed: bool = False) -> None:
    """交互式补齐提取结果，并保证 grouping 必需的 Docling 可用。"""
    ensure_extractions(
        pdf_path,
        TOOLS_OUTPUT_ROOT,
        _run_all_extractors,
        already_confirmed=already_confirmed,
    )


def run_extract_all() -> None:
    """用一个命令执行四种工具的全部既有策略。"""
    args = parse_extract_all_args()
    pdf_path = args.pdf.resolve()
    if not pdf_path.is_file():
        raise SystemExit(f"PDF not found: {pdf_path}")
    failures = _run_all_extractors(pdf_path)
    remaining = _incomplete_extractions(pdf_path)
    if failures or remaining:
        details = remaining or failures
        raise SystemExit("[extract-all] incomplete:\n  - " + "\n  - ".join(details))
    print("[extract-all] all tools and strategies completed successfully.")


def _execute_grouping(pdf_path: Path, output_root: Path) -> Path:
    """执行候选准入与分组，并返回报告路径。"""
    output_dir = (output_root / pdf_path.stem).resolve()
    groups_path = run_selection(pdf_path, TOOLS_OUTPUT_ROOT, output_dir)
    print(f"[grouping] report saved: {groups_path}")
    return groups_path


def _grouping_issue(pdf_path: Path) -> str | None:
    """检查评分所需的默认分组报告是否存在且属于当前 PDF。"""
    return grouping_issue(DEFAULT_GROUPING_OUTPUT_ROOT, pdf_path)


def run_table_grouping() -> None:
    """以 Docling Table Slot 为锚点完成候选准入与分组。"""
    args = parse_grouping_args()
    pdf_path = args.pdf.resolve()
    if not pdf_path.is_file():
        raise SystemExit(f"PDF not found: {pdf_path}")
    _ensure_extractions(pdf_path)
    root = args.output_dir or DEFAULT_GROUPING_OUTPUT_ROOT
    _execute_grouping(pdf_path, root)


def run_table_scoring() -> None:
    """对 ready groups 执行四项组内评分并选出唯一表格。"""
    args = parse_scoring_args()
    pdf_path = args.pdf.resolve()
    if not pdf_path.is_file():
        raise SystemExit(f"PDF not found: {pdf_path}")
    grouping_problem = _grouping_issue(pdf_path)
    extraction_issues = _incomplete_extractions(pdf_path)
    if grouping_problem is not None or extraction_issues:
        if grouping_problem is not None:
            print(f"未检测到当前 PDF 的可用分组结果：{grouping_problem}")
        if extraction_issues:
            print("以下表格提取结果缺失或不可用：")
            for issue in extraction_issues:
                print(f"  - {issue}")
        if not _confirm("是否立即补齐缺失依赖并继续评分？"):
            raise SystemExit("操作已取消；请先完成表格提取与分组后再执行评分。")
        if extraction_issues:
            _ensure_extractions(pdf_path, already_confirmed=True)
        # 提取结果一旦重建，旧分组可能引用过期候选，因此必须同步重建。
        _execute_grouping(pdf_path, DEFAULT_GROUPING_OUTPUT_ROOT)
    root = args.output_dir or DEFAULT_SCORING_OUTPUT_ROOT
    output_dir = (root / pdf_path.stem).resolve()
    scoring_path = run_scoring(pdf_path, TOOLS_OUTPUT_ROOT, output_dir)
    print(f"[scoring] report saved: {scoring_path}")


def run_admission_calibration() -> None:
    """计算人工标注候选的双向覆盖率并更新校准产物。"""
    args = parse_admission_calibration_args()
    labels_path = args.labels.resolve()
    chart_path = args.chart.resolve()
    if not labels_path.is_file():
        raise SystemExit(f"Labels not found: {labels_path}")
    rows = run_calibration(labels_path, TOOLS_OUTPUT_ROOT, chart_path)
    comparable_count = sum(row.metrics is not None for row in rows)
    print(f"[admission-calibration] updated: {labels_path}")
    print(f"  chart: {chart_path}")
    print(f"  candidates: {len(rows)}, comparable: {comparable_count}")


def main() -> None:
    """根据首个参数分派表格提取、分组、评分或校准实验。"""
    if len(sys.argv) <= 1:
        raise SystemExit(
            "Choose a command: extract-all, pymupdf, camelot, unstructured, docling, grouping, scoring, "
            "or admission-calibration."
        )
    if sys.argv[1] == "extract-all":
        run_extract_all()
    elif sys.argv[1] == "pymupdf":
        run_pymupdf()
    elif sys.argv[1] == "camelot":
        run_camelot()
    elif len(sys.argv) > 1 and sys.argv[1] == "unstructured":
        run_unstructured()
    elif len(sys.argv) > 1 and sys.argv[1] == "docling":
        run_docling()
    elif len(sys.argv) > 1 and sys.argv[1] == "grouping":
        run_table_grouping()
    elif len(sys.argv) > 1 and sys.argv[1] == "scoring":
        run_table_scoring()
    elif len(sys.argv) > 1 and sys.argv[1] == "admission-calibration":
        run_admission_calibration()
    else:
        raise SystemExit(
            "Choose a command: extract-all, pymupdf, camelot, unstructured, docling, grouping, scoring, "
            "or admission-calibration."
        )


if __name__ == "__main__":
    main()
