"""LLM table processing: a structure annotation with a lead sentence, and a retrieval summary.

Two calls on the same input (the table grid as Markdown plus the text before the table). The
annotation call returns which rows are headers, which rows title a section of rows, which blanks
repeat the value above, and a lead sentence; code renders every chunk from the original cells.
The summary call returns one paragraph that replaces the table in the index. Outputs are
constrained by a strict JSON Schema; each is a fact recorded as returned, and validation is a
separate verdict whose issues are fed back once.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Protocol, Sequence

from rag.ingest.tables.models import (
    ImplicitMerge, SerializedTable, SerializedTableLine, TableAnnotation, TableSection,
)

PROMPT_VERSION = "llm_tables_v3"
REASONING_EFFORT = "medium"
LEAD_MAX_TOKENS = 64                  # accepted by code, and reserved in every chunk
LEAD_WORDS, LEAD_CHARS = 24, 48       # told to the model
SUMMARY_WORDS_PER_TOKEN, SUMMARY_CHARS_PER_TOKEN = 0.5, 1.0
CONTEXT_NODES, CONTEXT_NODE_TOKENS = 2, 200
CONTEXT_SKIPPED_KINDS = frozenset({"header", "footer"})
Language = Literal["chinese", "spanish", "english"]


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
    cells: Sequence[_Cell]
    unplaced_text: str | None


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def _digits(text: str) -> set[str]:
    return set(re.findall(r"\d+", text))


# ---------------------------------------------------------------- grid

@dataclass(frozen=True)
class Grid:
    markdown: str
    owner: dict[tuple[int, int], _Cell]      # every covered position -> its cell
    digits: frozenset[str]                   # digit runs in the table text


def render_grid(table: _Table) -> Grid:
    rows, cols = table.row_count or 0, table.column_count or 0
    grid = [[""] * cols for _ in range(rows)]
    owner: dict[tuple[int, int], _Cell] = {}
    texts = []
    for cell in table.cells:
        r0, r1 = cell.start_row_offset_idx, cell.end_row_offset_idx
        c0, c1 = cell.start_col_offset_idx, cell.end_col_offset_idx
        if r0 is None or r1 is None or c0 is None or c1 is None:
            continue
        text = _clean(cell.text)
        texts.append(text)
        for r in range(r0, r1):
            for c in range(c0, c1):
                owner[(r, c)] = cell
                grid[r][c] = (text.replace("|", "\\|") if (r, c) == (r0, c0)
                              else "←" if r == r0 else "↑")
    lines = ["| row | " + " | ".join(f"c{c}" for c in range(cols)) + " |"]
    lines += [f"| r{r} | " + " | ".join(grid[r]) + " |" for r in range(rows)]
    if table.unplaced_text and table.unplaced_text.strip():
        lines.append(f"(Text not placed in the grid: {_clean(table.unplaced_text)})")
    all_text = " ".join(texts) + " " + (table.unplaced_text or "")
    return Grid("\n".join(lines), owner, frozenset(_digits(all_text)))


# ---------------------------------------------------------------- context

@dataclass(frozen=True)
class ContextNode:
    kind: str
    text: str


def table_context(nodes: Sequence[tuple[str, str]], index: int,
                  count: Callable[[str], int]) -> tuple[ContextNode, ...]:
    """The nodes before `nodes[index]` (kind, text), nearest last; page headers and footers skipped,
    stopping at a table. A node over the token limit keeps its end, the part nearest the table."""
    picked: list[ContextNode] = []
    for kind, text in reversed(nodes[:index]):
        if kind == "table" or len(picked) == CONTEXT_NODES:
            break
        if kind in CONTEXT_SKIPPED_KINDS or not _clean(text):
            continue
        picked.append(ContextNode(kind, _tail(_clean(text), count, CONTEXT_NODE_TOKENS)))
    return tuple(reversed(picked))


def _tail(text: str, count: Callable[[str], int], limit: int) -> str:
    if count(text) <= limit:
        return text
    low, high = 0, len(text)                  # smallest start whose tail fits
    while low < high:
        middle = (low + high) // 2
        if count("… " + text[middle:]) <= limit:
            high = middle
        else:
            low = middle + 1
    space = text.find(" ", low)
    start = space + 1 if 0 <= space - low < 20 else low   # do not open inside a word
    return "… " + text[start:]


# ---------------------------------------------------------------- prompts

GRID_GUIDE = """How to read the table grid:
- The first line gives column numbers (c0, c1, ...). Each following line is one grid row and
  starts with its row number (r0, r1, ...).
- A merged cell's text is written in its top-left position. Positions it covers in the same row
  are written "←"; positions it covers in rows below are written "↑". Treat them as one cell.
- An empty position is an empty cell. "\\|" is a literal "|" inside a cell.
- No header has been marked; decide from the content which rows are headers."""

ANNOTATION_SYSTEM = """You analyze the structure of a table extracted from a PDF. The table will be split into
retrieval chunks of consecutive rows, and each chunk will be searched and read on its own.
Code builds every chunk from the original cells; you do not rewrite any text. Your annotation
decides what each chunk repeats, so that a reader who sees only one chunk still understands it.

Annotate:
1. header_rows: the rows that hold column labels. They must be consecutive. They are repeated
   at the top of every chunk. Leave the list empty if the table has no such rows.
2. sections: a section is a group of rows introduced by a title. The title takes one row or
   several consecutive rows. Give the title rows as title_rows and the last row of the group as
   last_row. The title is repeated in every chunk that contains any row of the group. Sections
   may be nested. List every section as its own item, parents and sub-sections alike; do not
   put one inside another. A sub-section's rows, from its first title row to its last_row, lie
   entirely within its parent's rows; code works out the nesting from these ranges. A title
   above the header rows that applies to the whole table is a section that runs to the last
   row.
   Decide by meaning, not by layout: a row spanning the full width may be a title, a total or
   a note. Rows that total or summarize other rows, and notes, are not section titles.
3. implicit_merges: empty cells that, by meaning, repeat the value above them in the same
   column (the table writes a value once and leaves the following rows blank). Give the column,
   first_row (the row that holds the value) and last_row (the last row that repeats it). Mark
   only cells that are empty in the grid, not cells written "↑", and not cells that are empty
   because there is no value.
4. lead: one sentence in the document language, at most {lead_limit}, that tells a reader what
   the table is about: what it lists, what a row stands for and what the columns give. You may
   use the meaning of headings in the context. Do not mention where the table is in the
   document (section, clause, article or table numbers, page numbers). Describe the table in
   general terms; do not copy values from its cells. Do not state anything that the table or
   the context does not support.

Every row not named in header_rows or in any title_rows is a data row and is kept as it is.
Give row and column numbers as integers (r12 is 12, c3 is 3).

{grid_guide}"""

SUMMARY_SYSTEM = """You write a retrieval summary for a table extracted from a PDF. The summary replaces the table
in the search index: a question about anything in the table should be able to find this
summary. After it is found, the full table is shown to the reader, so the summary does not need
to carry every value.

Rules:
1. Write one paragraph in the document language, at most {summary_limit}. This is a limit, not
   a target.
2. State what the table is about. You may use the meaning of headings in the context. Do not
   mention where the table is in the document (section, clause, article or table numbers, page
   numbers).
3. State how the table is organized: what a row stands for, what the columns are (with their
   original labels), and any groups, subtotals or totals.
4. Copy exactly the values that identify rows and groups, that is, the values a question would
   use to point at a particular row. If they do not all fit, say what kinds there are and give
   as many as fit, in the original order.
5. Do not list the other values the rows report; say only which kinds of values the table gives.
6. Do not state anything that the table or the context does not support.

{grid_guide}"""

USER_TEMPLATE = """Document language: {language}

<context>
The text that comes just before the table in the document, nearest last. It may describe the
table, or it may be unrelated to it; judge for yourself. It is given to help you understand the
table. Your answer does not have to draw on it, and where it does, you need not keep its
wording.
{nodes}
</context>

<table>
{grid}
</table>"""

RETRY_TEMPLATE = """Your answer did not pass these checks:
{issues}
Return a corrected answer in the same format."""

_INT = {"type": "integer"}
ANNOTATION_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["header_rows", "sections", "implicit_merges", "lead"],
    "properties": {
        "header_rows": {"type": "array", "items": _INT},
        "sections": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["title_rows", "last_row"],
            "properties": {"title_rows": {"type": "array", "items": _INT}, "last_row": _INT}}},
        "implicit_merges": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["column", "first_row", "last_row"],
            "properties": {"column": _INT, "first_row": _INT, "last_row": _INT}}},
        "lead": {"type": "string"},
    },
}
SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False, "required": ["summary"],
    "properties": {"summary": {"type": "string"}},
}


@dataclass(frozen=True)
class Lengths:
    """Lengths told to the model in words (characters for Chinese); code checks tokens."""
    language: Language
    summary_max_tokens: int           # max_tokens minus the passage prefix

    def size(self, text: str) -> str:
        if self.language == "chinese":
            return f"{len(''.join(text.split()))} Chinese characters"
        return f"{len(text.split())} words"

    @property
    def lead_limit(self) -> str:
        return f"{LEAD_CHARS} Chinese characters" if self.language == "chinese" else f"{LEAD_WORDS} words"

    @property
    def summary_limit(self) -> str:
        if self.language == "chinese":
            return f"{int(self.summary_max_tokens * SUMMARY_CHARS_PER_TOKEN)} Chinese characters"
        return f"{int(self.summary_max_tokens * SUMMARY_WORDS_PER_TOKEN)} words"


def build_messages(kind: Literal["annotation", "summary"], grid: Grid,
                   context: tuple[ContextNode, ...], lengths: Lengths) -> list[dict[str, str]]:
    if kind == "annotation":
        system = ANNOTATION_SYSTEM.format(lead_limit=lengths.lead_limit, grid_guide=GRID_GUIDE)
    else:
        system = SUMMARY_SYSTEM.format(summary_limit=lengths.summary_limit, grid_guide=GRID_GUIDE)
    nodes = "\n".join(f"[{node.kind}] {node.text}" for node in context) or "(none)"
    user = USER_TEMPLATE.format(language=lengths.language.capitalize(), nodes=nodes,
                                grid=grid.markdown)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ---------------------------------------------------------------- validation

def known_digits(grid: Grid, context: tuple[ContextNode, ...]) -> frozenset[str]:
    return grid.digits | frozenset(d for node in context for d in _digits(node.text))


def parse_annotation(raw: str) -> tuple[TableAnnotation, str] | None:
    try:
        data = json.loads(raw)
        annotation = TableAnnotation(
            tuple(int(r) for r in data["header_rows"]),
            tuple(TableSection(tuple(int(r) for r in s["title_rows"]), int(s["last_row"]))
                  for s in data["sections"]),
            tuple(ImplicitMerge(int(m["column"]), int(m["first_row"]), int(m["last_row"]))
                  for m in data["implicit_merges"]))
        lead = data["lead"]
        assert isinstance(lead, str)
    except Exception:
        return None
    return annotation, lead.strip()


def _consecutive(rows: tuple[int, ...]) -> bool:
    return list(rows) == list(range(rows[0], rows[0] + len(rows))) if rows else True


def annotation_issues(annotation: TableAnnotation, table: _Table, grid: Grid) -> list[str]:
    """Structural checks of the annotation against the grid (proposal 2.3)."""
    rows, cols = table.row_count or 0, table.column_count or 0
    span = f"rows 0-{rows - 1}"
    issues: list[str] = []
    header = annotation.header_rows
    header_set = set(header)
    if any(not 0 <= r < rows for r in header):
        issues.append(f"header_rows: {list(header)} has rows outside the table ({span})")
    elif not _consecutive(header):
        issues.append(f"header_rows: {list(header)} are not consecutive rows in ascending order")
    titles: dict[int, int] = {}                          # title row -> section index
    for i, section in enumerate(annotation.sections):
        name = f"sections[{i}]"
        t = section.title_rows
        if not t:
            issues.append(f"{name}: title_rows is empty")
            continue
        if any(not 0 <= r < rows for r in t) or not 0 <= section.last_row < rows:
            issues.append(f"{name}: rows {list(t)} to {section.last_row} are outside the table ({span})")
            continue
        if not _consecutive(t):
            issues.append(f"{name}: title_rows {list(t)} are not consecutive rows in ascending order")
        if header_set & set(t):
            issues.append(f"{name}: title rows {sorted(header_set & set(t))} are also header rows")
        if section.last_row < t[-1]:
            issues.append(f"{name}: last_row {section.last_row} is above its last title row {t[-1]}")
        if header:
            if t[0] > header[-1] or section.last_row < header[0]:
                pass
            elif t[0] < header[0] and section.last_row <= header[-1]:
                issues.append(f"{name}: a title above the header rows must run past them "
                              f"(last_row {section.last_row}, header ends at row {header[-1]})")
        for r in t:
            if r in titles:
                issues.append(f"{name}: title row {r} is also a title row of sections[{titles[r]}]")
            titles.setdefault(r, i)
    sections = annotation.sections
    for i in range(len(sections)):
        for j in range(i + 1, len(sections)):
            a, b = sections[i], sections[j]
            if not (a.title_rows and b.title_rows):
                continue
            a0, a1, b0, b1 = a.first_row, a.last_row, b.first_row, b.last_row
            disjoint = a1 < b0 or b1 < a0
            nested = (a0 <= b0 and b1 <= a1) or (b0 <= a0 and a1 <= b1)
            if not (disjoint or nested):
                issues.append(f"sections[{i}] and sections[{j}] overlap without one containing "
                              f"the other (rows {a0}-{a1} and {b0}-{b1})")
            elif a0 == b0 and a1 == b1:
                issues.append(f"sections[{i}] and sections[{j}] cover the same rows ({a0}-{a1})")
    if header:
        above = [r for r in range(header[0]) if r not in titles]
        if above:
            issues.append(f"header_rows: rows {above} above the header are neither header rows "
                          "nor section titles")
    structural = header_set | set(titles)
    taken: dict[tuple[int, int], int] = {}
    for i, merge in enumerate(annotation.implicit_merges):
        name = f"implicit_merges[{i}]"
        c, r0, r1 = merge.column, merge.first_row, merge.last_row
        if not 0 <= c < cols or not 0 <= r0 < rows or not 0 <= r1 < rows:
            issues.append(f"{name}: column {c}, rows {r0}-{r1} are outside the table "
                          f"(columns 0-{cols - 1}, {span})")
            continue
        if r1 <= r0:
            issues.append(f"{name}: last_row {r1} must be below first_row {r0}")
            continue
        if structural & set(range(r0, r1 + 1)):
            issues.append(f"{name}: rows {sorted(structural & set(range(r0, r1 + 1)))} are header "
                          "or section title rows")
        anchor = grid.owner.get((r0, c))
        if anchor is None or not _clean(anchor.text):
            issues.append(f"{name}: the cell at row {r0}, column {c} is empty")
        bad = []
        for r in range(r0 + 1, r1 + 1):
            cell = grid.owner.get((r, c))
            if cell is not None and (_clean(cell.text) or (cell.start_row_offset_idx, cell.end_row_offset_idx,
                                                            cell.start_col_offset_idx, cell.end_col_offset_idx)
                                     != (r, r + 1, c, c + 1)):
                bad.append(r)
            if (r, c) in taken:
                issues.append(f"{name}: row {r}, column {c} is also in implicit_merges[{taken[(r, c)]}]")
            taken.setdefault((r, c), i)
        if bad:
            issues.append(f"{name}: in column {c}, rows {bad} are not empty cells of their own "
                          "(they hold text or belong to a merged cell)")
    return issues


def text_issues(field: str, text: str, digits: frozenset[str], count: Callable[[str], int],
                max_tokens: int, limit: str, lengths: Lengths) -> list[str]:
    if not text:
        return [f"{field}: is empty"]
    issues = []
    invented = sorted(_digits(text) - digits)
    if invented:
        issues.append(f"{field}: numbers not found in the table or the context: {', '.join(invented)}")
    if count(text) > max_tokens:
        issues.append(f"{field}: {lengths.size(text)}, write at most {limit}")
    return issues


# ---------------------------------------------------------------- calling

@dataclass(frozen=True)
class Attempt:
    raw: str
    reasoning: str | None
    issues: tuple[str, ...]
    prompt_tokens: int | None
    completion_tokens: int | None
    reasoning_tokens: int | None
    cost_usd: float | None
    cached: bool


class _StructuredLLM(Protocol):
    model: str

    def complete_structured(self, messages: list[dict[str, str]], name: str,
                            schema: dict[str, Any], reasoning_effort: str) -> Any: ...


@dataclass
class LLMCache:
    folder: Path

    def key(self, model: str, messages: list[dict[str, str]], schema: dict[str, Any]) -> str:
        blob = json.dumps({"model": model, "messages": messages, "schema": schema,
                           "reasoning_effort": REASONING_EFFORT, "prompt_version": PROMPT_VERSION},
                          ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def get(self, key: str) -> dict | None:
        path = self.folder / f"{key}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def put(self, key: str, value: dict) -> None:
        from rag.jsonio import write_atomic
        write_atomic(self.folder / f"{key}.json", json.dumps(value, ensure_ascii=False, indent=2))


def _call(llm: _StructuredLLM, messages: list[dict[str, str]], name: str, schema: dict[str, Any],
          cache: LLMCache | None) -> tuple[dict, bool]:
    key = cache.key(llm.model, messages, schema) if cache else ""
    hit = cache.get(key) if cache else None
    if hit is not None:
        return hit, True
    done = llm.complete_structured(messages, name, schema, REASONING_EFFORT)
    hit = {"text": done.text, "reasoning": done.reasoning, "prompt_tokens": done.prompt_tokens,
           "completion_tokens": done.completion_tokens, "reasoning_tokens": done.reasoning_tokens,
           "cost_usd": done.cost_usd}
    if cache:
        cache.put(key, hit)
    return hit, False


def _attempt(hit: dict, cached: bool, issues: list[str]) -> Attempt:
    return Attempt(hit["text"], hit.get("reasoning"), tuple(issues), hit.get("prompt_tokens"),
                   hit.get("completion_tokens"), hit.get("reasoning_tokens"), hit.get("cost_usd"),
                   cached)


def _retry(messages: list[dict[str, str]], raw: str, issues: list[str]) -> list[dict[str, str]]:
    return messages + [{"role": "assistant", "content": raw},
                       {"role": "user", "content": RETRY_TEMPLATE.format(
                           issues="\n".join(f"- {issue}" for issue in issues))}]


@dataclass(frozen=True)
class AnnotationResult:
    model: str
    attempts: tuple[Attempt, ...]
    annotation: TableAnnotation | None     # None: no attempt passed the annotation checks
    lead: str | None                       # None: no attempt passed the lead checks


def run_annotation(llm: _StructuredLLM, table: _Table, grid: Grid, context: tuple[ContextNode, ...],
                   lengths: Lengths, count: Callable[[str], int], cache: LLMCache | None,
                   extra_checks: Callable[[TableAnnotation], list[str]] = lambda _: [],
                   retries: int = 1) -> AnnotationResult:
    """The annotation and the lead are judged separately; each keeps its latest passing attempt."""
    messages = build_messages("annotation", grid, context, lengths)
    digits = known_digits(grid, context)
    attempts: list[Attempt] = []
    annotation: TableAnnotation | None = None
    lead: str | None = None
    for _ in range(retries + 1):
        hit, cached = _call(llm, messages, "table_annotation", ANNOTATION_SCHEMA, cache)
        parsed = parse_annotation(hit["text"])
        if parsed is None:
            issues = ["the answer does not follow the JSON schema"]
        else:
            candidate, candidate_lead = parsed
            structure = annotation_issues(candidate, table, grid)
            if not structure:
                structure = extra_checks(candidate)
            lead_found = text_issues("lead", candidate_lead, digits, count, LEAD_MAX_TOKENS,
                                     lengths.lead_limit, lengths)
            if not structure:
                annotation = candidate
            if not lead_found:
                lead = candidate_lead
            issues = structure + lead_found
        attempts.append(_attempt(hit, cached, issues))
        if not issues:
            break
        messages = _retry(messages, hit["text"], issues)
    return AnnotationResult(llm.model, tuple(attempts), annotation, lead)


@dataclass(frozen=True)
class SummaryResult:
    model: str
    attempts: tuple[Attempt, ...]
    summary: str | None


def run_summary(llm: _StructuredLLM, grid: Grid, context: tuple[ContextNode, ...], lengths: Lengths,
                count: Callable[[str], int], cache: LLMCache | None, retries: int = 1) -> SummaryResult:
    messages = build_messages("summary", grid, context, lengths)
    digits = known_digits(grid, context)
    attempts: list[Attempt] = []
    summary: str | None = None
    for _ in range(retries + 1):
        hit, cached = _call(llm, messages, "table_summary", SUMMARY_SCHEMA, cache)
        try:
            text = json.loads(hit["text"])["summary"].strip()
        except Exception:
            text = None
        issues = (["the answer does not follow the JSON schema"] if text is None else
                  text_issues("summary", text, digits, count, lengths.summary_max_tokens,
                              lengths.summary_limit, lengths))
        attempts.append(_attempt(hit, cached, issues))
        if not issues:
            summary = text
            break
        messages = _retry(messages, hit["text"], issues)
    return SummaryResult(llm.model, tuple(attempts), summary)


# ---------------------------------------------------------------- chunk planning

@dataclass(frozen=True)
class PlannedChunk:
    text: str
    context_line_ids: tuple[str, ...]      # header and title lines written again before the rows
    line_ids: tuple[str, ...]              # the chunk's own lines, in table order
    oversize: Literal["", "row", "context"] = ""   # a single line that does not fit, and why


def plan_chunks(serialized: SerializedTable, annotation: TableAnnotation | None,
                fits: Callable[[str], bool]) -> tuple[PlannedChunk, ...]:
    """Pack a table's lines into chunks.

    Without an annotation this is the rule packing: lines in order, the header line repeated
    in chunks after it. With one: every chunk repeats the header rows and the titles of the
    sections its first line lies in; sections are kept whole when they fit a chunk, and a
    section that fits none is packed by its sub-sections and rows. A lead sentence is written
    before each chunk by the caller, so chunks are the same with and without it; `fits` leaves
    room for it.
    """
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
