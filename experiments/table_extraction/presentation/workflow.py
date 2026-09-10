"""检查 CLI 上游产物，并编排四工具提取与交互式补齐。"""

from __future__ import annotations

from pathlib import Path
from typing import Callable
from experiments.table_extraction.application.workflow import run_all_extractors


EXTRACTION_TOOLS = ("pymupdf", "camelot", "unstructured", "docling")
ToolExecutor = Callable[[Path], bool]


def confirm(message: str) -> bool:
    """通过 Y/N 循环确认是否自动补做缺失的上游流程。"""
    while True:
        try:
            answer = input(f"{message} [y/N]: ").strip().casefold()
        except EOFError:
            print("未检测到交互式输入，操作已取消。")
            return False
        if answer in {"y", "yes"}:
            return True
        if answer in {"", "n", "no"}:
            return False
        print("请输入 Y 或 N。")


def ensure_extractions(
    pdf_path: Path,
    output_root: Path,
    run_all: Callable[[Path], list[str]],
    *,
    already_confirmed: bool = False,
) -> None:
    """交互式补齐提取结果，并保证 grouping 必需的 Docling 可用。"""
    issues = incomplete_extractions(output_root, pdf_path)
    if not issues:
        return
    print("未检测到当前 PDF 的完整四工具表格提取结果：")
    for issue in issues:
        print(f"  - {issue}")
    if not already_confirmed and not confirm(
        "是否立即运行 extract-all？该 PDF 已有的工具输出将被对应的新结果覆盖。"
    ):
        if extraction_issue(output_root, "docling", pdf_path) is not None:
            raise SystemExit("操作已取消；Docling Table Slot 不可用，无法继续分组。")
        print("未补齐可选工具；将使用当前已有候选继续分组。")
        return

    failures = run_all(pdf_path)
    remaining = incomplete_extractions(output_root, pdf_path)
    docling_issue = extraction_issue(output_root, "docling", pdf_path)
    if docling_issue is not None:
        raise SystemExit(f"Docling 提取未成功，无法建立 Table Slot：{docling_issue}")
    if failures or remaining:
        details = remaining or failures
        print("部分可选工具未成功；将使用已有成功结果继续分组：")
        for detail in details:
            print(f"  - {detail}")


# Readiness callables are injected by bootstrap.
