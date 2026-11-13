"""Read a .docx file into addressed blocks (docs/rules/docx.md). The parser (docx_native) and
excerpt locating (anchors) both read files through this module, so their addresses agree.

Addresses: `<part>/<block>` for a paragraph or table of a part; inside a table
`<table>/r<row>c<col>/<block>` (the cell's start row and grid column, then the block in the cell);
a text-box paragraph adds `/t<n>` to its anchoring paragraph. The numbering text of a paragraph has
its address with `#label` appended. Blocks are numbered from 0 in document order, paragraphs and
tables alike; content controls are transparent; empty paragraphs are numbered too.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Literal

import docx
from lxml import etree

from rag.ingest.docx.numbering import W, Numbering

MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PartKind = Literal["body", "header", "footer", "footnotes", "endnotes"]
HEADER_TYPES = ("default", "first", "even")
TRANSPARENT = {f"{W}sdt", f"{W}customXml", f"{W}smartTag"}       # their content is read in place
SKIPPED = {f"{W}pPr", f"{W}rPr", f"{W}del", f"{W}moveFrom", f"{W}instrText", f"{W}delInstrText",
           f"{W}delText", f"{W}sdtPr", f"{W}sdtEndPr", f"{W}footnoteReference", f"{W}endnoteReference",
           f"{W}footnoteRef", f"{W}endnoteRef", f"{W}commentReference", f"{W}annotationRef"}
OBJECTS = {f"{W}drawing", f"{W}pict", f"{W}object"}
LEAVES = {f"{W}{name}" for name in ("t", "tab", "ptab", "br", "cr", "sym", "noBreakHyphen", "softHyphen")}


@dataclass(frozen=True)
class Paragraph:
    address: str
    text: str                         # by the text rules, not stripped
    label: str | None                 # numbering text; None for bullets and unnumbered paragraphs
    bullet: str | None                # the bullet text of a bulleted paragraph
    level: int                        # numbering level (w:ilvl); 0 when not numbered
    heading: int | None               # outline level 0–8 when the paragraph is a heading
    toc: bool                         # a table-of-contents paragraph
    objects: tuple[str, ...]          # pictures, charts, OLE objects skipped: their kinds
    textbox: tuple["Block", ...] = () # text-box content anchored in the paragraph

    @property
    def numbered(self) -> bool:
        return self.label is not None or self.bullet is not None


@dataclass(frozen=True)
class Cell:
    row: int
    column: int
    row_span: int
    col_span: int
    address: str
    blocks: tuple["Block", ...]
    header: bool                      # its row repeats as a header row (w:tblHeader)


@dataclass(frozen=True)
class Table:
    address: str
    rows: int
    columns: int
    cells: tuple[Cell, ...]
    uncovered: tuple[tuple[int, int], ...]   # grid positions no cell covers (gridBefore, gridAfter, short rows)


Block = Paragraph | Table


@dataclass(frozen=True)
class Part:
    name: str                         # body, header<n>, footer<n>, footnotes, endnotes
    kind: PartKind
    blocks: tuple[Block, ...]


@dataclass(frozen=True)
class DocxFile:
    parts: tuple[Part, ...]           # reading order: headers, body, footnotes, endnotes, footers
    unknown_formats: tuple[str, ...]  # numbering formats written as decimal


@dataclass(frozen=True)
class Unit:
    """A piece of text with its address: what excerpts are located in."""
    address: str
    text: str


@dataclass
class _Style:
    name: str
    based_on: str | None
    outline: int | None
    num_id: str | None
    ilvl: int | None


def _val(element) -> str | None:
    return None if element is None else element.get(f"{W}val")


def _styles(xml: bytes | None) -> tuple[dict[str, _Style], str | None]:
    styles: dict[str, _Style] = {}
    default = None
    if not xml:
        return styles, default
    for style in etree.fromstring(xml).iter(f"{W}style"):
        if style.get(f"{W}type") != "paragraph":
            continue
        ident = style.get(f"{W}styleId") or ""
        ppr = style.find(f"{W}pPr")
        outline = _val(ppr.find(f"{W}outlineLvl")) if ppr is not None else None
        numpr = ppr.find(f"{W}numPr") if ppr is not None else None
        ilvl = _val(numpr.find(f"{W}ilvl")) if numpr is not None else None
        styles[ident] = _Style((_val(style.find(f"{W}name")) or ident).casefold(),
                               _val(style.find(f"{W}basedOn")),
                               int(outline) if outline is not None and outline.isdigit() else None,
                               _val(numpr.find(f"{W}numId")) if numpr is not None else None,
                               int(ilvl) if ilvl is not None and ilvl.isdigit() else None)
        if style.get(f"{W}default") in ("1", "true"):
            default = ident
    return styles, default


class _Reader:
    def __init__(self, styles: dict[str, _Style], default_style: str | None, numbering: Numbering):
        self.styles, self.default_style, self.numbering = styles, default_style, numbering
        self.fields: list[dict] = []          # open fields: instruction text and whether in the result

    # --- styles -----------------------------------------------------------------------------
    def _chain(self, style_id: str | None) -> Iterator[tuple[str, _Style]]:
        seen: set[str] = set()
        while style_id and style_id in self.styles and style_id not in seen:
            seen.add(style_id)
            yield style_id, self.styles[style_id]
            style_id = self.styles[style_id].based_on

    def _heading(self, ppr, style_id: str | None) -> int | None:
        own = _val(ppr.find(f"{W}outlineLvl")) if ppr is not None else None
        if own is not None and own.isdigit():
            return int(own) if int(own) <= 8 else None
        for _, style in self._chain(style_id):
            if style.outline is not None:
                return style.outline if style.outline <= 8 else None
        name = self.styles[style_id].name if style_id in self.styles else ""
        if name == "title":
            return 0
        match = re.fullmatch(r"heading ([1-9])", name)
        return int(match.group(1)) - 1 if match else None

    def _numbering(self, ppr, style_id: str | None) -> tuple[str, int] | None:
        numpr = ppr.find(f"{W}numPr") if ppr is not None else None
        num_id = _val(numpr.find(f"{W}numId")) if numpr is not None else None
        ilvl = _val(numpr.find(f"{W}ilvl")) if numpr is not None else None
        level = int(ilvl) if ilvl is not None and ilvl.isdigit() else None
        for ident, style in self._chain(style_id):
            if num_id is None and style.num_id is not None:
                num_id = style.num_id
            if level is None and style.ilvl is not None:
                level = style.ilvl
            if num_id is not None and level is None:
                level = self.numbering.level_for_style(num_id, ident)
        if num_id is None or num_id == "0":
            return None
        return num_id, level or 0

    # --- text -------------------------------------------------------------------------------
    def _in_result(self) -> bool:
        return all(item["result"] for item in self.fields)

    def _in_toc(self) -> bool:
        return any(item["instr"].strip().upper().startswith("TOC") for item in self.fields)

    def _runs(self, element, out: list[str], objects: list[str], boxes: list, state: dict) -> None:
        for child in element:
            tag = child.tag
            if not isinstance(tag, str) or tag in SKIPPED:
                if tag == f"{W}instrText" and self.fields and not self.fields[-1]["result"]:
                    self.fields[-1]["instr"] += child.text or ""
                continue
            state["toc"] |= self._in_toc()
            if tag == f"{W}fldChar":
                kind = child.get(f"{W}fldCharType")
                if kind == "begin":
                    self.fields.append({"instr": "", "result": False})
                elif kind == "separate" and self.fields:
                    self.fields[-1]["result"] = True
                elif kind == "end" and self.fields:
                    self.fields.pop()
                state["toc"] |= self._in_toc()
            elif tag == f"{W}fldSimple":
                self.fields.append({"instr": child.get(f"{W}instr") or "", "result": True})
                state["toc"] |= self._in_toc()
                self._runs(child, out, objects, boxes, state)
                self.fields.pop()
            elif tag == f"{MC}AlternateContent":
                choice = child.find(f"{MC}Choice")
                if choice is not None:
                    self._runs(choice, out, objects, boxes, state)
            elif tag in OBJECTS:
                contents = list(child.iter(f"{W}txbxContent"))
                if contents:
                    boxes.extend(contents)
                elif self._in_result():
                    objects.append(_object_kind(child))
            elif tag in LEAVES and not self._in_result():
                continue                      # field code, not shown
            elif tag == f"{W}t":
                out.append(child.text or "")
            elif tag == f"{W}tab" or tag == f"{W}ptab":
                out.append(" ")
            elif tag in (f"{W}br", f"{W}cr"):
                out.append("\n")
            elif tag == f"{W}sym":
                code = child.get(f"{W}char") or ""
                try:
                    out.append(chr(int(code, 16)))
                except ValueError:
                    pass
            elif tag == f"{W}noBreakHyphen":
                out.append("-")
            elif tag == f"{W}softHyphen":
                continue
            else:
                self._runs(child, out, objects, boxes, state)

    def paragraph(self, element, address: str) -> Paragraph:
        ppr = element.find(f"{W}pPr")
        style_id = _val(ppr.find(f"{W}pStyle")) if ppr is not None else None
        style_id = style_id or self.default_style
        state = {"toc": self._in_toc()}
        out: list[str] = []
        objects: list[str] = []
        boxes: list = []
        self._runs(element, out, objects, boxes, state)
        style_name = self.styles[style_id].name if style_id in self.styles else ""
        toc = state["toc"] or bool(re.fullmatch(r"toc [1-9]", style_name))
        label = bullet = None
        level = 0
        numbering = self._numbering(ppr, style_id)
        if numbering is not None:
            level = numbering[1]
            written = self.numbering.label(*numbering)
            if written is not None and written.text:
                if written.bullet:
                    bullet = written.text
                else:
                    label = written.text
        textbox: list[Block] = []
        for content in boxes:
            textbox += self.blocks(content, f"{address}/t", start=len(textbox), joiner="")
        return Paragraph(address, "".join(out), label, bullet, level, self._heading(ppr, style_id), toc,
                         tuple(objects), tuple(textbox))

    # --- blocks -----------------------------------------------------------------------------
    def blocks(self, container, prefix: str, start: int = 0, joiner: str = "/") -> list[Block]:
        found: list[Block] = []
        for element in _block_elements(container):
            address = f"{prefix}{joiner}{start + len(found)}"
            if element.tag == f"{W}p":
                found.append(self.paragraph(element, address))
            else:
                found.append(self.table(element, address))
        return found

    def table(self, element, address: str) -> Table:
        grid = element.find(f"{W}tblGrid")
        columns = len(grid.findall(f"{W}gridCol")) if grid is not None else 0
        cells: list[dict] = []
        open_merges: dict[int, dict] = {}       # grid column → the cell a vertical merge continues
        covered: set[tuple[int, int]] = set()
        rows = list(_children(element, f"{W}tr"))
        for r, row in enumerate(rows):
            trpr = row.find(f"{W}trPr")
            header = trpr is not None and trpr.find(f"{W}tblHeader") is not None and \
                _val(trpr.find(f"{W}tblHeader")) not in ("0", "false")
            before = trpr.find(f"{W}gridBefore") if trpr is not None else None
            column = int(_val(before) or 0) if before is not None else 0
            previous: dict | None = None
            for tc in _children(row, f"{W}tc"):
                tcpr = tc.find(f"{W}tcPr")
                span_el = tcpr.find(f"{W}gridSpan") if tcpr is not None else None
                span = int(_val(span_el) or 1) if span_el is not None else 1
                hmerge = tcpr.find(f"{W}hMerge") if tcpr is not None else None
                vmerge = tcpr.find(f"{W}vMerge") if tcpr is not None else None
                if hmerge is not None and _val(hmerge) != "restart" and previous is not None:
                    previous["col_span"] += span            # legacy horizontal merge continues
                    for c in range(column, column + span):
                        covered.add((r, c))
                    column += span
                    continue
                if vmerge is not None and _val(vmerge) != "restart" and column in open_merges:
                    merged = open_merges[column]
                    merged["row_span"] = r - merged["row"] + 1
                    for c in range(column, column + span):
                        covered.add((r, c))
                    column += span
                    previous = None
                    continue
                cell_address = f"{address}/r{r}c{column}"
                cell = {"row": r, "column": column, "row_span": 1, "col_span": span, "address": cell_address,
                        "blocks": tuple(self.blocks(tc, cell_address)), "header": header}
                cells.append(cell)
                for c in range(column, column + span):
                    covered.add((r, c))
                    open_merges.pop(c, None)
                if vmerge is not None:
                    open_merges[column] = cell
                previous = cell
                column += span
            columns = max(columns, column)
        uncovered = tuple((r, c) for r in range(len(rows)) for c in range(columns) if (r, c) not in covered)
        return Table(address, len(rows), columns,
                     tuple(Cell(c["row"], c["column"], c["row_span"], c["col_span"], c["address"],
                                c["blocks"], c["header"]) for c in cells), uncovered)


def _object_kind(element) -> str:
    uris = [g.get("uri") or "" for g in element.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}graphicData")]
    if any(uri.endswith("/chart") for uri in uris):
        return "chart"
    if element.tag == f"{W}object" or list(element.iter("{urn:schemas-microsoft-com:office:office}OLEObject")):
        return "ole"
    return "picture"


def _children(element, tag: str) -> Iterator:
    """Child elements of one tag, looking through content controls and custom XML."""
    for child in element:
        if child.tag == tag:
            yield child
        elif child.tag in TRANSPARENT:
            content = child.find(f"{W}sdtContent") if child.tag == f"{W}sdt" else child
            if content is not None:
                yield from _children(content, tag)


def _block_elements(container) -> Iterator:
    for child in container:
        if child.tag in (f"{W}p", f"{W}tbl"):
            yield child
        elif child.tag in TRANSPARENT:
            content = child.find(f"{W}sdtContent") if child.tag == f"{W}sdt" else child
            if content is not None:
                yield from _block_elements(content)


def _part_name(partname: str) -> str:
    return Path(partname).stem


def read_docx(path: Path) -> DocxFile:
    document = docx.Document(str(path))
    main = document.part
    parts_by_name = {str(part.partname): part for part in main.package.iter_parts()}

    def blob(name: str) -> bytes | None:
        part = parts_by_name.get(name)
        return part.blob if part is not None else None

    styles, default_style = _styles(blob("/word/styles.xml"))
    style_numbers = {ident: style.num_id for ident, style in styles.items() if style.num_id}
    numbering = Numbering.load(blob("/word/numbering.xml"), style_numbers)
    body_root = etree.fromstring(main.blob).find(f"{W}body")
    reader = _Reader(styles, default_style, numbering)
    body = Part("body", "body", tuple(reader.blocks(body_root, "body")))

    def header_parts(kind: str) -> list[Part]:
        found: list[Part] = []
        names: set[str] = set()
        texts: set[tuple[str, ...]] = set()
        for sect in body_root.iter(f"{W}sectPr"):
            for kind_name in HEADER_TYPES:
                for ref in sect.findall(f"{W}{kind}Reference"):
                    if ref.get(f"{W}type", "default") != kind_name:
                        continue
                    rel = main.rels.get(ref.get(f"{R}id"))
                    if rel is None or rel.is_external:
                        continue
                    target = rel.target_part
                    name = _part_name(str(target.partname))
                    if name in names:
                        continue
                    names.add(name)
                    own = _Reader(styles, default_style, numbering.fresh())
                    part = Part(name, kind, tuple(own.blocks(etree.fromstring(target.blob), name)))
                    content = tuple(unit.text for unit in units(part))
                    if content in texts:
                        continue
                    texts.add(content)
                    found.append(part)
        return found

    def notes(kind: str) -> list[Part]:
        xml = blob(f"/word/{kind}.xml")
        if xml is None:
            return []
        own = _Reader(styles, default_style, numbering.fresh())
        blocks: list[Block] = []
        for note in etree.fromstring(xml).findall(f"{W}{kind[:-1]}"):
            if note.get(f"{W}type") in ("separator", "continuationSeparator", "continuationNotice"):
                continue
            blocks += own.blocks(note, kind, start=len(blocks))
        return [Part(kind, kind, tuple(blocks))] if blocks else []   # type: ignore[arg-type]

    parts = (header_parts("header") + [body] + notes("footnotes") + notes("endnotes")
             + header_parts("footer"))
    unknown = numbering.unknown_formats
    return DocxFile(tuple(parts), tuple(sorted(unknown)))


# --- units -----------------------------------------------------------------------------------

def cell_text_blocks(blocks: tuple[Block, ...]) -> Iterator[tuple[str, str]]:
    """(address, text) of the blocks of a cell as the cell text writes them: numbered paragraphs
    with their label (a bullet as "-"), a nested table as rows of "a | b | c", text boxes after
    their paragraph; empty paragraphs left out."""
    for block in blocks:
        if isinstance(block, Paragraph):
            body = block.text.strip()
            marker = block.label if block.label is not None else "-" if block.bullet is not None else None
            if body or marker:
                yield block.address, f"{marker} {body}".strip() if marker else body
            yield from cell_text_blocks(block.textbox)
        else:
            yield block.address, nested_table_text(block)


def nested_table_text(table: Table) -> str:
    lines = []
    for r in range(table.rows):
        row = sorted((c for c in table.cells if c.row == r), key=lambda c: c.column)
        lines.append(" | ".join(" ".join(text for _, text in cell_text_blocks(c.blocks)).replace("\n", " ")
                                for c in row))
    return "\n".join(line for line in lines if line.strip(" |"))


def cell_text(cell: Cell) -> str:
    return "\n".join(text for _, text in cell_text_blocks(cell.blocks))


def _block_units(block: Block) -> Iterator[Unit]:
    if isinstance(block, Paragraph):
        if block.label is not None:
            yield Unit(block.address + "#label", block.label)
        yield Unit(block.address, block.text)
        for inner in block.textbox:
            yield from _block_units(inner)
    else:
        for cell in sorted(block.cells, key=lambda c: (c.row, c.column)):
            for inner in cell.blocks:
                yield from _block_units(inner)


def units(part: Part) -> Iterator[Unit]:
    for block in part.blocks:
        yield from _block_units(block)


def all_units(file: DocxFile) -> list[Unit]:
    """Every addressed text of the file in reading order (labels before their paragraph)."""
    return [unit for part in file.parts for unit in units(part)]
