"""Render draft test-set mappings for human review without embedding vectors."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

from rag.evaluation_repository import load_ground_truth_dataset
from rag.vector_store import ChromaVectorStore, StoredRecord


STYLE = """
body{font-family:system-ui,sans-serif;max-width:1500px;margin:28px auto;padding:0 18px;color:#172033}
h1{margin-bottom:4px}h2{margin-top:42px;border-bottom:2px solid #cbd5e1;padding-bottom:8px}
h3{margin:0}.meta,.source{color:#526075}.case{border:1px solid #cbd5e1;border-radius:10px;margin:18px 0;padding:16px}
.question{font-size:1.04rem}.group{border:1px solid #dbe3ee;border-radius:8px;margin-top:14px;padding:12px}
.evidence{background:#f8fafc;padding:10px;white-space:pre-wrap;margin:8px 0}.pipelines{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.pipeline{border:1px solid #dbe3ee;border-radius:7px;padding:10px;min-width:0}.mapped{color:#15803d}.unmappable{color:#b42318}
.chunk{background:#eef6ff;border-left:4px solid #3b82f6;padding:10px;margin-top:8px;white-space:pre-wrap;overflow-wrap:anywhere}
code{background:#eef2f7;padding:2px 5px;border-radius:4px}@media(max-width:900px){.pipelines{grid-template-columns:1fr}}
"""


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def physical_pages(metadata: dict) -> str:
    """Return the normalized physical PDF pages stored by either pipeline."""
    if "page_numbers_json" in metadata:
        try:
            pages = json.loads(str(metadata["page_numbers_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            pages = []
        if isinstance(pages, list) and pages:
            return ", ".join(str(page) for page in pages)
    pages = metadata.get("page_numbers")
    if isinstance(pages, (list, tuple)) and pages:
        return ", ".join(str(page) for page in pages)
    page = metadata.get("page_number")
    return "?" if page is None else str(page)


def load_test_set(root: Path, directory: Path) -> tuple[dict, dict[str, dict]]:
    manifest = load_json(directory / "manifest.json")
    documents = {entry["document_key"]: load_json(root / entry["json_path"]) for entry in manifest["files"]}
    return manifest, documents


def load_records(root: Path, manifest: dict) -> dict[str, StoredRecord]:
    store = ChromaVectorStore(root / ".rag/system-v2/chroma", manifest["collection_name"])
    records = {record.record_id: record for record in store.list_records()}
    expected = {
        chunk_id
        for entry in manifest["files"]
        for case in load_json(root / entry["json_path"])["cases"]
        for group in case["evidence_groups"]
        for chunk_set in group["acceptable_chunk_sets"]
        for chunk_id in chunk_set
    }
    missing = sorted(expected - records.keys())
    if missing:
        raise RuntimeError(f"映射引用了不存在的 chunks: {missing}")
    return records


def render(root: Path, directories: list[Path]) -> str:
    ground_truth = load_ground_truth_dataset(root, required_status="approved")
    sets = [load_test_set(root, directory) for directory in directories]
    records = [load_records(root, manifest) for manifest, _ in sets]
    parts = [
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<style>{STYLE}</style><title>Test sets 映射审核</title></head><body>",
        "<h1>v1 / v2 Test sets 映射审核</h1>",
        "<p class='meta'>审核标准：同一可接受组合内的 chunks 必须共同完整覆盖 evidence group（AND）；多个组合互为替代（OR）。只有组合索引中的全部 chunks 仍无法恢复事实时才标记 unmappable。</p>",
    ]
    for manifest, _ in sets:
        parts.append(
            f"<p><strong>{esc(manifest['pipeline_id'])}</strong> · 状态 <code>{esc(manifest['review_status'])}</code> · "
            f"build <code>{esc(manifest['build_config_fingerprint'])}</code> · test set <code>{esc(manifest['test_set_fingerprint'])}</code></p>"
        )
    for gt_document in ground_truth.documents:
        parts.append(f"<h2>{esc(gt_document.document_name)}</h2>")
        mapped_documents = [documents[gt_document.document_key] for _, documents in sets]
        for case_index, gt_case in enumerate(gt_document.cases):
            parts.extend([
                "<section class='case'>", f"<h3>{esc(gt_case.case_id)}</h3>",
                f"<p class='question'><strong>问题：</strong>{esc(gt_case.question)}</p>",
            ])
            for group_index, gt_group in enumerate(gt_case.evidence_groups):
                parts.append(f"<div class='group'><strong>{esc(gt_group.evidence_group_id)}</strong>")
                for excerpt in gt_group.excerpts:
                    pages = ", ".join(map(str, excerpt.page_numbers))
                    parts.append(
                        f"<div class='source'>Ground truth · PDF 物理页 {esc(pages)}</div>"
                        f"<div class='evidence'>{esc(excerpt.text)}</div>"
                    )
                parts.append("<div class='pipelines'>")
                for set_index, ((manifest, _), mapped_document) in enumerate(zip(sets, mapped_documents, strict=True)):
                    mapping = mapped_document["cases"][case_index]["evidence_groups"][group_index]
                    status = mapping["status"]
                    parts.append(
                        f"<div class='pipeline'><strong>{esc(manifest['pipeline_id'])}</strong> · "
                        f"<span class='{esc(status)}'>{esc(status)}</span>"
                    )
                    if status == "unmappable":
                        parts.append("<p class='meta'>组合当前索引中的 chunks 仍无法恢复该组完整事实。</p>")
                    for combination_index, chunk_set in enumerate(mapping["acceptable_chunk_sets"], 1):
                        parts.append(f"<p><strong>可接受组合 {combination_index}</strong>（以下 chunks 全部需要）</p>")
                        for chunk_id in chunk_set:
                            record = records[set_index][chunk_id]
                            pages = physical_pages(record.metadata)
                            parts.append(
                                f"<div class='chunk'><code>{esc(chunk_id)}</code><br>"
                                f"<span class='source'>PDF 物理页 {esc(pages)}</span><br>{esc(record.document)}</div>"
                            )
                    parts.append("</div>")
                parts.append("</div></div>")
            parts.append("</section>")
    parts.append("</body></html>")
    return "".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=Path(".rag/system-v2/evaluation-drafts/test-set-review.html"))
    args = parser.parse_args()
    root = args.project_root.resolve()
    directories = sorted((root / "eval/test-sets").glob("system-v2.0__pipeline-*"))
    if len(directories) != 2:
        raise RuntimeError(f"预期两套 test sets，实际找到 {len(directories)} 套。")
    output = args.output if args.output.is_absolute() else root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(root, directories), encoding="utf-8")
    print(output.resolve())


if __name__ == "__main__":
    main()
