"""Review files for excerpt locations: an HTML page with one highlighted page crop per excerpt,
plus a copy of each PDF with every excerpt highlighted (the note on each highlight names it)."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pymupdf

from rag.eval.anchors import ExcerptAnchor, read_text
from rag.ingest.docx.reader import Block, DocxFile, Paragraph
from rag.jsonio import write_atomic
from rag.paths import readable_name
from rag.reports.html import badge, esc, filters, page, text_block, write_assets

YELLOW = (1.0, 0.86, 0.0)
BLUE = (0.2, 0.55, 1.0)    # words of a piece matched approximately on a scanned page
RED = (0.85, 0.15, 0.1)
MARGIN = 70.0
DPI = 120
STATUS = {"missing": ("有片段找不到", "bad"), "ambiguous": ("片段多处出现，按位置选定", "warn"),
          "located": ("唯一定位", "ok")}


def _crop(pdf_path: Path, item: ExcerptAnchor, case_id: str, folder: Path) -> list[str]:
    """One PNG per page of the excerpt: yellow = located words, blue = matched approximately on a
    scanned page, red frame = piece chosen among several."""
    names: list[str] = []
    ambiguous = {w for p in item.pieces if p.candidates > 1 for w in p.words}
    approximate = {w for p in item.pieces if p.distance is not None for w in p.words}
    with pymupdf.open(pdf_path) as pdf:
        for number in item.excerpt.pages:
            page_ = pdf[number - 1]
            boxes = [w for w in item.words if w.page == number]
            for w in boxes:
                fill = BLUE if w in approximate else YELLOW
                page_.draw_rect(pymupdf.Rect(w.bbox), color=None, fill=fill, fill_opacity=0.45, overlay=True)
                if w in ambiguous:
                    page_.draw_rect(pymupdf.Rect(w.bbox) + (-1.5, -1.5, 1.5, 1.5), color=RED, width=1.2)
            rect = page_.rect
            if boxes:
                top = max(rect.y0, min(w.bbox[1] for w in boxes) - MARGIN)
                bottom = min(rect.y1, max(w.bbox[3] for w in boxes) + MARGIN)
                rect = pymupdf.Rect(rect.x0, top, rect.x1, bottom)
            name = f"{case_id}_{item.excerpt.excerpt_id}_p{number}.png"
            page_.get_pixmap(dpi=DPI, clip=rect).save(folder / name)
            names.append(name)
    return names


def _highlighted_pdfs(items: list[tuple[str, ExcerptAnchor]], pdf_paths: dict[str, Path], folder: Path) -> dict[str, str]:
    by_doc: dict[str, list[tuple[str, ExcerptAnchor]]] = defaultdict(list)
    for case_id, item in items:
        by_doc[item.excerpt.document_id].append((case_id, item))
    names: dict[str, str] = {}
    for doc, entries in by_doc.items():
        with pymupdf.open(pdf_paths[doc]) as pdf:
            for case_id, item in entries:
                per_page: dict[int, list[pymupdf.Rect]] = defaultdict(list)
                for w in item.words:
                    per_page[w.page].append(pymupdf.Rect(w.bbox))
                for number, rects in per_page.items():
                    pdf_page = pdf[number - 1]   # keep the page alive while editing its annotation
                    annot = pdf_page.add_highlight_annot(rects)
                    annot.set_colors(stroke=YELLOW)
                    annot.set_info(title="excerpt", content=f"{case_id} / {item.excerpt.excerpt_id}")
                    annot.update()
            name = f"{readable_name(pdf_paths[doc].name)}.pdf"
            pdf.save(folder / name)
            names[doc] = name
    return names


# --- Word -------------------------------------------------------------------------------------

WORD_CSS = ("<style>.docx p{margin:.25em 0;white-space:pre-wrap}.docx table{border-collapse:collapse;margin:.4em 0}"
            ".docx td{border:1px solid var(--line);padding:2px 6px;vertical-align:top}"
            ".docx .addr{color:var(--muted);font-size:11px;margin-right:6px}"
            ".docx mark{background:#ffdb00aa}.docx mark.amb{outline:1.5px solid #d9261a}"
            ".ctx{max-height:480px;overflow:auto;border:1px solid var(--line);padding:6px;border-radius:6px}</style>")

Marks = dict[str, list[tuple[int, int, str, str]]]   # address → (start, end, css class, title)


def _marks(items: list[tuple[str, ExcerptAnchor]]) -> Marks:
    marks: Marks = defaultdict(list)
    for case_id, item in items:
        ambiguous = {w for p in item.pieces if p.candidates > 1 for w in p.words}
        for w in item.words:
            marks[w.address].append((w.start, w.end, "amb" if w in ambiguous else "",
                                     f"{case_id} / {item.excerpt.excerpt_id}"))
    return marks


def _marked(text: str, ranges: list[tuple[int, int, str, str]]) -> str:
    out, at = [], 0
    for start, end, cls, title in sorted(ranges):
        if start < at:
            continue
        out.append(esc(text[at:start]))
        out.append(f'<mark class="{cls}" title="{esc(title)}">{esc(text[start:end])}</mark>')
        at = end
    return "".join(out) + esc(text[at:])


def _render(block: Block, marks: Marks, show_address: bool) -> str:
    if isinstance(block, Paragraph):
        if block.toc:
            return ""
        label = (f'{_marked(block.label, marks.get(block.address + "#label", []))} ' if block.label
                 else f"{esc(block.bullet)} " if block.bullet else "")
        address = f'<span class="addr">{esc(block.address)}</span>' if show_address else ""
        line = (f"<p>{address}{label}{_marked(block.text, marks.get(block.address, []))}</p>"
                if block.text.strip() or label else "")
        return line + "".join(_render(inner, marks, show_address) for inner in block.textbox)
    rows = []
    for r in range(block.rows):
        cells = sorted((c for c in block.cells if c.row == r), key=lambda c: c.column)
        rows.append("<tr>" + "".join(
            f'<td rowspan="{c.row_span}" colspan="{c.col_span}">'
            + "".join(_render(inner, marks, False) for inner in c.blocks) + "</td>" for c in cells) + "</tr>")
    address = f'<div class="addr">{esc(block.address)}</div>' if show_address else ""
    return f"{address}<table>{''.join(rows)}</table>"


def _top_block(address: str) -> tuple[str, int]:
    part, _, rest = address.split("#", 1)[0].partition("/")
    return part, int(rest.split("/", 1)[0])


def _word_context(file: DocxFile, item: ExcerptAnchor, case_id: str) -> str:
    """The top-level paragraphs and tables holding the located words, highlighted."""
    parts = {part.name: part for part in file.parts}
    order = list(parts)
    keys = sorted({_top_block(w.address) for w in item.words}, key=lambda k: (order.index(k[0]), k[1]))
    marks = _marks([(case_id, item)])
    html = "".join(_render(parts[name].blocks[index], marks, True) for name, index in keys)
    return f'<div class="ctx docx">{html or "（没有定位到的词）"}</div>'


def _word_pages(items: list[tuple[str, ExcerptAnchor]], files: dict[str, DocxFile], names: dict[str, str],
                folder: Path) -> dict[str, str]:
    """One page per Word document: all its blocks part by part, every excerpt highlighted."""
    pages: dict[str, str] = {}
    for doc, file in files.items():
        marks = _marks([(c, i) for c, i in items if i.excerpt.document_id == doc])
        body = "".join(f"<h2>{esc(part.name)}</h2>" + "".join(_render(b, marks, True) for b in part.blocks)
                       for part in file.parts)
        name = f"{readable_name(names[doc])}.html"
        write_atomic(folder / name, page(names[doc], WORD_CSS + f'<h1>{esc(names[doc])}</h1>'
                                         '<div class="sub">黄色是定位到的词，红框是多处出现时选定的一处；'
                                         '鼠标停在高亮上显示题号和 excerpt 号。灰色是地址。</div>'
                                         f'<div class="card docx">{body}</div>', root="../"))
        pages[doc] = name
    return pages


def write_anchor_review(folder: Path, items: list[tuple[str, ExcerptAnchor]], pdf_paths: dict[str, Path],
                        questions: dict[str, str], previous: dict[tuple[str, str], str | None],
                        word_files: dict[str, DocxFile] | None = None) -> Path:
    """`previous` maps (case_id, excerpt_id) → text before this round's edit (None = new excerpt).
    `pdf_paths` holds the PDF documents; `word_files` the Word documents, read."""
    word_files = word_files or {}
    for name in ("img", "pdf", "word"):
        (folder / name).mkdir(parents=True, exist_ok=True)
    write_assets(folder)
    pdf_names = _highlighted_pdfs([(c, i) for c, i in items if i.excerpt.document_id in pdf_paths],
                                  pdf_paths, folder / "pdf")
    word_names = _word_pages(items, word_files, {i.excerpt.document_id: i.excerpt.document_name
                                                 for _, i in items}, folder / "word")
    rank = {"missing": 0, "ambiguous": 1, "located": 2}
    ordered = sorted(items, key=lambda ci: (rank[ci[1].status], not ci[1].approximate))
    counts = {s: sum(1 for _, i in items if i.status == s) for s in rank}
    edited = sum(1 for c, i in items if (c, i.excerpt.excerpt_id) in previous)
    headers = sum(1 for _, i in items if i.excerpt.table_header)
    approximate = sum(1 for _, i in items if i.approximate)

    cards = []
    for case_id, item in ordered:
        x = item.excerpt
        key = (case_id, x.excerpt_id)
        label, kind = STATUS[item.status]
        where = ("、".join(item.locations) if x.document_id in word_files
                 else "第 " + "、".join(map(str, x.pages)) + " 页")
        tags = (["all", item.status] + (["edited"] if key in previous else []) + (["header"] if x.table_header else [])
                + (["approximate"] if item.approximate else []) + (["word"] if x.document_id in word_files else []))
        head = (f'<h3>{esc(case_id)} · {esc(x.excerpt_id)} {badge(label, kind)}'
                f'{" " + badge("表头", "warn") if x.table_header else ""}'
                f'{" " + badge("本次修改") if key in previous else ""}'
                f'{" " + badge("近似匹配", "warn") if item.approximate else ""}</h3>'
                f'<div class="meta">{esc(x.document_name)} · {esc(where)} · '
                f'问题：{esc(questions[case_id])}</div>'
                + (f'<div class="meta">定位提示（只用于在 PDF 上选列，不属于证据）：{esc(x.locate_hint)}</div>'
                   if x.locate_hint else ""))
        if key in previous:
            before = previous[key]
            text = (f'<div class="cols"><div><div class="meta">修改前</div>'
                    f'{text_block(before) if before is not None else "<div class=text>（新增）</div>"}</div>'
                    f'<div><div class="meta">修改后</div>{text_block(x.text)}</div></div>')
        else:
            text = text_block(x.text, 400)
        near = [p for p in item.pieces if p.distance is not None]
        compare = (f'<table><tr><th>片段原文（纸面）</th><th>OCR 读出的文字</th><th class="num">编辑距离</th></tr>'
                   + "".join(f'<tr><td>{esc(p.text)}</td><td>{esc(read_text(p.words))}</td>'
                             f'<td class="num">{p.distance}</td></tr>' for p in near)
                   + '</table>' if near else "")
        problems = [p for p in item.pieces if p.candidates != 1 or p.missing]
        rows = "".join(
            f'<tr><td>{esc(p.text)}</td><td class="num">{p.candidates}</td>'
            f'<td>{esc("、".join(p.missing)) or "—"}</td></tr>' for p in problems)
        detail = (f'<details><summary>多处出现或找不到的片段（{len(problems)}）</summary>'
                  f'<table><tr><th>片段</th><th class="num">出现次数</th><th>找不到的词</th></tr>{rows}</table>'
                  f'</details>' if problems else "")
        if x.document_id in word_files:
            images = _word_context(word_files[x.document_id], item, case_id)
        else:
            images = "".join(f'<div class="meta">第 {name.rsplit("_p", 1)[1][:-4]} 页</div>'
                             f'<img src="img/{esc(name)}" style="max-width:100%;border:1px solid var(--line)">'
                             for name in _crop(pdf_paths[x.document_id], item, case_id, folder / "img"))
        cards.append(f'<div class="card" data-tags="{" ".join(tags)}">{head}{text}{compare}{detail}{images}</div>')

    links = " · ".join([f'<a href="pdf/{esc(n)}">{esc(n)}</a>' for n in pdf_names.values()]
                       + [f'<a href="word/{esc(n)}">{esc(n)}</a>' for n in word_names.values()])
    body = (
        "<h1>excerpt 定位审核</h1>"
        f'<div class="sub">共 {len(items)} 段 excerpt。黄色是定位到的词；蓝色是扫描页上近似匹配的片段'
        "（纸面文字与 OCR 读出的不同，卡片中并排列出），请核对位置是否正确；红框表示该片段在页面上出现多处，"
        "已按与其他片段最接近的位置选定，请重点核对。</div>"
        '<div class="tiles">'
        f'<div class="tile"><div class="v">{counts["missing"]}</div><div class="k">有片段找不到</div></div>'
        f'<div class="tile"><div class="v">{counts["ambiguous"]}</div><div class="k">多处出现、按位置选定</div></div>'
        f'<div class="tile"><div class="v">{counts["located"]}</div><div class="k">唯一定位</div></div>'
        f'<div class="tile"><div class="v">{approximate}</div><div class="k">含近似匹配</div></div>'
        f'<div class="tile"><div class="v">{edited}</div><div class="k">本次修改的 excerpt</div></div></div>'
        f'<div class="card"><b>整份文档高亮副本</b>（PDF 每个高亮的批注、Word 页面每个高亮的提示写明题号和'
        f' excerpt 号；Word 文档请打开原文件对照）：{links}</div>'
        + filters([("all", "全部", len(items)), ("missing", "有片段找不到", counts["missing"]),
                   ("ambiguous", "多处出现", counts["ambiguous"]), ("approximate", "近似匹配", approximate),
                   ("edited", "本次修改", edited),
                   ("word", "Word 文档", sum(1 for _, i in items if i.excerpt.document_id in word_files)),
                   ("header", "表头 excerpt", headers)])
        + "".join(cards))
    path = folder / "index.html"
    write_atomic(path, page("excerpt 定位审核", WORD_CSS + body, root=""))
    return path
