"""Cut each sheet into regions: the only judgment the Excel parser makes (docs/rules/xlsx.md).

Excel has no notion of a table besides Table objects, so the parser must say where tables are. It
does so by geometry alone, never by content:

1. A Table object's range is a region; its cells take no part in what follows.
2. Occupied cells (a display value, covered by a merge whose first cell has one, or empty with a
   border on any side) that share an edge are connected; each connected set is a region.
3. Regions are merged, until no two can be: when their bounding rectangles overlap, or when both
   span at least two rows and start and end on the same rows (side by side, top and bottom
   aligned). A region without any value cell (only empty bordered cells) is dropped.
4. By the region's value cells (a merge counts once, at its first cell): all in one row → a row of
   text; all in one column → one paragraph per cell; otherwise a table, whose grid is the bounding
   rectangle (positions nobody occupies are empty cells). A Table object is always a table.

Borders are what draws a table on screen; they decide only which cells a table covers, never what
its rows mean. Titles above a table, section rows inside it, notes beside it stay where the
geometry puts them; what they mean is for the table structure step.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from rag.ingest.xlsx.reader import Cell, Merge, Sheet

RegionKind = Literal["table", "row", "column"]


@dataclass(frozen=True)
class Region:
    kind: RegionKind
    first_row: int
    first_column: int
    last_row: int
    last_column: int
    cells: tuple[tuple[int, int], ...]     # value cells, by row then column
    table_object: str | None = None        # the Table object's name, when the region is one

    @property
    def rows(self) -> int:
        return self.last_row - self.first_row + 1

    @property
    def columns(self) -> int:
        return self.last_column - self.first_column + 1


def _box(points) -> tuple[int, int, int, int]:
    rows = [r for r, _ in points]
    columns = [c for _, c in points]
    return min(rows), min(columns), max(rows), max(columns)


def _overlap(a, b) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def _joined(a, b) -> bool:
    """Overlapping rectangles, or side by side on exactly the same rows (two rows or more)."""
    aligned = a[0] == b[0] and a[2] == b[2] and a[2] > a[0]
    return _overlap(a, b) or aligned


def sheet_regions(sheet: Sheet) -> tuple[Region, ...]:
    shown = {(c.row, c.column): c for c in sheet.cells if c.display.strip()}
    merges = [m for m in sheet.merges if (m.first_row, m.first_column) in shown]
    # position → its value cell; None for an empty bordered cell
    occupied: dict[tuple[int, int], tuple[int, int] | None] = {p: None for p in sheet.bordered}
    occupied.update({p: p for p in shown})
    for m in merges:
        for row in range(m.first_row, m.last_row + 1):
            for column in range(m.first_column, m.last_column + 1):
                occupied[(row, column)] = (m.first_row, m.first_column)
    regions: list[Region] = []
    for table in sheet.tables:
        inside = [p for p in occupied
                  if table.first_row <= p[0] <= table.last_row and table.first_column <= p[1] <= table.last_column]
        values = sorted({occupied[p] for p in inside} - {None})
        for p in inside:
            del occupied[p]
        regions.append(Region("table", table.first_row, table.first_column, table.last_row,
                              table.last_column, tuple(values), table.name))
    # Connected sets of occupied positions.
    groups: list[set[tuple[int, int]]] = []
    seen: set[tuple[int, int]] = set()
    for start in sorted(occupied):
        if start in seen:
            continue
        group, stack = set(), [start]
        seen.add(start)
        while stack:
            row, column = stack.pop()
            group.add((row, column))
            for near in ((row - 1, column), (row + 1, column), (row, column - 1), (row, column + 1)):
                if near in occupied and near not in seen:
                    seen.add(near)
                    stack.append(near)
        groups.append(group)
    # Merge groups (step 3), until no two can be.
    boxes = [(_box(g), g) for g in groups]
    merged = True
    while merged:
        merged = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                if _joined(boxes[i][0], boxes[j][0]):
                    group = boxes[i][1] | boxes[j][1]
                    boxes[i] = (_box(group), group)
                    del boxes[j]
                    merged = True
                    break
            if merged:
                break
    for box, group in boxes:
        values = tuple(sorted({occupied[p] for p in group} - {None}))
        if not values:
            continue
        if len({r for r, _ in values}) == 1:
            kind: RegionKind = "row"
        elif len({c for _, c in values}) == 1:
            kind = "column"
        else:
            kind = "table"
        regions.append(Region(kind, *box, values))
    return tuple(sorted(regions, key=lambda r: (r.first_row, r.first_column)))


def merges_in(sheet: Sheet, region: Region) -> tuple[Merge, ...]:
    """The merges lying wholly inside the region (an empty merge too: it is part of the grid)."""
    return tuple(m for m in sheet.merges
                 if region.first_row <= m.first_row and m.last_row <= region.last_row
                 and region.first_column <= m.first_column and m.last_column <= region.last_column)


def cell_map(sheet: Sheet) -> dict[tuple[int, int], Cell]:
    return {(c.row, c.column): c for c in sheet.cells}
