"""Excel parser: reads an .xlsx file (rag/ingest/xlsx/reader.py), cuts each sheet into regions
(rag/ingest/xlsx/regions.py) and writes them as the primary document. Rules: docs/rules/xlsx.md.

Reading order: sheets in workbook order (hidden ones included; a sheet without any shown cell is
left out), each opened by a title holding the sheet's name; inside a sheet, regions by their first
row, then first column. A row region is one paragraph, its cells joined by ` | `; a column region
is one paragraph per cell; a table region is a table read exactly from the file (`parser_exact`).
Text is each cell's display value. Pictures, charts and chart sheets are skipped with a warning;
formulas without a cached value and number formats written as General are warned about.

Positions are file addresses: `sheet<N>#name` for the sheet's name, `sheet<N>/r<row>c<col>` for a
cell, `sheet<N>/r<r1>c<c1>:r<r2>c<c2>` for a table. A text element's pieces say which characters of
which cell each part of its text is.

The native result (parse-native.json) holds the reader's facts and, apart from them, the regions.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from rag.ingest.parsers import ParseResult
from rag.ingest.parsers.docling_mapper import node_id
from rag.ingest.tables.models import StructuredTable, TableCell, TableSlot
from rag.ingest.xlsx.reader import Sheet, cell_address, cell_ref, read_xlsx
from rag.ingest.xlsx.regions import Region, cell_map, merges_in, sheet_regions
from rag.jsonio import to_jsonable
from rag.models import (
    DocxSpan, NativeTableFact, PrimaryDocument, PrimaryElement, PrimaryTablePlaceholder, PrimaryText,
    ProcessingWarning, SourceDocument, TextPiece,
)

# Rules version of the Excel parser (xlsx_native.py and rag/ingest/xlsx/). Bump it, to the release
# that ships the change, whenever this code changes its output: Excel documents are then rebuilt.
RULES_VERSION = "v4.3"

FALLBACK_LABELS = {"condition": "带条件的格式", "scaling": "末尾逗号缩放", "fraction": "分数",
                   "elapsed_time": "经过时间", "unsupported_token": "不支持的记号",
                   "unknown_builtin": "未知的内置格式", "negative_date": "负数日期"}


def table_address(sheet: int, region: Region) -> str:
    return f"sheet{sheet}/r{region.first_row}c{region.first_column}:r{region.last_row}c{region.last_column}"


def region_ref(region: Region) -> str:
    first = cell_ref(region.first_row, region.first_column)
    last = cell_ref(region.last_row, region.last_column)
    return first if first == last else f"{first}:{last}"


@dataclass
class _Builder:
    document_id: str
    elements: list[PrimaryElement] = field(default_factory=list)
    slots: list[TableSlot] = field(default_factory=list)
    tables: list[NativeTableFact] = field(default_factory=list)

    def text(self, address: str, label: str, parts: list[tuple[str, str]]) -> None:
        """One paragraph from (address, text) parts joined by ` | `; each part trimmed."""
        pieces, chunks, at = [], [], 0
        for part_address, raw in parts:
            body = raw.strip()
            if not body:
                continue
            if chunks:
                at += 3
            pieces.append(TextPiece(at, at + len(body), part_address, len(raw) - len(raw.lstrip())))
            chunks.append(body)
            at += len(body)
        if not chunks:
            return
        self.elements.append(PrimaryText(node_id(self.document_id, address), "paragraph", " | ".join(chunks),
                                         (DocxSpan(address, label, address),), address, (), tuple(pieces)))

    def sheet(self, sheet: Sheet, regions: tuple[Region, ...]) -> None:
        address = f"{sheet.address}#name"
        self.elements.append(PrimaryText(node_id(self.document_id, address), "title", sheet.name,
                                         (DocxSpan(address, sheet.name, address),), address, (),
                                         (TextPiece(0, len(sheet.name), address, 0),)))
        cells = cell_map(sheet)
        for region in regions:
            if region.kind == "table":
                self.table(sheet, region)
                continue
            label = f"{sheet.name}!{region_ref(region)}"
            parts = [(cell_address(sheet.index, r, c), cells[(r, c)].display) for r, c in region.cells]
            if region.kind == "row":
                self.text(parts[0][0], label, parts)
            else:
                for (r, c), part in zip(region.cells, parts):
                    self.text(part[0], f"{sheet.name}!{cell_ref(r, c)}", [part])

    def table(self, sheet: Sheet, region: Region) -> None:
        number = len(self.slots) + 1
        slot_id, table_id = f"slot_{number:03d}", f"xlsx_table_{number:03d}"
        address = table_address(sheet.index, region)
        span = DocxSpan(address, f"{sheet.name}!{region_ref(region)}", address)
        cells = cell_map(sheet)
        header_rows = 0
        if region.table_object is not None:
            header_rows = next(t.header_rows for t in sheet.tables if t.name == region.table_object)
        covered: set[tuple[int, int]] = set()
        spans: dict[tuple[int, int], tuple[int, int]] = {}
        for m in merges_in(sheet, region):
            spans[(m.first_row, m.first_column)] = (m.last_row - m.first_row + 1, m.last_column - m.first_column + 1)
            covered |= {(r, c) for r in range(m.first_row, m.last_row + 1)
                        for c in range(m.first_column, m.last_column + 1)} - {(m.first_row, m.first_column)}
        out: list[TableCell] = []
        for row in range(region.first_row, region.last_row + 1):
            for column in range(region.first_column, region.last_column + 1):
                if (row, column) in covered:
                    continue
                row_span, col_span = spans.get((row, column), (1, 1))
                r, c = row - region.first_row, column - region.first_column
                cell = cells.get((row, column))
                text = cell.display.strip() if cell is not None else ""
                at = cell_address(sheet.index, row, column)
                header = r < header_rows
                out.append(TableCell(f"{table_id}_r{r}c{c}", text, r, r + row_span, c, c + col_span,
                                     row_span, col_span, None, ("column_header",) if header else ("body",),
                                     "native", (at,), at))
        self.slots.append(TableSlot(slot_id, address, None, None, None, None, "eligible", None, (address,)))
        self.tables.append(NativeTableFact(slot_id, address, StructuredTable(
            table_id, "xlsx_native", "file", region.rows, region.columns, tuple(out), (), None, (span,), ())))
        self.elements.append(PrimaryTablePlaceholder(node_id(self.document_id, address), slot_id, address, (span,)))


def _warnings(book) -> list[ProcessingWarning]:
    out: list[ProcessingWarning] = []
    for name in book.chart_sheets:
        out.append(ProcessingWarning("unsupported_nontext_item", "xlsx_parsing", f"图表工作表「{name}」未入库", ()))
    for sheet in book.sheets:
        if sheet.images:
            out.append(ProcessingWarning("unsupported_nontext_item", "xlsx_parsing",
                                         f"工作表「{sheet.name}」的 {sheet.images} 张图片未入库", (sheet.address,)))
        if sheet.charts:
            out.append(ProcessingWarning("unsupported_nontext_item", "xlsx_parsing",
                                         f"工作表「{sheet.name}」的 {sheet.charts} 个图表未入库", (sheet.address,)))
        missing = [c for c in sheet.cells if c.no_cached_value]
        if missing:
            out.append(ProcessingWarning(
                "formula_without_cached_value", "xlsx_parsing",
                f"工作表「{sheet.name}」有 {len(missing)} 个公式没有缓存值，按空格处理："
                + "、".join(cell_ref(c.row, c.column) for c in missing[:20]),
                tuple(cell_address(sheet.index, c.row, c.column) for c in missing)))
        fallback: dict[str, list] = defaultdict(list)
        for c in sheet.cells:
            if c.fallback:
                fallback[c.fallback].append(c)
        for reason, found in fallback.items():
            out.append(ProcessingWarning(
                "number_format_fallback", "xlsx_parsing",
                f"工作表「{sheet.name}」有 {len(found)} 格的数字格式（{FALLBACK_LABELS.get(reason, reason)}）"
                f"按 General 写出：" + "、".join(cell_ref(c.row, c.column) for c in found[:20]),
                tuple(cell_address(sheet.index, c.row, c.column) for c in found)))
    return out


class XlsxNativeParser:
    provides_table_slots = True

    def identity(self) -> dict[str, Any]:
        from rag.ingest.libraries import versions
        return {"rules": RULES_VERSION, "libraries": versions("openpyxl")}

    def parse(self, source: SourceDocument, cache: Any = None) -> ParseResult:
        """Reading takes about a second per workbook, so it is not cached."""
        book = read_xlsx(source.absolute_path)
        builder = _Builder(source.document_id)
        regions = {}
        for sheet in book.sheets:
            regions[sheet.index] = sheet_regions(sheet)
            if regions[sheet.index]:
                builder.sheet(sheet, regions[sheet.index])
        primary = PrimaryDocument(source.document_id, source.file_hash, max(1, len(book.sheets)),
                                  tuple(builder.elements), tuple(builder.slots), tuple(builder.tables),
                                  tuple(_warnings(book)))
        native = {"format": "xlsx_reader_v1", **to_jsonable(book),
                  "regions": {str(index): to_jsonable(found) for index, found in regions.items()}}
        return ParseResult(primary, native)
