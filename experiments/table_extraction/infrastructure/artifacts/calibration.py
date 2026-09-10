from __future__ import annotations
from pathlib import Path
from typing import Any
import json
import math
from experiments.table_extraction.domain.calibration import Box, CandidateGeometry, SlotLabel, CandidateLabel, CoverageMetrics, CalibrationRow, coverage_metrics, calculate_rows
TOOLS = ('pymupdf','camelot','unstructured','docling')
def _cells(line: str) -> list[str]:
    """拆分 Markdown 表格行并去除单元格两侧空白。"""
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def parse_labels(markdown: str) -> tuple[list[SlotLabel], list[CandidateLabel]]:
    """从人工标注 Markdown 读取卡位映射和候选标签。"""
    slots: list[SlotLabel] = []
    candidates: list[CandidateLabel] = []
    for line in markdown.splitlines():
        cells = _cells(line) if line.startswith("|") else []
        if len(cells) >= 5 and cells[1].startswith("slot_") and cells[0].endswith(".pdf"):
            slots.append(SlotLabel(cells[0], cells[1], cells[2]))
        elif len(cells) >= 6 and cells[0].isdigit() and cells[1].endswith(".pdf"):
            candidates.append(
                CandidateLabel(
                    sequence=int(cells[0]),
                    pdf_name=cells[1],
                    review_group_id=cells[2],
                    candidate_id=cells[3],
                    admission_label=cells[4],
                    target_slot_id=cells[5],
                )
            )
    return slots, candidates


def _box(value: Any) -> Box:
    """把规范化 JSON bbox 转换为经过校验的矩形。"""
    if not isinstance(value, dict):
        raise ValueError("bbox must be an object")
    box = Box(*(float(value[key]) for key in ("x0", "y0", "x1", "y1")))
    if not all(math.isfinite(number) for number in (box.x0, box.y0, box.x1, box.y1)):
        raise ValueError("bbox coordinates must be finite")
    if box.area <= 0:
        raise ValueError("bbox area must be positive")
    return box


def _geometry(candidate: dict[str, Any]) -> CandidateGeometry:
    """从单页规范化候选读取页面和表级 bbox。"""
    regions = candidate.get("regions")
    if not isinstance(regions, list) or len(regions) != 1 or not isinstance(regions[0], dict):
        raise ValueError(f"candidate {candidate.get('candidate_id')} must have one region")
    region = regions[0]
    return CandidateGeometry(
        candidate_id=str(candidate["candidate_id"]),
        page_number=int(region["page_number"]),
        bbox=_box(region.get("bbox")),
    )


def load_candidates(output_root: Path, pdf_name: str) -> dict[str, CandidateGeometry]:
    """加载一份 PDF 的全部四工具规范化候选。"""
    result: dict[str, CandidateGeometry] = {}
    pdf_stem = Path(pdf_name).stem
    for tool in TOOLS:
        normalized = output_root / tool / pdf_stem / "normalized"
        for path in sorted(normalized.glob("*/tables.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            for candidate in payload.get("tables", []):
                geometry = _geometry(candidate)
                if geometry.candidate_id in result:
                    raise ValueError(f"duplicate candidate in {pdf_name}: {geometry.candidate_id}")
                result[geometry.candidate_id] = geometry
    return result


def update_label_markdown(markdown: str, rows: list[CalibrationRow], chart_name: str) -> str:
    """把比较卡位和两个覆盖率写回人工标注表。"""
    marker = "## 标注表"
    prefix, separator, _ = markdown.partition(marker)
    if not separator:
        raise ValueError("label table marker not found")
    chart_link = f"[打开交互式二维覆盖率图]({chart_name})"
    prefix = prefix.rstrip()
    if chart_link not in prefix:
        prefix += f"\n\n{chart_link}"
    lines = [
        marker,
        "",
        "`admit` 行与人工指定的目标 slot 比较；`reject` 行与同页双向重叠最强的 slot 比较。没有同页 slot 时覆盖率为 `N/A`。",
        "",
        "| 序号 | PDF | 当前审核组 | candidate_id | 准入标签 | 比较/目标 slot_id | candidate_coverage | slot_coverage |",
        "|---:|---|---|---|---|---|---:|---:|",
    ]
    for row in rows:
        candidate_coverage = f"{row.metrics.candidate_coverage:.6f}" if row.metrics else "N/A"
        slot_coverage = f"{row.metrics.slot_coverage:.6f}" if row.metrics else "N/A"
        label = row.label
        lines.append(
            f"| {label.sequence} | {label.pdf_name} | {label.review_group_id} | "
            f"{label.candidate_id} | {label.admission_label} | {row.comparison_slot_id} | "
            f"{candidate_coverage} | {slot_coverage} |"
        )
    return prefix + "\n\n" + "\n".join(lines) + "\n"


def read_labels(path):
    return path.read_text(encoding='utf-8')

def write_text(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding='utf-8')
