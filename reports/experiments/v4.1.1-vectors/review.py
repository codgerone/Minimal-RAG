"""Review page for the V4.1.1 vector comparison (run.py → summary.json). Writes index.html next to
this script. Run: python reports/experiments/v4.1.1-vectors/review.py"""

from __future__ import annotations

import json
from pathlib import Path

from rag.reports.html import badge, esc, page, write_assets

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def sci(value: float) -> str:
    return "0" if value == 0 else f"{value:.2e}"


def main() -> None:
    summary = json.loads((HERE / "summary.json").read_text(encoding="utf-8"))
    noise, configs = summary["noise"], summary["configs"]
    bound = noise["bound"]
    worst = max(r["layer2"]["max"] for r in configs.values())
    swaps = sum(len(r["layer3"]["swaps_within_bound"]) for r in configs.values())
    violations = sum(len(r["layer3"]["violations"]) for r in configs.values())
    over = sum(len(r["layer2"]["over_bound"]) for r in configs.values())
    texts = sum(len(r["layer1"]["text_mismatches"]) for r in configs.values())
    verdict = (badge("通过", "ok") if summary["passed"] else badge("不通过", "bad"))
    conclusion = (
        f'<div class="card"><h3>结论 {verdict}</h3>'
        f'<div>八份索引重建后与 V4.1 比较，三层标准{"全部通过" if summary["passed"] else "有不通过的项"}：</div><ul>'
        f'<li>对应：chunk 编号一一对应，向量维度相同，送入编码器的文字不同 {texts} 处。</li>'
        f'<li>数值：所有 chunk 的 ‖Δv‖ 最大 {sci(worst)}，批次噪声上界 {sci(bound)}，超过上界 {over} 个。</li>'
        f'<li>排名：{configs["structured"]["layer3"]["questions"]} 题 × 8 份索引的纯向量全量排名中，'
        f'名次互换 {swaps} 处，都满足"旧分数差 ≤ 两者 ‖Δv‖ 之和"的有 {swaps} 处，违反 {violations} 处。</li></ul></div>')
    tiles = "".join(f'<div class="tile"><div class="v">{v}</div><div class="k">{k}</div></div>' for v, k in (
        (sci(bound), "批次噪声上界 ‖Δ‖"), (sci(worst), "新旧向量最大 ‖Δv‖"), (over, "超过上界的 chunk"),
        (swaps, "名次互换（满足不等式）"), (violations, "名次互换（违反不等式）")))
    rows = "".join(
        f'<tr><td>{esc(name)}</td><td>{badge("通过", "ok") if r["passed"] else badge("不通过", "bad")}</td>'
        f'<td class="num">{r["layer1"]["chunks"]}</td><td class="num">{"、".join(map(str, r["layer1"]["dims"]))}</td>'
        f'<td class="num">{len(r["layer1"]["text_mismatches"])}</td>'
        f'<td class="num">{sci(r["layer2"]["max"])}</td><td class="num">{sci(r["layer2"]["median"])}</td>'
        f'<td class="num">{r["layer2"]["bit_identical"]}</td><td class="num">{len(r["layer2"]["over_bound"])}</td>'
        f'<td class="num">{len(r["layer3"]["swaps_within_bound"])}</td><td class="num">{len(r["layer3"]["violations"])}</td></tr>'
        for name, r in configs.items())
    table = ('<h2>逐份索引</h2><div class="scroll"><table><tr><th>配置</th><th>判定</th><th class="num">chunk</th>'
             '<th class="num">维度</th><th class="num">文字不同</th><th class="num">最大 ‖Δv‖</th><th class="num">中位 ‖Δv‖</th>'
             '<th class="num">逐位相同</th><th class="num">超过上界</th><th class="num">互换（满足）</th>'
             f'<th class="num">互换（违反）</th></tr>{rows}</table></div>')
    labels = {"alone~batch8": "单独 与 8 条一批", "alone~batch32": "单独 与 32 条一批", "batch8~batch32": "8 条一批 与 32 条一批"}
    noise_rows = "".join(f'<tr><td>{labels[k]}</td><td class="num">{sci(v["max"])}</td><td class="num">{sci(v["median"])}</td>'
                         f'<td class="num">{v["bit_identical"]} / {noise["texts"]}</td></tr>' for k, v in noise["pairs"].items())
    rep = noise["reproducibility"]
    noise_html = (f'<h2>批次噪声上界</h2><div class="sub">八份索引中不同的 chunk 文字共 {noise["texts"]} 段，每段单独编码、'
                  f'随机组成 8 条一批与 32 条一批各编码一次；上界取同一段文字两种编法之差的最大值。不使用 V4.1 的向量。'
                  f'另把每段文字在本次进程中重新单独编码，与重建时（另一个进程）存下的向量比较：'
                  f'{rep["bit_identical"]} / {rep["texts"]} 段逐位相同，其余的差最大 {sci(rep["max"])}，'
                  f'约为上界的 {rep["max"] / bound:.0%}；原因未查明（已排除编码器设置不同、编码先后顺序两种）。'
                  f'步骤缓存使这种差不进入索引：缓存命中时直接读出重建时存下的向量。</div>'
                  '<div class="scroll"><table><tr><th>两种编法</th><th class="num">最大 ‖Δ‖</th><th class="num">中位 ‖Δ‖</th>'
                  f'<th class="num">逐位相同</th></tr>{noise_rows}</table></div>')
    perturb_html = ""
    if (HERE / "perturb.json").is_file():
        pt = json.loads((HERE / "perturb.json").read_text(encoding="utf-8"))
        names = {"delete_char": "删一个字符", "replace_letter": "换一个字母", "replace_digit": "换一个数字",
                 "replace_word": "换一个词", "space_trailing": "末尾加一个空格", "space_double": "一个空格改两个"}
        real = [v for k, v in pt["kinds"].items() if not k.startswith("space")]
        smallest = min(v["min"] for v in real)
        prow = "".join(f'<tr><td>{names[k]}</td><td class="num">{v["n"]}</td><td class="num">{sci(v["min"])}</td>'
                       f'<td class="num">{sci(v["p05"])}</td><td class="num">{sci(v["median"])}</td>'
                       f'<td class="num">{v["zero"]}</td><td class="num">{v["within_10x_bound"]}</td></tr>'
                       for k, v in pt["kinds"].items())
        perturb_html = (
            f'<h2>最小的真实改变</h2><div class="sub">抽 {pt["sample"]} 段 chunk 文字（含最长的 {pt["longest_included"]} 段），'
            f'每段各做一处最小改动，与原文都单独编码后比较。改字符、数字、词的 ‖Δv‖ 最小 {sci(smallest)}，'
            f'是批次噪声上界的 {smallest / bound:.0f} 倍；以后的判定上界取噪声上界的 10 倍（{sci(10 * bound)}），'
            f'与最小的真实改变仍差 {smallest / (10 * bound):.0f} 倍。只改空白时向量逐位不变（分词器忽略多余空格），'
            f'这类改动只能由第 1 层（送入编码器的文字相同）查出。</div>'
            '<div class="scroll"><table><tr><th>改动</th><th class="num">段数</th><th class="num">最小 ‖Δv‖</th>'
            '<th class="num">5% 分位</th><th class="num">中位</th><th class="num">向量不变</th>'
            f'<th class="num">≤ 10 倍噪声上界</th></tr>{prow}</table></div>')
    details = []
    for name, r in configs.items():
        items = r["layer3"]["violations"] + r["layer3"]["swaps_within_bound"] + r["layer2"]["over_bound"]
        if not items:
            continue
        lines = "".join(
            f'<tr><td>{esc(i.get("case", ""))}</td><td>{esc(" / ".join(i.get("chunks", [i.get("chunk", "")])))}</td>'
            f'<td class="num">{sci(i.get("old_gap", i.get("norm", 0)))}</td><td class="num">{sci(i.get("bound", bound))}</td>'
            f'<td class="num">{esc(" / ".join(map(str, i.get("old_ranks", []))))}</td></tr>' for i in items)
        details.append(f'<details><summary>{esc(name)}：{len(items)} 项</summary><div class="scroll"><table>'
                       '<tr><th>题</th><th>chunk</th><th class="num">旧分数差（或 ‖Δv‖）</th><th class="num">上界</th>'
                       f'<th class="num">V4.1 名次</th></tr>{lines}</table></div></details>')
    details_html = "<h2>名次互换与超界明细</h2>" + ("".join(details) or '<div class="meta">没有任何名次互换，也没有超过上界的 chunk。</div>')
    method = ('<h2>判定标准</h2><div class="meta">见 docs/changes/v4.1.1.md 拍板问题 3。查询向量与 chunk 向量都已归一化，'
              '一个 chunk 的分数变化不超过它的 ‖Δv‖，所以两个 chunk 只有在旧分数差 ≤ 两者 ‖Δv‖ 之和时才可能互换名次；'
              '排名用全部 chunk（不按文档过滤），过滤后的排名是它的子序列。原始数据见同目录 summary.json、noise.json，'
              '计算脚本 run.py。</div>')
    body = (f'<h1>向量比较 · V4.1.1 重建与 V4.1</h1><div class="sub">编码改为逐条单独编码后，判定新旧向量是否相同</div>'
            f'{conclusion}<div class="tiles">{tiles}</div>{table}{noise_html}{perturb_html}{details_html}{method}')
    write_assets(ROOT / "reports")
    (HERE / "index.html").write_text(page("向量比较 · V4.1.1", body, root="../../",
                                          crumbs=[("报告首页", "../../index.html")]), encoding="utf-8")
    print(HERE / "index.html")


if __name__ == "__main__":
    main()
