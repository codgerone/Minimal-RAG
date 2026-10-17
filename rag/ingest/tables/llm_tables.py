"""LLM table processing: self-contained records, a lead sentence, a retrieval summary.

Three independent calls on the same input (the table grid as Markdown plus nearby context).
The LLM output is a fact recorded as returned; validation is a separate verdict. Values must
be copied from cells, so every record is checked to contain each cell it cites, and every
non-empty cell must be cited by some record.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Protocol

PROMPT_VERSION = "llm_tables_v1"
CallKind = Literal["records", "lead", "summary"]
WORDS_PER_TOKEN = 0.6                # only a hint to the LLM; the tokenizer check decides


class _Cell(Protocol):
    cell_id: str
    text: str | None
    start_row_offset_idx: int | None
    end_row_offset_idx: int | None
    start_col_offset_idx: int | None
    end_col_offset_idx: int | None


class _Table(Protocol):
    row_count: int | None
    column_count: int | None
    cells: tuple[_Cell, ...]
    unplaced_text: str | None


# ---------------------------------------------------------------- grid

@dataclass(frozen=True)
class Grid:
    markdown: str
    owner: dict[tuple[int, int], str]        # every covered position -> cell_id
    cell_text: dict[str, str]                # cell_id -> text (whitespace collapsed)
    digits: frozenset[str]                   # digit runs in the table text


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def render_grid(table: _Table) -> Grid:
    rows, cols = table.row_count or 0, table.column_count or 0
    grid = [[""] * cols for _ in range(rows)]
    owner: dict[tuple[int, int], str] = {}
    cell_text: dict[str, str] = {}
    for cell in table.cells:
        r0, r1 = cell.start_row_offset_idx, cell.end_row_offset_idx
        c0, c1 = cell.start_col_offset_idx, cell.end_col_offset_idx
        if None in (r0, r1, c0, c1):
            continue
        text = _clean(cell.text)
        cell_text[cell.cell_id] = text
        for r in range(r0, r1):
            for c in range(c0, c1):
                owner[(r, c)] = cell.cell_id
                grid[r][c] = (text.replace("|", "\\|") if (r, c) == (r0, c0)
                              else "←" if r == r0 else "↑")
    lines = ["| row | " + " | ".join(f"c{c}" for c in range(cols)) + " |"]
    lines += [f"| r{r} | " + " | ".join(grid[r]) + " |" for r in range(rows)]
    if table.unplaced_text and table.unplaced_text.strip():
        lines.append(f"(Text not placed in the grid: {_clean(table.unplaced_text)})")
    all_text = " ".join(cell_text.values()) + " " + (table.unplaced_text or "")
    return Grid("\n".join(lines), owner, cell_text, frozenset(re.findall(r"\d+", all_text)))


# ---------------------------------------------------------------- prompts

GRID_GUIDE = """How to read the table grid:
- The first line gives column numbers (c0, c1, ...); each following line is one grid row and
  starts with its row number (r0, r1, ...). A cell is referred to as r<row>c<column>, e.g. r4c2.
- A merged cell's text is written in its top-left position. Positions it covers in the same row
  are written "←"; positions it covers in rows below are written "↑". Treat all of them as one cell.
- An empty position is an empty cell. "\\|" is a literal "|" inside a cell.
- No header has been marked; decide from the content which rows are headers."""

RECORDS_SYSTEM = """You convert a table extracted from a PDF into self-contained records for a retrieval index.
Each record will be embedded and searched on its own, so a reader who sees only one record
must understand what it states without seeing the rest of the table.

Rules:
1. A record normally corresponds to one data row. Write into it everything needed to understand
   that row: the column labels (all header levels that apply), the group or section title the row
   belongs to, and the values of merged cells that cover the row.
2. Header rows and group/section title rows are not records of their own; their text goes into
   the records they govern. Decide from the meaning of the text whether a full-width row is a
   title for the rows below it or a total/summary of the rows above it. A total or summary row
   is a record of its own and states what it totals.
3. Copy every cell's text exactly as it appears, including numbers, units, codes, dates and
   their punctuation. Do not compute, convert, round, translate or correct values. You may only
   join words that the extraction split by a stray space.
4. Omit empty cells. Do not add information that is not in the table, except a short generic
   label for a column that has no header when its meaning is evident from the table or context.
5. List in "cells" every cell whose text you used in the record (as r<row>c<column>), including
   header and title cells. Every non-empty cell must be used by at least one record.
6. Write in the document language. Prefer the compact form "label: value; label: value" and keep
   the original wording of labels.
7. Each record must stay under {record_max_tokens} tokens (roughly {record_max_words} words).
   This is a limit, not a target.
8. Group consecutive records that belong together (for example the rows under one group title)
   into one segment. Keep the original row order.

Return only JSON in this form:
{{"segments": [{{"records": [{{"text": "...", "cells": ["r4c0", "r4c1"]}}]}}]}}

{grid_guide}"""

LEAD_SYSTEM = """You write one short lead sentence for a table extracted from a PDF. The sentence will be placed
at the start of every retrieval chunk cut from this table, so it must tell a reader what the
table is about and what it is part of.

Rules:
1. One sentence, in the document language, under {lead_max_tokens} tokens
   (roughly {lead_max_words} words).
2. Say what the table lists and, when the context shows it, which section, clause or purpose of
   the document it belongs to. Use the context to tell this table apart from similar tables.
3. Describe the table in general terms; do not copy values from its cells.
4. Do not invent anything that is not supported by the table or the context.

Return only JSON: {{"lead": "..."}}

{grid_guide}"""

SUMMARY_SYSTEM = """You write a retrieval summary for a table extracted from a PDF. The summary replaces the table
in the search index: a question about anything in the table should be able to find this summary.
After it is found, the full table is shown to the reader, so the summary does not need to carry
every value.

Rules:
1. In the document language, one paragraph, under {summary_max_tokens} tokens
   (roughly {summary_max_words} words). This is a limit, not a target.
2. State what the table is and, when the context shows it, which section, clause or purpose of
   the document it belongs to.
3. State how the table is organized: what a row stands for, what the columns are (with their
   original labels), and any groups, subtotals or totals.
4. Copy exactly the values that identify rows and groups, that is, the values a question would
   use to point at a particular row. If they do not all fit, say what kinds there are and give
   as many as fit, in the original order.
5. Do not list the other values the rows report; say only which kinds of values the table gives.
6. Do not invent anything that is not supported by the table or the context.

Return only JSON: {{"summary": "..."}}

{grid_guide}"""

USER_TEMPLATE = """Document language: {language}

<context>
Section heading: {heading}
Text before the table: {before}
</context>

<table>
{grid}
</table>"""


@dataclass(frozen=True)
class TableContext:
    heading: str | None
    before: str | None


@dataclass(frozen=True)
class Limits:
    """Token limits handed to the LLM, all counted as a passage by multilingual-e5."""
    record_max_tokens: int
    lead_max_tokens: int
    summary_max_tokens: int

    @classmethod
    def from_budget(cls, max_tokens: int, lead_max_tokens: int, prefix_tokens: int) -> "Limits":
        # The lead budget is reserved whether or not the lead is used, so records are identical
        # with the lead switched on or off.
        return cls(max_tokens - lead_max_tokens - prefix_tokens, lead_max_tokens,
                   max_tokens - prefix_tokens)


def build_messages(kind: CallKind, grid: Grid, context: TableContext, language: str,
                   limits: Limits) -> list[dict[str, str]]:
    words = lambda tokens: int(tokens * WORDS_PER_TOKEN)
    template = {"records": RECORDS_SYSTEM, "lead": LEAD_SYSTEM, "summary": SUMMARY_SYSTEM}[kind]
    system = template.format(grid_guide=GRID_GUIDE,
                             record_max_tokens=limits.record_max_tokens,
                             record_max_words=words(limits.record_max_tokens),
                             lead_max_tokens=limits.lead_max_tokens,
                             lead_max_words=words(limits.lead_max_tokens),
                             summary_max_tokens=limits.summary_max_tokens,
                             summary_max_words=words(limits.summary_max_tokens))
    user = USER_TEMPLATE.format(language=language.capitalize(),
                                heading=_clean(context.heading) or "(none)",
                                before=_clean(context.before) or "(none)", grid=grid.markdown)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ---------------------------------------------------------------- validation

@dataclass(frozen=True)
class Record:
    text: str
    cell_ids: tuple[str, ...]


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text).lower())


def _json_object(raw: str) -> Any:
    text = raw.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object")
    return json.loads(text[start:end + 1])


def check_records(raw: str, grid: Grid, context: TableContext, count: Callable[[str], int],
                  limit: int) -> tuple[tuple[tuple[Record, ...], ...], list[str]]:
    """Segments of records and the list of issues (empty when valid)."""
    known = _known_digits(grid, context)
    try:
        data = _json_object(raw)
        segments_in = data["segments"]
        assert isinstance(segments_in, list)
    except Exception:
        return (), ['output is not JSON of the form {"segments": [{"records": [...]}]}']
    issues: list[str] = []
    segments: list[tuple[Record, ...]] = []
    cited: set[str] = set()
    for s, segment in enumerate(segments_in):
        records: list[Record] = []
        for r, item in enumerate(segment.get("records", []) if isinstance(segment, dict) else []):
            if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                issues.append(f"segment {s} record {r}: missing text")
                continue
            text, ids = item["text"].strip(), []
            for ref in item.get("cells") or []:
                m = re.fullmatch(r"r(\d+)c(\d+)", str(ref).strip().lower())
                cell_id = grid.owner.get((int(m.group(1)), int(m.group(2)))) if m else None
                if cell_id is None:
                    issues.append(f"segment {s} record {r}: {ref} is not a cell of the grid")
                elif grid.cell_text[cell_id] and cell_id not in ids:
                    ids.append(cell_id)
            # A cited cell whose text is absent is not an error by itself (models often cite the
            # header of an empty column); it is dropped, and coverage below still requires every
            # non-empty cell to appear verbatim in some record that cites it.
            ids = [cell_id for cell_id in ids if _norm(grid.cell_text[cell_id]) in _norm(text)]
            invented = sorted(set(re.findall(r"\d+", text)) - known)
            if invented:
                issues.append(f"segment {s} record {r}: numbers not found in the table or context: "
                              f"{', '.join(invented)}")
            tokens = count(text)
            if tokens > limit:
                issues.append(f"segment {s} record {r}: {tokens} tokens, over the limit of {limit}")
            cited.update(ids)
            records.append(Record(text, tuple(ids)))
        if records:
            segments.append(tuple(records))
    missing = [cell_id for cell_id, text in grid.cell_text.items() if text and cell_id not in cited]
    if missing:
        refs = ", ".join(_ref(grid, cell_id) for cell_id in missing)
        issues.append("these non-empty cells do not appear verbatim in any record that cites them: "
                      + refs)
    if not segments:
        issues.append("no records")
    return tuple(segments), issues


def check_text(kind: Literal["lead", "summary"], raw: str, grid: Grid, context: TableContext,
               count: Callable[[str], int], limit: int) -> tuple[str, list[str]]:
    try:
        text = _json_object(raw)[kind]
        assert isinstance(text, str) and text.strip()
    except Exception:
        return "", [f'output is not JSON of the form {{"{kind}": "..."}}']
    text = text.strip()
    issues = []
    known = _known_digits(grid, context)
    invented = sorted(set(re.findall(r"\d+", text)) - known)
    if invented:
        issues.append(f"numbers not found in the table or context: {', '.join(invented)}")
    tokens = count(text)
    if tokens > limit:
        issues.append(f"{tokens} tokens, over the limit of {limit}")
    return text, issues


def _known_digits(grid: Grid, context: TableContext) -> frozenset[str]:
    return grid.digits | set(re.findall(r"\d+", f"{context.heading or ''} {context.before or ''}"))


def _ref(grid: Grid, cell_id: str) -> str:
    r, c = min(pos for pos, owner in grid.owner.items() if owner == cell_id)
    return f"r{r}c{c}"


# ---------------------------------------------------------------- calling

@dataclass(frozen=True)
class Attempt:
    raw: str
    issues: tuple[str, ...]
    prompt_tokens: int | None
    completion_tokens: int | None
    cost_usd: float | None
    cached: bool


@dataclass(frozen=True)
class CallResult:
    kind: CallKind
    model: str
    prompt_version: str
    attempts: tuple[Attempt, ...]
    records: tuple[tuple[Record, ...], ...] = ()
    text: str = ""

    @property
    def valid(self) -> bool:
        return bool(self.attempts) and not self.attempts[-1].issues


class _UsageLLM(Protocol):
    model: str

    def complete_with_usage(self, messages: list[dict[str, str]]) -> Any: ...


@dataclass
class LLMCache:
    folder: Path

    def key(self, model: str, messages: list[dict[str, str]]) -> str:
        blob = json.dumps({"model": model, "messages": messages}, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def get(self, key: str) -> dict | None:
        path = self.folder / f"{key}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def put(self, key: str, value: dict) -> None:
        from rag.jsonio import write_atomic
        write_atomic(self.folder / f"{key}.json", json.dumps(value, ensure_ascii=False, indent=2))


def run_call(kind: CallKind, llm: _UsageLLM, grid: Grid, context: TableContext, language: str,
             limits: Limits, count: Callable[[str], int], cache: LLMCache | None,
             retries: int = 1) -> CallResult:
    """One call, then up to `retries` more with the issues of the previous answer fed back."""
    messages = build_messages(kind, grid, context, language, limits)
    attempts: list[Attempt] = []
    records: tuple[tuple[Record, ...], ...] = ()
    text = ""
    for _ in range(retries + 1):
        key = cache.key(llm.model, messages) if cache else ""
        hit = cache.get(key) if cache else None
        if hit is None:
            done = llm.complete_with_usage(messages)
            hit = {"text": done.text, "prompt_tokens": done.prompt_tokens,
                   "completion_tokens": done.completion_tokens, "cost_usd": done.cost_usd}
            if cache:
                cache.put(key, hit)
            cached = False
        else:
            cached = True
        raw = hit["text"]
        if kind == "records":
            records, issues = check_records(raw, grid, context, count, limits.record_max_tokens)
        else:
            limit = limits.lead_max_tokens if kind == "lead" else limits.summary_max_tokens
            text, issues = check_text(kind, raw, grid, context, count, limit)
        attempts.append(Attempt(raw, tuple(issues), hit["prompt_tokens"], hit["completion_tokens"],
                                hit["cost_usd"], cached))
        if not issues:
            break
        messages = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": "Your answer did not pass validation:\n- "
             + "\n- ".join(issues) + "\nReturn the corrected JSON in full."}]
    return CallResult(kind, llm.model, PROMPT_VERSION, tuple(attempts), records, text)


# ---------------------------------------------------------------- packing

@dataclass(frozen=True)
class PackedChunk:
    text: str
    records: tuple[Record, ...]
    tokens: int


def pack_records(segments: tuple[tuple[Record, ...], ...], count: Callable[[str], int],
                 max_tokens: int, lead: str | None = None) -> tuple[PackedChunk, ...]:
    """Whole segments per chunk while they fit; a segment over the budget is cut between records."""
    def render(records: list[Record]) -> str:
        return "\n".join(([lead] if lead else []) + [r.text for r in records])

    chunks: list[list[Record]] = []
    current: list[Record] = []
    for segment in segments:
        if current and count(render(current + list(segment))) <= max_tokens:
            current += segment
            continue
        if current:
            chunks.append(current)
        current = []
        for record in segment:
            if current and count(render(current + [record])) > max_tokens:
                chunks.append(current)
                current = []
            current.append(record)
    if current:
        chunks.append(current)
    return tuple(PackedChunk(render(c), tuple(c), count(render(c))) for c in chunks)
