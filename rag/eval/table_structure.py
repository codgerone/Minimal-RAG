"""Correct structure annotations of the evaluation set's tables, and checking an annotation against them.

One file per document under `eval/table-structure/`, named like its ground-truth file. A table is
found by where it sits in the PDF: an answer region and a parsed table's region on the same page
overlap by at least `MIN_IOU` (intersection over union). Row and column numbers refer to the grid
the table was parsed into; the parse result is trusted as delivered.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from rag.ingest.tables.models import ImplicitMerge, TableAnnotation, TableSection
from rag.models import PageSpan

SCHEMA_VERSION = "table_structure_v1"
MIN_IOU = 0.5


@dataclass(frozen=True)
class AnswerRegion:
    page_number: int
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True)
class TableAnswer:
    regions: tuple[AnswerRegion, ...]
    annotation: TableAnnotation
    note: str = ""


def load_answers(folder: Path, document_id: str) -> tuple[TableAnswer, ...] | None:
    """The answers for one document; None when the document has no answer file."""
    for path in sorted(folder.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"{path.name}: unsupported schema")
        if data["document_id"] != document_id:
            continue
        return tuple(TableAnswer(
            tuple(AnswerRegion(r["page"], tuple(r["bbox"])) for r in t["regions"]),
            TableAnnotation(tuple(t["header_rows"]),
                            tuple(TableSection(tuple(s["title_rows"]), s["last_row"]) for s in t["sections"]),
                            tuple(ImplicitMerge(m["column"], m["first_row"], m["last_row"])
                                  for m in t["implicit_merges"])),
            t.get("note", "")) for t in data["tables"])
    return None


def _iou(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    inter = max(w, 0) * max(h, 0)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def find_answer(answers: tuple[TableAnswer, ...], sources: tuple[PageSpan, ...]) -> TableAnswer | None:
    """The answer whose regions overlap the table's; None when none or several do."""
    found = [answer for answer in answers if any(
        region.page_number == span.page_number and span.bbox is not None
        and _iou(region.bbox, (span.bbox.x0, span.bbox.y0, span.bbox.x1, span.bbox.y1)) >= MIN_IOU
        for region in answer.regions for span in sources)]
    return found[0] if len(found) == 1 else None


def _rows(rows: tuple[int, ...]) -> str:
    return f"r{rows[0]}" if len(rows) == 1 else f"r{rows[0]}–r{rows[-1]}"


def _section(s: TableSection) -> str:
    return f"标题 {_rows(s.title_rows)}、管到 r{s.last_row} 的节"


def _merge(m: ImplicitMerge) -> str:
    return f"c{m.column} 列 r{m.first_row} 的值沿用到 r{m.last_row}"


def differences(answer: TableAnnotation, given: TableAnnotation) -> list[str]:
    """What `given` gets wrong against the correct annotation; empty when it is correct."""
    found = []
    if tuple(given.header_rows) != tuple(answer.header_rows):
        right = f"{_rows(answer.header_rows)}" if answer.header_rows else "无表头"
        wrong = f"{_rows(given.header_rows)}" if given.header_rows else "无表头"
        found.append(f"表头行应为 {right}，标成了 {wrong}")
    right_by_title = {s.title_rows: s for s in answer.sections}
    given_by_title = {s.title_rows: s for s in given.sections}
    for title, s in right_by_title.items():
        other = given_by_title.get(title)
        if other is not None and other.last_row != s.last_row:
            found.append(f"节 {_rows(title)} 应管到 r{s.last_row}，标成了 r{other.last_row}")
    paired = {t for t in right_by_title if t in given_by_title}
    for kind, right, wrong, show in (("节", tuple(s for s in answer.sections if s.title_rows not in paired),
                                      tuple(s for s in given.sections if s.title_rows not in paired), _section),
                                     ("隐式合并", answer.implicit_merges, given.implicit_merges, _merge)):
        for item in right:
            if item not in wrong:
                found.append(f"漏标{kind}：{show(item)}")
        for item in wrong:
            if item not in right:
                found.append(f"多标{kind}：{show(item)}")
    return found
