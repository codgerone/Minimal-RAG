"""Review files for excerpt locations: an HTML page with one highlighted page crop per excerpt,
plus a copy of each PDF with every excerpt highlighted (the note on each highlight names it)."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pymupdf

from rag.eval.anchors import ExcerptAnchor
from rag.jsonio import write_atomic
from rag.paths import readable_name
from rag.reports.html import badge, esc, filters, page, text_block, write_assets

YELLOW = (1.0, 0.86, 0.0)
RED = (0.85, 0.15, 0.1)
MARGIN = 70.0
DPI = 120
STATUS = {"missing": ("有片段找不到", "bad"), "ambiguous": ("片段多处出现，按位置选定", "warn"),
          "located": ("唯一定位", "ok")}


def _crop(pdf_path: Path, item: ExcerptAnchor, case_id: str, folder: Path) -> list[str]:
    """One PNG per page of the excerpt: yellow = located words, red frame = piece chosen among several."""
    names: list[str] = []
    ambiguous = {w for p in item.pieces if p.candidates > 1 for w in p.words}
    with pymupdf.open(pdf_path) as pdf:
        for number in item.excerpt.pages:
            page_ = pdf[number - 1]
            boxes = [w for w in item.words if w.page == number]
            for w in boxes:
                page_.draw_rect(pymupdf.Rect(w.bbox), color=None, fill=YELLOW, fill_opacity=0.45, overlay=True)
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


def write_anchor_review(folder: Path, items: list[tuple[str, ExcerptAnchor]], pdf_paths: dict[str, Path],
                        questions: dict[str, str], previous: dict[tuple[str, str], str | None]) -> Path:
    """`previous` maps (case_id, excerpt_id) → text before this round's edit (None = new excerpt)."""
    (folder / "img").mkdir(parents=True, exist_ok=True)
    (folder / "pdf").mkdir(parents=True, exist_ok=True)
    write_assets(folder)
    pdf_names = _highlighted_pdfs(items, pdf_paths, folder / "pdf")
    rank = {"missing": 0, "ambiguous": 1, "located": 2}
    ordered = sorted(items, key=lambda ci: rank[ci[1].status])
    counts = {s: sum(1 for _, i in items if i.status == s) for s in rank}
    edited = sum(1 for c, i in items if (c, i.excerpt.excerpt_id) in previous)
    headers = sum(1 for _, i in items if i.excerpt.table_header)

    cards = []
    for case_id, item in ordered:
        x = item.excerpt
        key = (case_id, x.excerpt_id)
        label, kind = STATUS[item.status]
        tags = ["all", item.status] + (["edited"] if key in previous else []) + (["header"] if x.table_header else [])
        head = (f'<h3>{esc(case_id)} · {esc(x.excerpt_id)} {badge(label, kind)}'
                f'{" " + badge("表头", "warn") if x.table_header else ""}'
                f'{" " + badge("本次修改") if key in previous else ""}</h3>'
                f'<div class="meta">{esc(x.document_name)} · 第 {"、".join(map(str, x.pages))} 页 · '
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
        problems = [p for p in item.pieces if p.candidates != 1 or p.missing]
        rows = "".join(
            f'<tr><td>{esc(p.text)}</td><td class="num">{p.candidates}</td>'
            f'<td>{esc("、".join(p.missing)) or "—"}</td></tr>' for p in problems)
        detail = (f'<details><summary>多处出现或找不到的片段（{len(problems)}）</summary>'
                  f'<table><tr><th>片段</th><th class="num">出现次数</th><th>找不到的词</th></tr>{rows}</table>'
                  f'</details>' if problems else "")
        images = "".join(f'<div class="meta">第 {name.rsplit("_p", 1)[1][:-4]} 页</div>'
                         f'<img src="img/{esc(name)}" style="max-width:100%;border:1px solid var(--line)">'
                         for name in _crop(pdf_paths[x.document_id], item, case_id, folder / "img"))
        cards.append(f'<div class="card" data-tags="{" ".join(tags)}">{head}{text}{detail}{images}</div>')

    links = " · ".join(f'<a href="pdf/{esc(n)}">{esc(n)}</a>' for n in pdf_names.values())
    body = (
        "<h1>excerpt 定位审核</h1>"
        f'<div class="sub">共 {len(items)} 段 excerpt。黄色是定位到的词；红框表示该片段在页面上出现多处，'
        "已按与其他片段最接近的位置选定，请重点核对。</div>"
        '<div class="tiles">'
        f'<div class="tile"><div class="v">{counts["missing"]}</div><div class="k">有片段找不到</div></div>'
        f'<div class="tile"><div class="v">{counts["ambiguous"]}</div><div class="k">多处出现、按位置选定</div></div>'
        f'<div class="tile"><div class="v">{counts["located"]}</div><div class="k">唯一定位</div></div>'
        f'<div class="tile"><div class="v">{edited}</div><div class="k">本次修改的 excerpt</div></div></div>'
        f'<div class="card"><b>整份 PDF 高亮副本</b>（每个高亮的批注写明题号和 excerpt 号）：{links}</div>'
        + filters([("all", "全部", len(items)), ("missing", "有片段找不到", counts["missing"]),
                   ("ambiguous", "多处出现", counts["ambiguous"]), ("edited", "本次修改", edited),
                   ("header", "表头 excerpt", headers)])
        + "".join(cards))
    path = folder / "index.html"
    write_atomic(path, page("excerpt 定位审核", body, root=""))
    return path
