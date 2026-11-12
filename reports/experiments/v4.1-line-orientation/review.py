"""Review page for the line-orientation experiment (run.py): OCR time per document, every box the line
classifier turned 180°, both readings with their confidences, which one is right, and whether the
higher-confidence reading is the right one.

Reads results.json and manual.json (judgements made by looking at the crops); writes summary.json and
index.html. Run: python reports/experiments/v4.1-line-orientation/review.py
"""

from __future__ import annotations

import collections
import json
from pathlib import Path

from rag.reports.html import esc, page, write_assets

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
DATA = json.loads((HERE / "results.json").read_text(encoding="utf-8"))
MANUAL_PATH = HERE / "manual.json"
MANUAL = json.loads(MANUAL_PATH.read_text(encoding="utf-8")) if MANUAL_PATH.exists() else {}
SETS = {"sim": "模拟扫描件（数字版渲染）", "user": "用户扫描件"}
METHODS = {"reference_f1": "对照数字版原文", "vocabulary": "词表", "manual": "看图", "only_one_reading": "只有一种读法"}

CSS = """
.crop{max-width:520px;max-height:90px;border:1px solid #ddd;display:block}
.crop.v{max-height:220px;max-width:90px}
td.t{font-family:Consolas,monospace;font-size:12px;max-width:380px;word-break:break-all}
.right{background:#e9f7ee}.wrong{background:#fdecea}
.num{text-align:right;font-variant-numeric:tabular-nums}
"""


def right_side(box: dict) -> str | None:
    return MANUAL[box["crop"]]["right"] if box["crop"] in MANUAL else box.get("right")


def higher(box: dict) -> str:
    on, off = box["on"], box["off"]
    if on is None or off is None:
        return "off" if on is None else "on"
    return "on" if on["conf"] > off["conf"] else "off"      # equal confidence keeps the unturned reading


def summarise() -> dict:
    out = {}
    for name in SETS:
        boxes = [b for b in DATA["boxes"] if b["set"] == name]
        groups = collections.defaultdict(collections.Counter)
        for b in boxes:
            shape = "vertical" if b["vertical"] else "horizontal"
            r = right_side(b)
            g = groups[shape]
            g["boxes"] += 1
            g["turned"] += b["turned"]
            if b["on"] and b["off"] and b["on"]["text"] == b["off"]["text"]:
                g["same_text"] += 1
                continue
            if r is None:
                g["undecided"] += 1
                continue
            g[f"right_{r}"] += 1
            g["higher_is_right"] += higher(b) == r
        out[name] = {k: dict(v) for k, v in groups.items()}
    return out


def crop_img(box: dict) -> str:
    cls = "crop v" if box["vertical"] else "crop"
    return f'<img class="{cls}" loading="lazy" src="crops/{esc(box["crop"])}">'


def reading(box: dict, side: str, right: str | None) -> str:
    r = box[side]
    if r is None:
        return '<td class="t">（未检出）</td><td class="num">—</td>'
    cls = "" if right is None else ("right" if side == right else "wrong")
    return f'<td class="t {cls}">{esc(r["text"])}</td><td class="num {cls}">{r["conf"]:.3f}</td>'


def rows(boxes: list[dict]) -> str:
    out = []
    for b in boxes:
        r = right_side(b)
        ok = "—" if r is None else ("✓" if higher(b) == r else "✗")
        method = (f'{METHODS["manual"]}：{MANUAL[b["crop"]]["note"]}' if b["crop"] in MANUAL
                  else METHODS.get(b.get("method"), ""))
        out.append(f'<tr><td>{esc(b["doc"][:30])}<br>第 {b["page"]} 页 · {"竖排" if b["vertical"] else "横排"}</td>'
                   f'<td>{crop_img(b)}</td>{reading(b, "on", r)}{reading(b, "off", r)}'
                   f'<td>{ok}</td><td>{esc(method)}</td></tr>')
    head = ('<tr><th>位置</th><th>原图局部</th><th>分类器开：文字</th><th>置信度</th><th>分类器关：文字</th>'
            '<th>置信度</th><th>高置信度的一方正确</th><th>正确方的判定依据</th></tr>')
    return f'<div class="wide"><table>{head}{"".join(out)}</table></div>'


def timing_table() -> str:
    head = ('<tr><th>扫描件</th><th>扫描页</th><th>现有 OCR（秒）</th><th>每页（秒）</th>'
            '<th>开、关各认一遍（秒）</th><th>每页（秒）</th><th>倍数</th></tr>')
    body = []
    for t in DATA["timings"]:
        n = t["scanned_pages"]
        body.append(f'<tr><td>{esc(SETS[t["set"]][:4])} · {esc(t["doc"][:40])}</td><td class="num">{n}</td>'
                    f'<td class="num">{t["current_s"]:.1f}</td><td class="num">{t["current_s"] / n:.1f}</td>'
                    f'<td class="num">{t["pick_s"]:.1f}</td><td class="num">{t["pick_s"] / n:.1f}</td>'
                    f'<td class="num">{t["pick_s"] / t["current_s"]:.2f}</td></tr>')
    total_c = sum(t["current_s"] for t in DATA["timings"])
    total_p = sum(t["pick_s"] for t in DATA["timings"])
    pages = sum(t["scanned_pages"] for t in DATA["timings"])
    body.append(f'<tr><th>合计</th><th class="num">{pages}</th><th class="num">{total_c:.1f}</th>'
                f'<th class="num">{total_c / pages:.1f}</th><th class="num">{total_p:.1f}</th>'
                f'<th class="num">{total_p / pages:.1f}</th><th class="num">{total_p / total_c:.2f}</th></tr>')
    return f'<table>{head}{"".join(body)}</table>'


def summary_table(summary: dict) -> str:
    head = ('<tr><th>扫描件</th><th>行的方向</th><th>开、关读法不同的框</th><th>分类器转了 180°</th>'
            '<th>两种读法文字相同</th><th>文字不同：开的方向正确</th><th>文字不同：关的方向正确</th>'
            '<th>文字不同：看不出正确方向</th><th>高置信度的一方方向正确</th></tr>')
    body = []
    for name, groups in summary.items():
        for shape, label in (("horizontal", "横排"), ("vertical", "竖排")):
            g = groups.get(shape)
            if not g:
                continue
            decided = g["boxes"] - g.get("undecided", 0) - g.get("same_text", 0)
            body.append(f'<tr><td>{esc(SETS[name])}</td><td>{label}</td><td class="num">{g["boxes"]}</td>'
                        f'<td class="num">{g["turned"]}</td><td class="num">{g.get("same_text", 0)}</td><td class="num">{g.get("right_on", 0)}</td>'
                        f'<td class="num">{g.get("right_off", 0)}</td><td class="num">{g.get("undecided", 0)}</td>'
                        f'<td class="num">{g.get("higher_is_right", 0)} / {decided}</td></tr>')
    return f'<table>{head}{"".join(body)}</table>'


def main() -> None:
    summary = summarise()
    timings = DATA["timings"]
    (HERE / "summary.json").write_text(json.dumps({"lines": summary, "timings": timings}, ensure_ascii=False,
                                                  indent=1), encoding="utf-8")
    boxes = DATA["boxes"]
    def differs(b):
        return not (b["on"] and b["off"] and b["on"]["text"] == b["off"]["text"])
    exceptions = [b for b in boxes if differs(b) and (right_side(b) is None or higher(b) != right_side(b))]
    tabs = [("例外：文字不同，且高置信度的一方方向不正确或看不出正确方向", exceptions)] + \
           [(f"{SETS[s]} · {shape}", [b for b in boxes if b["set"] == s and b["vertical"] == (shape == "竖排")])
            for s in SETS for shape in ("横排", "竖排")
            if any(b["set"] == s and b["vertical"] == (shape == "竖排") for b in boxes)]
    tab_html = "".join(
        f'<details {"open" if i == 0 else ""}><summary>{esc(title)}（{len(items)}）</summary>{rows(items)}</details>'
        for i, (title, items) in enumerate(tabs))
    body = (f'<style>{CSS}</style><h1>V4.1 行方向分类器：开、关各认一遍</h1>'
            '<p>同一页面图（现有 OCR 的最终图像）分别在分类器开、关下识别；两次的检测框相同，只有被分类器转了 180° 的框读法不同。'
            '方向正确的一方：模拟扫描件按框内数字版原文的词 F1 判定，用户扫描件按词表命中比例判定，两者都定不了或判定有误的看图判定；'
            '置信度相同时取分类器关的读法。</p>'
            f'<h2>逐行：高置信度的一方是否正确</h2>{summary_table(summary)}'
            f'<h2>OCR 耗时（每份扫描件，全部扫描页）</h2>{timing_table()}'
            f'<h2>逐行明细</h2>{tab_html}')
    write_assets(ROOT / "reports")
    (HERE / "index.html").write_text(page("V4.1 行方向分类器", body, root="../../",
                                          crumbs=[("报告首页", "../../index.html")]), encoding="utf-8")


if __name__ == "__main__":
    main()
