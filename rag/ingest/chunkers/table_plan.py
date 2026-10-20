"""Table chunk planning: which lines of a serialized table go into which chunk, and which header
and section title lines each chunk writes again. Rules: docs/rules/chunking.md."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Literal

from rag.ingest.tables.models import (
    SerializedTable, SerializedTableLine, TableSection, TableStructure,
)


@dataclass(frozen=True)
class PlannedChunk:
    text: str
    context_line_ids: tuple[str, ...]      # header and title lines written again before the rows
    line_ids: tuple[str, ...]              # the chunk's own lines, in table order
    oversize: Literal["", "row", "context"] = ""   # a single line that does not fit, and why


def plan_chunks(serialized: SerializedTable, structure: TableStructure,
                fits: Callable[[str], bool]) -> tuple[PlannedChunk, ...]:
    """Pack a table's lines into chunks.

    For a rule structure this is the rule packing: lines in order, the header line repeated
    in chunks after it. For an LLM annotation: every chunk repeats the header rows and the titles of the
    sections its first line lies in; sections are kept whole when they fit a chunk, and a
    section that fits none is packed by its sub-sections and rows. A lead sentence is written
    before each chunk by the caller; `fits` counts it.
    """
    annotation = structure if structure.source == "llm_annotation" else None
    lines = serialized.lines
    position = {line.line_id: i for i, line in enumerate(lines)}
    row_line = {line.source_rows[0]: line for line in lines if line.source_rows and line.kind != "unplaced_text"}

    def row_of(line: SerializedTableLine) -> float:
        return line.source_rows[0] if line.source_rows and line.kind != "unplaced_text" else math.inf

    if annotation is None:
        rule_header = next((line for line in lines if line.kind == "header"), None)
        body = list(lines)
        sections: tuple[TableSection, ...] = ()
        header_lines: list[SerializedTableLine] = []
        header_start = header_end = 0
    else:
        rule_header = None
        header = annotation.header_rows
        header_start, header_end = (header[0], header[-1] + 1) if header else (0, 0)
        header_lines = [line for line in lines if line.kind == "header"]
        body = [line for line in lines if line.kind != "header" and row_of(line) >= header_end]
        sections = annotation.sections

    def context(first: SerializedTableLine) -> list[SerializedTableLine]:
        if annotation is None:
            return [rule_header] if rule_header and position[first.line_id] > position[rule_header.line_id] else []
        r = row_of(first)
        open_ = sorted((s for s in sections if s.first_row < r <= s.last_row), key=lambda s: s.first_row)
        def titles(group): return [row_line[t] for s in group for t in s.title_rows if t < r]
        above = [s for s in open_ if header_end and s.first_row < header_start]
        return titles(above) + header_lines + titles([s for s in open_ if s not in above])

    def render(chunk: list[SerializedTableLine]) -> str:
        parts = [line.text for line in context(chunk[0])]
        parts += [chunk[0].resumed_text()] + [line.text for line in chunk[1:]]
        return "\n".join(parts)

    def units(seq: list[SerializedTableLine], group: list[TableSection]) -> list[tuple]:
        """("line", [line]) or ("section", lines, title lines, child units)."""
        outer = [s for s in group if not any(o is not s and o.first_row <= s.first_row
                                             and s.last_row <= o.last_row for o in group)]
        out, i = [], 0
        while i < len(seq):
            s = next((s for s in outer if s.first_row == row_of(seq[i])), None)
            if s is None:
                out.append(("line", [seq[i]]))
                i += 1
                continue
            j = i
            while j < len(seq) and row_of(seq[j]) <= s.last_row:
                j += 1
            n = sum(1 for line in seq[i:j] if row_of(line) in s.title_rows)
            inner = [o for o in group if o is not s and s.first_row <= o.first_row and o.last_row <= s.last_row]
            out.append(("section", seq[i:j], seq[i:i + n], units(seq[i + n:j], inner)))
            i = j
        return out

    def opening(unit: tuple) -> list[SerializedTableLine]:
        """What must share a chunk with the titles before `unit`: the whole unit when it fits a
        chunk, else its own titles and the opening of its first part."""
        if unit[0] == "line" or fits(render(unit[1])):
            return unit[1]
        return unit[2] + (opening(unit[3][0]) if unit[3] else [])

    chunks: list[PlannedChunk] = []
    current: list[SerializedTableLine] = []

    def close() -> None:
        nonlocal current
        if current:
            chunks.append(PlannedChunk(render(current), tuple(l.line_id for l in context(current[0])),
                                       tuple(l.line_id for l in current)))
        current = []

    def place(unit: tuple) -> None:
        nonlocal current
        unit_lines = unit[1]
        if current and fits(render(current + unit_lines)):
            current = current + unit_lines
            return
        if fits(render(unit_lines)):
            close()
            current = list(unit_lines)
            return
        if unit[0] == "section":
            titles, children = unit[2], unit[3]
            # Titles stay in the current chunk only together with what they introduce first.
            following = opening(children[0]) if children else []
            if current and not fits(render(current + titles + following)):
                close()
            for title in titles:
                place(("line", [title]))
            for child in children:
                place(child)
            return
        close()
        line = unit_lines[0]
        chunks.append(PlannedChunk(render([line]), tuple(l.line_id for l in context(line)),
                                   (line.line_id,), "row" if not fits(line.text) else "context"))

    below = [s for s in sections if s.first_row >= header_end]
    for unit in units(body, below):
        place(unit)
    close()
    return tuple(chunks)


def plan_issues(chunks: tuple[PlannedChunk, ...], serialized: SerializedTable) -> list[str]:
    """Rendering checks that the LLM can act on: repeated rows leaving no room for a row."""
    rows = {line.line_id: line.source_rows for line in serialized.lines}
    return [f"the header rows and section titles repeated before row {rows[c.line_ids[0]][0]} leave "
            "no room for it in a chunk; repeat fewer rows" for c in chunks if c.oversize == "context"]
