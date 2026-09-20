"""Render the current ground-truth dataset as a compact human-review page."""

from __future__ import annotations

import argparse
import html
from pathlib import Path

from rag.evaluation_repository import load_ground_truth_dataset


STYLE = """
body{font-family:system-ui,sans-serif;max-width:1200px;margin:32px auto;padding:0 20px;color:#1f2937}
h1{margin-bottom:4px}h2{margin-top:40px;border-bottom:2px solid #d1d5db;padding-bottom:8px}
.meta{color:#4b5563}.case{border:1px solid #d1d5db;border-radius:10px;margin:18px 0;padding:18px}
.case h3{margin:0 0 10px}.question{font-size:1.05rem}.answer{background:#f0fdf4;border-left:4px solid #16a34a;padding:12px;white-space:pre-wrap}
.group{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;margin-top:12px;padding:12px}
.group h4{margin:0 0 8px}.source{color:#475569;font-size:.9rem}.excerpt{white-space:pre-wrap;margin:8px 0 0}
code{background:#f1f5f9;padding:2px 5px;border-radius:4px}
"""


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def render(project_root: Path) -> str:
    dataset = load_ground_truth_dataset(project_root)
    parts = [
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<style>{STYLE}</style><title>Ground truth 人工审核</title></head><body>",
        "<h1>Ground truth 人工审核</h1>",
        f"<p class='meta'>版本 <code>{esc(dataset.manifest.dataset_version)}</code> · 状态 "
        f"<code>{esc(dataset.manifest.review_status)}</code> · fingerprint "
        f"<code>{esc(dataset.manifest.ground_truth_fingerprint)}</code></p>",
        "<p>请逐题审核问题、参考答案、证据组拆分、物理页码和原文摘录。e1/e2…代表回答问题缺一不可的独立事实。</p>",
    ]
    for document in dataset.documents:
        parts.append(f"<h2>{esc(document.document_name)}</h2>")
        parts.append(f"<p class='meta'>document_id: <code>{esc(document.document_id)}</code></p>")
        for case in document.cases:
            parts.extend([
                "<section class='case'>",
                f"<h3>{esc(case.case_id)}</h3>",
                f"<p class='question'><strong>问题：</strong>{esc(case.question)}</p>",
                f"<div class='answer'><strong>参考答案：</strong>\n{esc(case.reference_answer)}</div>",
            ])
            for group in case.evidence_groups:
                parts.append(f"<div class='group'><h4>{esc(group.evidence_group_id)}</h4>")
                for excerpt in group.excerpts:
                    pages = ", ".join(str(page) for page in excerpt.page_numbers)
                    parts.append(
                        f"<div class='source'>{esc(excerpt.document_name)} · PDF 物理页 {esc(pages)}</div>"
                        f"<div class='excerpt'>{esc(excerpt.text)}</div>"
                    )
                parts.append("</div>")
            parts.append("</section>")
    parts.append("</body></html>")
    return "".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".rag/system-v2/evaluation-drafts/ground-truth-review.html"),
    )
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else args.project_root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(args.project_root), encoding="utf-8")
    print(output.resolve())


if __name__ == "__main__":
    main()
