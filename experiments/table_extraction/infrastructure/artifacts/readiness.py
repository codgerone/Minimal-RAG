"""检查 CLI 上游产物，并编排四工具提取与交互式补齐。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from experiments.table_extraction.infrastructure.extractors.camelot.runner import SUPPORTED_FLAVORS
from experiments.table_extraction.infrastructure.extractors.pymupdf.config import STRATEGIES


EXTRACTION_TOOLS = ("pymupdf", "camelot", "unstructured", "docling")
ToolExecutor = Callable[[Path], bool]


def _same_path(value: object, expected: Path) -> bool:
    """判断 manifest 中的来源路径是否指向当前 PDF。"""
    if not isinstance(value, str) or not value:
        return False
    try:
        actual = Path(value).resolve()
    except (OSError, RuntimeError):
        return False
    return str(actual).casefold() == str(expected.resolve()).casefold()



def _read_manifest(path: Path) -> dict[str, object] | None:
    """读取工具 manifest；缺失或格式损坏时返回空值。"""
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None



def extraction_issue(output_root: Path, tool: str, pdf_path: Path) -> str | None:
    """检查一种工具是否已有当前 PDF 的完整成功结果。"""
    manifest_path = output_root / tool / pdf_path.stem / "manifest.json"
    manifest = _read_manifest(manifest_path)
    if manifest is None:
        return "manifest 缺失或无法读取"
    if manifest.get("tool") != tool:
        return "manifest 工具标识不一致"

    source_key = "source_file" if tool == "pymupdf" else "source_pdf"
    if not _same_path(manifest.get(source_key), pdf_path):
        return "manifest 来源 PDF 不一致"
    if manifest.get('format_version') == 'table_extraction_v2':
        from .execution import validate_source
        try:
            validate_source(manifest, pdf_path)
            from .identity import validate_manifest_files
            from ..extractors.configuration import configuration_identity
            validate_manifest_files(manifest_path.parent, manifest)
            if manifest.get('configuration_identity') != configuration_identity(tool):
                return 'tool version or extraction configuration changed'
        except (OSError, ValueError) as error:
            return str(error)
        runs = manifest.get('executions', [])
        if not isinstance(runs, list) or any(not isinstance(run, dict) for run in runs):
            return 'invalid strategy execution records'
        expected = set(STRATEGIES if tool == 'pymupdf' else SUPPORTED_FLAVORS if tool == 'camelot' else ('default',) if tool == 'docling' else ('hi_res',))
        if {run.get('strategy') for run in runs} != expected or len(runs) != len(expected):
            return 'requested strategies are incomplete'
        from ..pdf.pages import requested_pages
        required_pages = requested_pages(pdf_path)
        for run in runs:
            if run.get('startup_error'):
                return run['startup_error']
            if run.get('requested_page_numbers') != required_pages:
                return f"{run['strategy']}: requested pages do not cover the PDF"
            pages = run.get('page_results', [])
            if (not isinstance(pages, list) or any(not isinstance(p, dict) for p in pages)
                    or {p.get('page_number') for p in pages} != set(required_pages)
                    or len(pages) != len(required_pages)):
                return f"{run['strategy']}: incomplete page execution facts"
            if not any(page.get('status') == 'success' for page in pages):
                return f"{run['strategy']}: no successful pages"
        return None
    if tool == "pymupdf":
        strategies = manifest.get("strategies")
        if not isinstance(strategies, dict) or not set(STRATEGIES).issubset(strategies):
            return "三种策略结果不完整"
    elif tool == "camelot":
        runs = manifest.get("runs")
        if not isinstance(runs, list):
            return "parser 运行记录缺失"
        statuses = {
            item.get("flavor"): item.get("status")
            for item in runs if isinstance(item, dict)
        }
        failed = [name for name in SUPPORTED_FLAVORS if statuses.get(name) != "success"]
        if failed:
            return f"未成功的 parser: {', '.join(failed)}"
    elif manifest.get("status") != "success":
        return f"运行状态为 {manifest.get('status') or 'unknown'}"
    return None



def incomplete_extractions(output_root: Path, pdf_path: Path) -> list[str]:
    """列出缺失、失败或来源不一致的工具提取结果。"""
    issues: list[str] = []
    for tool in EXTRACTION_TOOLS:
        issue = extraction_issue(output_root, tool, pdf_path)
        if issue is not None:
            issues.append(f"{tool}: {issue}")
    return issues



def grouping_issue(grouping_root: Path, pdf_path: Path, *, tools_output_root: Path | None = None) -> str | None:
    """检查评分所需的默认分组报告是否存在且属于当前 PDF。"""
    grouping_dir = grouping_root / pdf_path.stem
    manifest = _read_manifest(grouping_dir / "manifest.json")
    if manifest is None:
        return "manifest.json 缺失或无法读取"
    if not (grouping_dir / "groups.json").is_file():
        return "groups.json 缺失"
    if not _same_path(manifest.get("source_pdf"), pdf_path):
        return "manifest 来源 PDF 不一致"
    from experiments.table_extraction.domain.admission_config import FORMAT_VERSION
    if manifest.get('format_version') != FORMAT_VERSION:
        return 'grouping format requires rebuilding'
    from .identity import digest
    if manifest.get('source_sha256') != digest(pdf_path):
        return 'grouping source PDF content changed'
    if manifest.get('report_sha256') != digest(grouping_dir/'groups.json'):
        return 'grouping report content changed'
    if tools_output_root is not None:
        from .identity import extraction_fingerprints
        if manifest.get('extraction_sha256') != extraction_fingerprints(tools_output_root, pdf_path.stem):
            return 'extraction inputs changed after grouping'
    return None

