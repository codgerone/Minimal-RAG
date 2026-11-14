"""Word reading: text rules, numbering, headings, tables and addresses (docs/rules/docx.md), on
documents synthesized with python-docx."""

from pathlib import Path

import docx
import pytest
from lxml import etree

from rag.ingest.docx.numbering import format_value
from rag.ingest.docx.reader import Paragraph, Table, all_units, cell_text, read_docx
from rag.ingest.parsers.docx_native import DocxNativeParser
from rag.ingest.sources import discover
from rag.models import PrimaryList, PrimaryTablePlaceholder, PrimaryText

NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
      'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
      'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
      'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
      'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"')
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

NUMBERING = f"""<w:numbering {NS}>
  <w:abstractNum w:abstractNumId="1">
    <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl>
    <w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="lowerLetter"/><w:lvlText w:val="%2)"/></w:lvl>
    <w:lvl w:ilvl="2"><w:start w:val="1"/><w:numFmt w:val="lowerRoman"/><w:lvlText w:val="%1.%2.%3"/></w:lvl>
  </w:abstractNum>
  <w:abstractNum w:abstractNumId="2">
    <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="-"/></w:lvl>
  </w:abstractNum>
  <w:abstractNum w:abstractNumId="3">
    <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="upperRoman"/><w:lvlText w:val="%1."/></w:lvl>
    <w:lvl w:ilvl="1"><w:start w:val="1"/><w:isLgl/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1.%2"/></w:lvl>
  </w:abstractNum>
  <w:abstractNum w:abstractNumId="4">
    <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="chineseCounting"/><w:lvlText w:val="第%1条"/><w:pStyle w:val="1"/></w:lvl>
  </w:abstractNum>
  <w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num>
  <w:num w:numId="2"><w:abstractNumId w:val="2"/></w:num>
  <w:num w:numId="3"><w:abstractNumId w:val="1"/></w:num>
  <w:num w:numId="4"><w:abstractNumId w:val="1"/><w:lvlOverride w:ilvl="0"><w:startOverride w:val="5"/></w:lvlOverride></w:num>
  <w:num w:numId="5"><w:abstractNumId w:val="3"/></w:num>
  <w:num w:numId="6"><w:abstractNumId w:val="4"/></w:num>
</w:numbering>"""

# A localized heading style: ID "1", name "heading 1", linked to numbering 6.
STYLES = (f'<w:style {NS} w:type="paragraph" w:styleId="1"><w:name w:val="heading 1"/>'
          '<w:pPr><w:numPr><w:numId w:val="6"/></w:numPr></w:pPr></w:style>',
          f'<w:style {NS} w:type="paragraph" w:styleId="TOC1"><w:name w:val="toc 1"/></w:style>')


def p(inner: str = "", num: tuple[int, int] | None = None, style: str | None = None) -> str:
    props = ""
    if style:
        props += f'<w:pStyle w:val="{style}"/>'
    if num:
        props += f'<w:numPr><w:ilvl w:val="{num[1]}"/><w:numId w:val="{num[0]}"/></w:numPr>'
    return f"<w:p>{'<w:pPr>' + props + '</w:pPr>' if props else ''}{inner}</w:p>"


def r(text: str) -> str:
    return f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r>'


def tc(text: str, props: str = "") -> str:
    return f"<w:tc><w:tcPr>{props}</w:tcPr>{p(r(text)) if text else p()}</w:tc>"


TEXTBOX = ('<w:r><mc:AlternateContent><mc:Choice Requires="wps"><w:drawing><wp:anchor><a:graphic>'
           '<a:graphicData uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
           '<wps:wsp><wps:txbx><w:txbxContent>' + p(r("In the box")) + '</w:txbxContent></wps:txbx></wps:wsp>'
           '</a:graphicData></a:graphic></wp:anchor></w:drawing></mc:Choice>'
           '<mc:Fallback><w:pict>' + p(r("In the box")) + '</w:pict></mc:Fallback></mc:AlternateContent></w:r>')

BODY = [
    p(r("Visible ") + '<w:ins><w:r><w:t>added</w:t></w:r></w:ins><w:del><w:r><w:delText>gone</w:delText></w:r></w:del>'
      + '<w:r><w:tab/></w:r>' + r("end")),                                             # 0
    p('<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText>PAGE</w:instrText></w:r>'
      '<w:r><w:fldChar w:fldCharType="separate"/></w:r>' + r("7") + '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
      + r(" of ") + '<w:fldSimple w:instr="NUMPAGES">' + r("9") + '</w:fldSimple>'),     # 1
    p(r("Clause one"), num=(1, 0)),                                                      # 2
    p(r("Sub a"), num=(1, 1)),                                                           # 3
    p(r("Deep"), num=(1, 2)),                                                            # 4
    p(r("Clause two"), num=(3, 0)),           # another num of the same list continues: 2.
    p(r("Sub restarts"), num=(1, 1)),         # a) again after level 0 advanced
    p(r("Restarted list"), num=(4, 0)),       # start override: 5.
    p(r("A bullet"), num=(2, 0)),                                                        # 8
    p(r("Roman"), num=(5, 0)),                                                           # 9
    p(r("Legal"), num=(5, 1)),                # isLgl: I. becomes 1 → 1.1
    p(r("Chapter"), style="1"),               # heading by localized style, numbered 第一条   # 11
    p(r("Anchor") + TEXTBOX),                                                            # 12
    p(r("Contents 3"), style="TOC1"),                                                    # 13
    p(),                                                                                 # 14 empty, still numbered
    ('<w:tbl><w:tblGrid><w:gridCol/><w:gridCol/><w:gridCol/></w:tblGrid>'                # 15
     '<w:tr><w:trPr><w:tblHeader/></w:trPr>' + tc("Wide", '<w:gridSpan w:val="2"/>') + tc("C") + '</w:tr>'
     '<w:tr>' + tc("Tall", '<w:vMerge w:val="restart"/>') + tc("x") + tc("y") + '</w:tr>'
     '<w:tr>' + tc("", "<w:vMerge/>") + tc("z")
     + '<w:tc><w:tcPr/><w:tbl><w:tblGrid><w:gridCol/><w:gridCol/></w:tblGrid><w:tr>' + tc("n1") + tc("n2")
     + '</w:tr></w:tbl>' + p() + '</w:tc></w:tr></w:tbl>'),
    ('<w:tbl><w:tblGrid><w:gridCol/></w:tblGrid>'                                         # 16 layout frame
     '<w:tr><w:tc>' + p(r("Framed heading"), style="1") + p(r("Framed text")) + '</w:tc></w:tr></w:tbl>'),
]


@pytest.fixture(scope="module")
def sample(tmp_path_factory) -> Path:
    document = docx.Document()
    body = document.element.body
    for child in list(body):
        if child.tag != f"{W}sectPr":
            body.remove(child)
    sect = body.find(f"{W}sectPr")
    for xml in BODY:
        sect.addprevious(etree.fromstring(f"<w:body {NS}>{xml}</w:body>")[0])
    numbering = document.part.numbering_part.element
    for child in list(numbering):
        numbering.remove(child)
    for child in etree.fromstring(NUMBERING):
        numbering.append(child)
    for xml in STYLES:
        document.styles.element.append(etree.fromstring(xml))
    document.sections[0].header.paragraphs[0].text = "Header line"
    document.sections[0].footer.paragraphs[0].text = "Footer line"
    path = tmp_path_factory.mktemp("docx") / "sample.docx"
    document.save(path)
    return path


def body_blocks(path: Path):
    file = read_docx(path)
    return file, next(part for part in file.parts if part.name == "body").blocks


def test_text_rules_accept_revisions_keep_field_results_and_write_tabs_as_spaces(sample):
    _, blocks = body_blocks(sample)
    assert blocks[0].text == "Visible added end"
    assert blocks[1].text == "7 of 9"


def test_numbering_follows_levels_restarts_overrides_and_legal_style(sample):
    _, blocks = body_blocks(sample)
    labels = [(b.label, b.bullet) for b in blocks[2:12]]
    assert labels == [("1.", None), ("a)", None), ("1.a.i", None), ("2.", None), ("a)", None),
                      ("5.", None), (None, "-"), ("I.", None), ("1.1", None), ("第一条", None)]


def test_headings_come_from_style_names_toc_is_marked_and_text_boxes_follow_their_anchor(sample):
    _, blocks = body_blocks(sample)
    assert blocks[11].heading == 0 and blocks[2].heading is None
    assert blocks[13].toc
    box = blocks[12].textbox
    assert [(b.address, b.text) for b in box] == [("body/12/t0", "In the box")]   # Fallback copy not read


def test_tables_follow_the_grid_merges_and_nested_tables(sample):
    _, blocks = body_blocks(sample)
    table = blocks[15]
    assert isinstance(table, Table) and (table.rows, table.columns) == (3, 3)
    cells = {(c.row, c.column): c for c in table.cells}
    assert (cells[0, 0].col_span, cells[0, 0].header) == (2, True)
    assert (cells[1, 0].row_span, (2, 0) in cells) == (2, False)
    assert cell_text(cells[2, 2]) == "n1 | n2" and cells[2, 2].address == "body/15/r2c2"


def test_units_carry_labels_separately_and_addresses_inside_cells(sample):
    file, _ = body_blocks(sample)
    units = {u.address: u.text for u in all_units(file)}
    assert units["body/2#label"] == "1." and units["body/2"] == "Clause one"
    assert units["body/15/r1c1/0"] == "x"
    assert any(address.startswith("header") for address in units)


def test_parser_builds_headings_lists_tables_and_reads_one_column_tables_as_text(sample):
    source = next(s for s in discover(sample.parent) if s.file_type == "docx")
    primary = DocxNativeParser().parse(source).primary
    titles = [e for e in primary.elements if isinstance(e, PrimaryText) and e.kind == "title"]
    assert [t.text for t in titles] == ["第一条 Chapter", "第二条 Framed heading"]
    assert titles[0].pieces[0].address == "body/11#label"
    framed = next(e for e in primary.elements if isinstance(e, PrimaryText) and e.text == "Framed text")
    assert framed.sources[0].address == "body/16/r0c0/1" and framed.sources[0].label == "第 17 块"
    lists = [e for e in primary.elements if isinstance(e, PrimaryList)]
    first = lists[0].items
    assert [(i.ordinal, i.level) for i in first[:3]] == [("1.", 0), ("a)", 1), ("1.a.i", 2)]
    assert first[1].parent_item_id == first[0].item_id
    assert [e.slot_id for e in primary.elements if isinstance(e, PrimaryTablePlaceholder)] == ["slot_001"]
    assert all("Contents" not in e.text for e in primary.elements if isinstance(e, PrimaryText))
    kinds = [e.kind for e in primary.elements if isinstance(e, PrimaryText)]
    assert kinds[0] == "header" and kinds[-1] == "footer" and primary.page_count is None


@pytest.mark.parametrize("value, fmt, written", [
    (27, "lowerLetter", "aa"), (14, "upperRoman", "XIV"), (5, "decimalZero", "05"),
    (11, "chineseCounting", "十一"), (105, "chineseCountingThousand", "一百零五"),
    (3, "ideographTraditional", "丙"), (2, "decimalEnclosedCircle", "②"), (4, "ordinalText", None),
])
def test_number_formats(value, fmt, written):
    assert format_value(value, fmt) == written


FOOTNOTES = (f'<w:footnotes {NS}>'
             '<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
             + "".join(f'<w:footnote w:id="{i}">{p(r(text))}</w:footnote>'
                       for i, text in ((1, "Note one"), (2, "Note two"), (3, "Orphan note")))
             + '</w:footnotes>')


def ref(i: int) -> str:
    return f'<w:r><w:footnoteReference w:id="{i}"/></w:r>'


@pytest.fixture(scope="module")
def sectioned(tmp_path_factory) -> Path:
    from docx.opc.constants import CONTENT_TYPE as CT, RELATIONSHIP_TYPE as RT
    from docx.opc.packuri import PackURI
    from docx.opc.part import Part as OpcPart
    document = docx.Document()
    body = document.element.body
    for child in list(body):
        if child.tag != f"{W}sectPr":
            body.remove(child)
    numbering = document.part.numbering_part.element
    for child in list(numbering):
        numbering.remove(child)
    for child in etree.fromstring(NUMBERING):
        numbering.append(child)
    sect = body.find(f"{W}sectPr")
    for xml in (p(r("One") + ref(1)), p(r("Item") + ref(2), num=(2, 0)), p(r("Item two"), num=(2, 0)),
                p(r("After list"))):
        sect.addprevious(etree.fromstring(f"<w:body {NS}>{xml}</w:body>")[0])
    document.sections[0].header.paragraphs[0].text = "H1"
    document.sections[0].footer.paragraphs[0].text = "F1"
    second = document.add_section()
    second.header.is_linked_to_previous = False
    second.header.paragraphs[0].text = "H2"
    document.add_paragraph("Two")
    part = OpcPart(PackURI("/word/footnotes.xml"), CT.WML_FOOTNOTES, FOOTNOTES.encode(), document.part.package)
    document.part.relate_to(part, RT.FOOTNOTES)
    path = tmp_path_factory.mktemp("sections") / "sectioned.docx"
    document.save(path)
    return path


def test_reader_keeps_sections_note_references_and_paragraph_facts(sectioned):
    file = read_docx(sectioned)
    assert len(file.sections) == 2
    assert [name for s in file.sections for _, name in s.headers] == ["header1", "header2"]
    assert file.sections[1].footers == ()                    # linked to the previous section
    assert [(n.id, n.blocks[0].text) for n in file.notes] == [("1", "Note one"), ("2", "Note two"),
                                                               ("3", "Orphan note")]
    first = file.body.blocks[0]
    assert first.notes == (("footnotes", "1"),) and first.text == "One"
    assert file.body.blocks[1].numbering == ("2", 0)


def test_headers_footers_go_by_section_and_notes_follow_their_reference(sectioned):
    source = next(s for s in discover(sectioned.parent) if s.file_type == "docx")
    result = DocxNativeParser().parse(source)
    order = []
    for element in result.primary.elements:
        if isinstance(element, PrimaryList):
            order.append("[" + ", ".join(i.text for i in element.items) + "]")
        elif isinstance(element, PrimaryText):
            order.append(element.text)
    assert order == ["H1", "One", "Note one", "[Item, Item two]", "Note two", "After list", "F1",
                     "H2", "Two", "Orphan note"]
    assert result.native["format"] == "docx_reader_v1" and len(result.native["sections"]) == 2


def test_excerpts_are_located_by_address_across_a_label_and_within_cells(sample):
    from rag.eval.anchors import format_word
    from rag.eval.dataset import Excerpt
    from rag.eval.docx_anchors import file_words, locate_excerpt
    words = file_words(read_docx(sample))
    labelled = locate_excerpt(Excerpt("x1", "d", "n", (), "1. Clause one"), words)
    assert labelled.status == "located"
    assert [format_word(w) for w in labelled.words] == ["body/2#label 0 2", "body/2 0 6", "body/2 7 10"]
    cells = locate_excerpt(Excerpt("x2", "d", "n", (), "x | y"), words)
    assert [w.address for w in cells.words] == ["body/15/r1c1/0", "body/15/r1c2/0"]
    across = locate_excerpt(Excerpt("x3", "d", "n", (), "x y"), words)   # two cells are not one text
    assert across.status == "located" and len(across.groups[0][1]) == 2 and \
        {w.address for w in across.words} == {"body/15/r1c1/0", "body/15/r1c2/0"}
    toc = locate_excerpt(Excerpt("x4", "d", "n", (), "Contents 3"), words)
    assert toc.status == "missing"                                       # table-of-contents text is not read
