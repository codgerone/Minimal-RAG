"""OCR review page (0-OCR.html): page classification, OCR boxes over the page image, low-confidence words.

Built from stored results only: the OCR facts (`.rag/ocr/<key>/ocr.json`) and the derived `ocr.pdf`.
Page images are rendered once into `reports/ocr/<key>/` and shared by every config's pages.
"""

from __future__ import annotations

import statistics
from pathlib import Path

import fitz

from rag.ingest.ocr.models import DocumentPages, OcrFacts, PageOcr
from rag.reports.html import badge, esc

LOW_CONFIDENCE = 0.8
IMAGE_WIDTH = 1400        # pixels of the rendered page image
KIND_LABELS = {"text_layer": "文字层（原样使用）", "scanned": "扫描页（OCR）", "blank": "空白页（跳过）"}
TYPE_LABELS = {"digital": "数字 PDF", "scanned": "扫描 PDF", "partly_scanned": "部分扫描"}


def page_images(ocr_pdf: Path, facts: OcrFacts, folder: Path) -> dict[int, str]:
    """Render each scanned page of ocr.pdf once; return page number → file name."""
    names = {}
    with fitz.open(ocr_pdf) as pdf:
        for item in facts.ocr_pages:
            name = f"p{item.page_number:03d}.jpg"
            path = folder / name
            if not path.is_file():
                page = pdf[item.page_number - 1]
                zoom = IMAGE_WIDTH / page.rect.width
                folder.mkdir(parents=True, exist_ok=True)
                page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY).save(path, jpg_quality=80)
            names[item.page_number] = name
    return names


def readback_deviation(ocr_pdf: Path, facts: OcrFacts) -> dict[int, float]:
    """Largest distance (points) between a word read back from ocr.pdf and its OCR box, per scanned page."""
    result = {}
    with fitz.open(ocr_pdf) as pdf:
        for item in facts.ocr_pages:
            read = pdf[item.page_number - 1].get_text("words", sort=False)
            result[item.page_number] = max((max(abs(r[0] - w.box.x0), abs(r[1] - w.box.y0),
                                                abs(r[2] - w.box.x1), abs(r[3] - w.box.y1))
                                            for r, w in zip(read, item.words)), default=0.0)
    return result


def _low(item: PageOcr) -> list:
    return [(line, word) for line in item.lines for word in line.words if word.confidence < LOW_CONFIDENCE]


def _orientation(item: PageOcr) -> str:
    text = f"旋转 {item.orientation}°" if item.orientation else "原方向"
    if item.deskewed:
        text += f"，纠偏 {item.skew_degrees:+.2f}°"
    elif item.skew_degrees:
        text += f"（倾斜 {item.skew_degrees:+.2f}°，未达 0.3°，不纠偏）"
    return text


def _overlay(item: PageOcr, image: str) -> str:
    width, height = item.width_pt, item.height_pt
    rects = "".join(
        f'<rect x="{w.box.x0:.1f}" y="{w.box.y0:.1f}" width="{w.box.x1 - w.box.x0:.1f}" '
        f'height="{w.box.y1 - w.box.y0:.1f}" class="{"lo" if w.confidence < LOW_CONFIDENCE else "hi"}">'
        f'<title>{esc(w.text)} · 置信度 {w.confidence:.3f}</title></rect>'
        for w in item.words)
    return (f'<div class="ocr-page"><img src="{esc(image)}" alt="第 {item.page_number} 页" loading="lazy">'
            f'<svg viewBox="0 0 {width:.1f} {height:.1f}" preserveAspectRatio="none">{rects}</svg></div>')


def _lines(item: PageOcr) -> str:
    rows = []
    for line in item.lines:
        words = " ".join(f'<span class="lo-word" title="置信度 {w.confidence:.3f}">{esc(w.text)}</span>'
                         if w.confidence < LOW_CONFIDENCE else esc(w.text) for w in line.words)
        rows.append(f"<div>{words}</div>")
    return f'<div class="ocr-lines">{"".join(rows) or "（未识别出文字）"}</div>'


def _crop(item: PageOcr, image: str, line, word) -> str:
    """The line around a low-confidence word, cut from the page image by CSS; the word is outlined."""
    scale = IMAGE_WIDTH / item.width_pt
    pad_x, pad_y = 60, 6     # points of context around the line
    x0, y0 = max(0.0, line.box.x0 - pad_x), max(0.0, line.box.y0 - pad_y)
    x1, y1 = min(item.width_pt, line.box.x1 + pad_x), min(item.height_pt, line.box.y1 + pad_y)
    mark = (f'<span class="ocr-mark" style="left:{(word.box.x0 - x0) * scale:.0f}px;top:{(word.box.y0 - y0) * scale:.0f}px;'
            f'width:{(word.box.x1 - word.box.x0) * scale:.0f}px;height:{(word.box.y1 - word.box.y0) * scale:.0f}px"></span>')
    return (f'<div class="scroll"><div class="ocr-crop" style="width:{(x1 - x0) * scale:.0f}px;'
            f'height:{(y1 - y0) * scale:.0f}px;background-image:url(\'{esc(image)}\');'
            f'background-size:{IMAGE_WIDTH}px auto;background-position:-{x0 * scale:.0f}px -{y0 * scale:.0f}px">'
            f'{mark}</div></div>')


def _page_panel(item: PageOcr, image: str, deviation: float) -> str:
    words = item.words
    low = _low(item)
    mean = statistics.fmean(w.confidence for w in words) if words else 0.0
    trials = ""
    if len(item.trials) > 1:
        rows = "".join(f'<tr{" class=winner-row" if t.orientation == item.orientation else ""}><td>{t.orientation}°</td>'
                       f'<td class="num">{t.line_count}</td><td class="num">{t.vertical_lines}</td>'
                       f'<td class="num">{t.valid_chars}</td><td class="num">{t.mean_confidence:.3f}</td>'
                       f'<td class="num">{t.score:.1f}</td></tr>' for t in item.trials)
        trials = ('<details><summary>方向判定：原方向竖向框过半或没有有效字符，四个方向都识别了一次</summary>'
                  '<table><tr><th>旋转</th><th class="num">行数</th><th class="num">竖向框</th>'
                  '<th class="num">有效字符</th><th class="num">平均置信度</th><th class="num">得分</th></tr>'
                  f'{rows}</table></details>')
    low_rows = "".join(
        f'<tr><td class="nw"><b>{esc(w.text)}</b></td><td class="num">{w.confidence:.3f}</td>'
        f'<td>{esc(line.text)}<details><summary>原图局部</summary>{_crop(item, image, line, w)}</details></td></tr>'
        for line, w in sorted(low, key=lambda pair: pair[1].confidence))
    low_table = (f'<details{" open" if 0 < len(low) <= 30 else ""}><summary>置信度低于 {LOW_CONFIDENCE} 的词'
                 f'（{len(low)}），按置信度从低到高</summary><table><tr><th>词</th><th class="num">置信度</th>'
                 f'<th>所在行</th></tr>{low_rows}</table></details>') if low else \
        f'<div class="meta">没有置信度低于 {LOW_CONFIDENCE} 的词。</div>'
    status = badge("低置信度 " + str(len(low)), "bad" if low else "ok")
    return (f'<div data-panel="p{item.page_number}"><h2>第 {item.page_number} 页 {status}</h2>'
            f'<div class="card"><div>{len(item.lines)} 行 · {len(words)} 词 · 平均置信度 {mean:.3f} · '
            f'{esc(_orientation(item))} · 原页面 /Rotate {item.source_rotation} · 渲染 {item.dpi} dpi，'
            f'图像 {item.image_width}×{item.image_height} 像素 · 文字层读回框最大偏差 {deviation:.2f} 点</div>'
            f'{trials}<div class="meta legend"><span class="ocr-hi">置信度 ≥ {LOW_CONFIDENCE}</span>'
            f'<span class="ocr-lo">置信度 &lt; {LOW_CONFIDENCE}</span>鼠标悬停在框上显示识别文字与置信度</div>'
            f'<div class="ocr-cols">{_overlay(item, image)}<div><div class="meta">识别文字（按 OCR 行，红色为低置信度）</div>'
            f'{_lines(item)}</div></div>{low_table}</div></div>')


OCR_CSS = """<style>
.ocr-cols{display:grid;grid-template-columns:minmax(0,3fr) minmax(0,2fr);gap:14px;margin-top:10px}
@media (max-width:900px){.ocr-cols{grid-template-columns:1fr}}
.ocr-page{position:relative;border:1px solid var(--line);align-self:start}
.ocr-page img{display:block;width:100%;height:auto}
.ocr-page svg{position:absolute;inset:0;width:100%;height:100%}
.ocr-page rect{fill:transparent;stroke-width:.6}.ocr-page rect.hi{stroke:#2f6fde;stroke-opacity:.55}
.ocr-page rect.lo{stroke:#c23b32;stroke-width:1.2;fill:rgba(194,59,50,.12)}
.ocr-page rect:hover{fill:rgba(47,111,222,.2)}
.ocr-lines{font:13px/1.6 ui-monospace,Consolas,monospace;background:var(--code);border-radius:6px;
padding:8px 10px;max-height:900px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere}
.lo-word{color:var(--bad);background:var(--bad-bg);font-weight:600}
.legend .ocr-hi{border:1.5px solid #2f6fde}.legend .ocr-lo{border:1.5px solid #c23b32;background:var(--bad-bg)}
.ocr-crop{position:relative;background-repeat:no-repeat;border:1px solid var(--line);margin:4px 0}
.ocr-mark{position:absolute;outline:2px solid var(--bad)}
</style>"""


def ocr_body(pages: DocumentPages, facts: OcrFacts, images: dict[int, str], image_root: str,
             deviations: dict[int, float]) -> str:
    """Summary, page table and one tab per scanned page; `image_root` is the relative path to the images."""
    kinds = [page.kind for page in pages.pages]
    words = [w for item in facts.ocr_pages for w in item.words]
    low = [w for w in words if w.confidence < LOW_CONFIDENCE]
    low_pages = sorted(((len(_low(item)), item.page_number) for item in facts.ocr_pages if _low(item)), reverse=True)
    turned = [item.page_number for item in facts.ocr_pages if item.orientation]
    deskewed = [item.page_number for item in facts.ocr_pages if item.deskewed]
    worst = max(deviations.values(), default=0.0)
    counts = "，".join(f"{kinds.count(kind)} 页{label}" for kind, label in
                      (("text_layer", "有文字层（原样使用）"), ("scanned", "扫描（已 OCR）"), ("blank", "空白（跳过）"))
                      if kinds.count(kind))
    share = len(low) / len(words) if words else 0.0
    top = "、".join(f"第 {n} 页 {c} 词" for c, n in low_pages[:3])
    summary = (f'<div class="card"><b>{TYPE_LABELS[pages.document_type]}</b>：{len(kinds)} 页，{counts}。'
               f'<div>OCR 共 {len(words)} 词，置信度低于 {LOW_CONFIDENCE} 的 {len(low)} 词（{share:.1%}）'
               + (f'，最多的是 {top}' if top else '') + '。</div>'
               f'<div>方向：{"第 " + "、".join(map(str, turned)) + " 页旋转后识别" if turned else "全部按原方向识别"}；'
               f'纠偏：{"第 " + "、".join(map(str, deskewed)) + " 页" if deskewed else "没有页面倾斜达到 0.3°"}。</div>'
               f'<div>{badge("自检通过", "ok")} 文字层 PDF 读回的词序列与 OCR 逐词相同，词框最大偏差 {worst:.2f} 点（上限 1 点）。</div>'
               f'<div class="meta">OCR 只认字；标题、段落、表格与阅读顺序由解析器从文字层 PDF 恢复，见“解析”“表格”页。'
               f'OCR 事实保存在 .rag/ocr/{esc(pages.ocr_key or "")}/ocr.json，规则见 docs/rules/ocr.md</div></div>')
    by_page = {item.page_number: item for item in facts.ocr_pages}
    rows = []
    for page in pages.pages:
        item = by_page.get(page.page_number)
        if item is None:
            rows.append(f'<tr><td class="num">{page.page_number}</td><td>{KIND_LABELS[page.kind]}</td>'
                        f'<td class="num">{page.rotation}</td><td colspan="4" class="meta">不 OCR</td></tr>')
            continue
        n_low = len(_low(item))
        rows.append(f'<tr><td class="num"><a href="#p{page.page_number}">{page.page_number}</a></td>'
                    f'<td>{KIND_LABELS[page.kind]}</td><td class="num">{page.rotation}</td>'
                    f'<td>{esc(_orientation(item))}</td><td class="num">{len(item.words)}</td>'
                    f'<td class="num">{statistics.fmean(w.confidence for w in item.words) if item.words else 0:.3f}</td>'
                    f'<td class="num">{badge(str(n_low), "bad" if n_low else "ok")}</td></tr>')
    table = ('<details open><summary>逐页判定</summary><table><tr><th class="num">页</th><th>判定</th>'
             '<th class="num">/Rotate</th><th>方向与纠偏</th><th class="num">词数</th><th class="num">平均置信度</th>'
             f'<th class="num">低置信度词</th></tr>{"".join(rows)}</table></details>')
    tabs = "".join(f'<a href="#p{item.page_number}" data-tab="p{item.page_number}">第 {item.page_number} 页'
                   f'{"（" + str(len(_low(item))) + "）" if _low(item) else ""}</a>' for item in facts.ocr_pages)
    panels = "".join(_page_panel(item, f"{image_root}{images[item.page_number]}", deviations[item.page_number])
                     for item in facts.ocr_pages)
    return OCR_CSS + summary + table + f'<div class="tabs">{tabs}</div>' + panels
