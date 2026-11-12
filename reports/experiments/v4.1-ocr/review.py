"""Review page for the V4.1 OCR route experiment (measure.py): conclusion by the decision rule of
docs/changes/v4.1.md, then OCR quality, tables and retrieval per route.

Reads measures.json; writes index.html. Run: python reports/experiments/v4.1-ocr/review.py
"""

from __future__ import annotations

import json
from pathlib import Path

from rag.reports.html import esc, page, write_assets

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
M = json.loads((HERE / "measures.json").read_text(encoding="utf-8"))
ROUTES = ["E0", "E1", "E2", "E3", "E3d"]
LABELS = {
    "E0": "数字原件 · structured",
    "E1": "模拟扫描 → OCR 事实 → 文字层 PDF · structured",
    "E2": "同 E1 · 不配表格提取器",
    "E3": "Docling 解析器内 OCR（引擎设置同 E1）· 不配表格提取器",
    "E3d": "Docling 解析器内 OCR（默认设置）· 不配表格提取器",
}
COMPARE = "../../compare/v4.1-ocr/index.html"

CSS = """
.num{text-align:right;font-variant-numeric:tabular-nums}
.good{background:#e9f7ee}.bad{background:#fdecea}
td.t{font-family:Consolas,monospace;font-size:12px;word-break:break-all;max-width:420px}
"""


def pct(x: float | None) -> str:
    return "—" if x is None else f"{x:.3f}"


def metric(route: str, name: str) -> dict:
    return M["retrieval"][route]["metrics"][name]


def structure_counts() -> dict[str, int]:
    counts = {r: 0 for r in ROUTES[1:]}
    for doc in M["tables"].values():
        for table in doc["tables"]:
            for r, v in table["routes"].items():
                counts[r] += bool(v.get("same_structure"))
    return counts


def total_tables() -> int:
    return sum(len(d["tables"]) for d in M["tables"].values())


def conclusion() -> str:
    s = structure_counts()
    c = {r: metric(r, "complete_coverage")["numerator"] for r in ROUTES}
    e3_more = c["E3"] - c["E1"]
    rule1 = e3_more >= 2
    rule2 = s["E3"] - s["E1"] >= 2 and c["E3"] >= c["E1"]
    verdict = "改为解析器内 OCR" if rule1 or rule2 else "采用 E1（OCR 事实 + 派生文字层 PDF）"
    return (f'<div class="card"><p><b>按判定规则：{esc(verdict)}。</b></p><ul>'
            f'<li>完整覆盖：E3 {c["E3"]} 题，E1 {c["E1"]} 题，E3 多 {e3_more} 题（规则要求多 ≥ 2）。</li>'
            f'<li>结构相同的表格（以 E0 的 {total_tables()} 张表为准）：E3 {s["E3"]} 张，E1 {s["E1"]} 张'
            f'（规则要求 E3 多 ≥ 2 且完整覆盖不少于 E1）。</li>'
            f'<li>文字路径（E3 对 E2）：完整覆盖 {c["E3"]} / {c["E2"]}，结构相同的表格 {s["E3"]} / {s["E2"]}；'
            f'多工具选优（E1 对 E2）：结构相同的表格 {s["E1"]} / {s["E2"]}。</li>'
            f'<li>OCR 本身的损失（E1 对 E0）：完整覆盖 {c["E1"]} / {c["E0"]}，见下方"观察"。</li></ul></div>')


def retrieval_table() -> str:
    s = structure_counts()
    head = ('<tr><th>编号</th><th>路线</th><th>命中</th><th>完整覆盖</th><th>证据组召回</th><th>MRR</th>'
            '<th>Chunk 精确率</th><th>每题返回 token</th><th>结构相同的表格</th><th>文字兜底</th></tr>')
    rows = []
    for r in ROUTES:
        v = M["retrieval"][r]
        m = v["metrics"]
        rows.append(
            f'<tr><td>{r}</td><td>{esc(LABELS[r])}</td>'
            f'<td class="num">{m["hit_rate"]["numerator"]}/32</td>'
            f'<td class="num">{m["complete_coverage"]["numerator"]}/32</td>'
            f'<td class="num">{m["group_recall"]["value"]:.1%}</td><td class="num">{m["mrr"]["value"]:.1%}</td>'
            f'<td class="num">{m["chunk_precision"]["value"]:.1%}</td>'
            f'<td class="num">{v["returned_tokens"]["value"]:.0f}</td>'
            f'<td class="num">{"—" if r == "E0" else f"{s[r]}/{total_tables()}"}</td>'
            f'<td class="num">{v["evidence_modes"]["fallback"]}</td></tr>')
    return f'<table>{head}{"".join(rows)}</table>'


def changed_cases() -> str:
    cases = M["retrieval"]["E0"]["cases"]

    def mark(r: str, case: str) -> str:
        c = M["retrieval"][r]["cases"][case]
        label = "完整覆盖" if c["complete"] else ("覆盖部分证据组" if c["hit"] else "没有覆盖任何证据组")
        rank = f'，首个相关结果第 {c["rank"]} 名' if c["rank"] else "，前 3 名无相关结果"
        return f'<td class="{"good" if c["complete"] else "bad"}">{label}{rank}</td>'

    rows = [f'<tr><td>{esc(case)}</td>{"".join(mark(r, case) for r in ROUTES)}</tr>'
            for case in cases
            if len({M["retrieval"][r]["cases"][case]["complete"] for r in ROUTES}) > 1
            or len({M["retrieval"][r]["cases"][case]["hit"] for r in ROUTES}) > 1]
    head = "<tr><th>题目</th>" + "".join(f"<th>{r}</th>" for r in ROUTES) + "</tr>"
    return (f'<p>结果在各路线间有变化的题目（其余题目各路线相同）。"相关结果"按评估规则指属于某个可接受组合的 chunk，'
            f'可能排在前面却没有覆盖完整的证据组。逐题明细见<a href="{COMPARE}">对比页</a>。</p>'
            f'<table>{head}{"".join(rows)}</table>')


def ocr_table() -> str:
    head = ('<tr><th>文档</th>' + "".join(f'<th>{r} 召回</th><th>{r} 精确率</th><th>{r} 含数字词召回</th>'
                                         for r in ("E1", "E3", "E3d")) +
            '<th>E3 与 E1 逐词相同（占 E1 / 占 E3）</th></tr>')
    rows = []
    for doc, d in M["ocr"].items():
        cells = ""
        for r in ("E1", "E3", "E3d"):
            t = d[r]["total"]
            cells += (f'<td class="num">{pct(t["matched"] / t["reference"])}</td>'
                      f'<td class="num">{pct(t["matched"] / t["recognised"])}</td>'
                      f'<td class="num">{pct(t["digit_matched"] / t["digit_reference"]) if t["digit_reference"] else "—"}</td>')
        c = d["consistency"]
        matched = sum(v["matched"] for v in c.values())
        e1 = sum(v["e1"] for v in c.values())
        e3 = sum(v["e3"] for v in c.values())
        rows.append(f'<tr><td>{esc(doc[:40])}</td>{cells}<td class="num">{pct(matched / e1)} / {pct(matched / e3)}</td></tr>')
    return f'<div class="wide"><table>{head}{"".join(rows)}</table></div>'


def consistency_details() -> str:
    out = []
    for doc, d in M["ocr"].items():
        rows = []
        for n, v in d["consistency"].items():
            if v["only_e1"] or v["only_e3"]:
                rows.append(f'<tr><td class="num">{n}</td><td class="num">{v["matched"]}</td>'
                            f'<td class="t">{esc(" · ".join(v["only_e1"]))}</td>'
                            f'<td class="t">{esc(" · ".join(v["only_e3"]))}</td></tr>')
        if rows:
            out.append(f'<details><summary>{esc(doc)}</summary><table><tr><th>页</th><th>相同的词</th>'
                       f'<th>只在 E1 中的词（前 40 个）</th><th>只在 E3 中的词（前 40 个）</th></tr>'
                       f'{"".join(rows)}</table></details>')
    return "".join(out)


def tables_section() -> str:
    out = []
    head = ("<tr><th>E0 的表</th>" + "".join(f"<th>{r}</th>" for r in ROUTES[1:]) + "</tr>")
    for doc, d in M["tables"].items():
        rows, details = [], []
        for t in d["tables"]:
            cells = []
            for r in ROUTES[1:]:
                v = t["routes"][r]
                if v.get("matched") is False:
                    cells.append(f'<td class="bad">没有对应的表（最大 IoU {v["iou"]:.2f}）</td>')
                    continue
                same = v["same_structure"]
                text = (f'{"结构相同" if same else "结构不同"} · {v["rows"]}×{v["columns"]} · {esc(v["tool"])}'
                        f'<br>字符错误率 {pct(v["cer"])}（{"逐格" if v["cer_basis"] == "cells" else "整表文字"}）')
                if not same:
                    text += f'<br>合并布局差异：E0 独有 {v["layout_only_e0"]}，本路线独有 {v["layout_only_route"]}'
                cells.append(f'<td class="{"good" if same else "bad"}">{text}</td>')
                if v.get("cell_diffs"):
                    diff_rows = "".join(f'<tr><td>{c["cell"][0]},{c["cell"][1]}</td><td class="t">{esc(c["original"])}</td>'
                                        f'<td class="t">{esc(c["recognised"])}</td></tr>' for c in v["cell_diffs"])
                    details.append(f'<details><summary>{r} · 第 {t["page"]} 页 {t["rows"]}×{t["columns"]}：'
                                   f'{v["cell_diff_count"]} 格文字不同</summary><table><tr><th>行,列</th>'
                                   f'<th>数字原件</th><th>识别</th></tr>{diff_rows}</table></details>')
            pages = "、".join(str(p) for p in t["pages"])
            rows.append(f'<tr><td>第 {pages} 页 · {t["rows"]}×{t["columns"]} · {esc(t["tool"])}</td>{"".join(cells)}</tr>')
        counts = "，".join(f"{r} {n} 张" for r, n in d["counts"].items())
        out.append(f'<h3>{esc(doc)}</h3><p>采用的表格数：E0 {d["e0_count"]} 张，{counts}。</p>'
                   f'<div class="wide"><table>{head}{"".join(rows)}</table></div>{"".join(details)}')
    return "".join(out)


OBSERVATIONS = """
<ul>
<li><b>E1 比 E0 多完整覆盖 2 题</b>（CONTRACT-105-Q001、Q011，都在 Contrato 105）。这份文档 E0 切成 22 块，E1 切成 24 块：
数字原件中只以图片存在的文字（信头、页脚地址、电子签章说明）在模拟扫描件中被 OCR 认出，
token 数增加，分块分界随之移动。两题的变化是否来自分界移动，未做对照实验。</li>
<li><b>E3d 与 E3 用的是同样的三个模型文件</b>（Docling 按默认语言选出的正是 PP-OCRv6 small 与 ch_ppocr_mobile_v2.0），
差别只在：方向分类器只开一遍、渲染比例 3（216 dpi）、不强制整页 OCR。Contrato 105 的词召回 E3d 0.882、E3 0.985；
三个差别各自的影响未分开测。</li>
<li><b>多工具选优的作用</b>：Contrato 105 第 5 页（6×10）、PO 24-12、PO 25-15 第 2 页，E1 由多工具选优选中与 E0 相同的结构，
E2、E3、E3d（全部采用 Docling 原生表格）结构不同；PO 25-19 第 2 页的跨页表四条路线都与 E0 不同（E1 整表字符错误率 0.009，其余约 0.27）；
PO 25-19 中 E2、E3、E3d 都比 E0 多 1 张表。</li>
<li><b>OCR 的表格单元格错误</b>集中在只有一个短横（"-"）的格子：OCR 没有认出这类格子（PO 25-15 第 2 页 10 格）。</li>
</ul>
"""


def main() -> None:
    body = (f'<style>{CSS}</style><h1>V4.1 OCR 接入方式：E0–E3d 对照实验</h1>'
            '<p>模拟扫描件：现有 5 份 PDF 每页按 300 dpi 渲染为灰度 JPEG（质量 75）。34 题（32 道可回答），K = 3，返回整表，过滤开启；'
            '语料只有这 5 份文档。实验设计见 docs/changes/v4.1.md "对照实验"。</p>'
            f'{conclusion()}'
            f'<h2>检索</h2>{retrieval_table()}{changed_cases()}'
            f'<h2>观察</h2>{OBSERVATIONS}'
            '<h2>OCR 质量（对照数字原件的文字层）</h2>'
            '<p>词按空白切分、不区分大小写，按多重集合计数；召回 = 相同的词 / 数字原件的词，精确率 = 相同的词 / 认出的词。'
            'E1 取 OCR 事实，E3、E3d 取 Docling 从 RapidOCR 收到的文字行。</p>'
            f'{ocr_table()}<h3>认字一致性：E3 与 E1 的逐页差异</h3>{consistency_details()}'
            '<h2>表格（以 E0 采用的表格为准）</h2>'
            '<p>同一页上框的 IoU 最大且 ≥ 0.5 的表格视为对应。结构相同 = 行数、列数与合并布局都相同。'
            '字符错误率 = 编辑距离之和 / 原文字符数之和：结构相同时逐格对齐，结构不同时比较整表按行排列的文字。</p>'
            f'{tables_section()}')
    write_assets(ROOT / "reports")
    (HERE / "index.html").write_text(page("V4.1 OCR 接入方式", body, root="../../",
                                          crumbs=[("报告首页", "../../index.html")]), encoding="utf-8")


if __name__ == "__main__":
    main()
