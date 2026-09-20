"""Deterministic machine and human reports for retrieval evaluation."""

from __future__ import annotations

import html
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from rag.evaluation_models import EvaluationDocumentResult, MetricValue


def json_bytes(value: Any) -> bytes:
    return (json.dumps(asdict(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _metric(value: MetricValue) -> str:
    return "不适用" if value.value is None else f"{value.value:.2%} ({value.numerator:g}/{value.denominator})"


def render_document(result: EvaluationDocumentResult) -> str:
    cases: list[str] = []
    for case in result.cases:
        expected = []
        for group in case.expected_evidence:
            excerpts = "".join(
                f"<article><div class='source'>{html.escape(x.document_name)} · 第 {', '.join(map(str, x.page_numbers))} 页</div>"
                f"<pre>{html.escape(x.text)}</pre></article>" for x in group.excerpts
            )
            expected.append(f"<section><h4>证据组 {html.escape(group.evidence_group_id)}</h4>{excerpts}</section>")
        actual = "".join(
            f"<article class='{'relevant' if hit.relevant else ''}'><div class='source'>排名 {hit.rank} · "
            f"{html.escape(hit.document_name)} · 第 {', '.join(map(str, hit.page_numbers)) or '未知'} 页 · "
            f"similarity {hit.similarity:.6f}</div><pre>{html.escape(hit.text)}</pre></article>"
            for hit in case.retrieved_chunks
        ) or "<p>未返回片段</p>"
        metrics = "不适用" if case.metrics is None else (
            f"Hit@K：{_metric(case.metrics.question_hit_at_k)}　"
            f"证据组 Recall@K：{_metric(case.metrics.evidence_group_recall_at_k)}　"
            f"Chunk Precision@K：{_metric(case.metrics.chunk_precision_at_k)}　"
            f"完整覆盖：{_metric(case.metrics.complete_coverage_at_k)}"
        )
        cases.append(
            f"<div class='case'><h2>{html.escape(case.case_id)}</h2><p class='question'>{html.escape(case.question)}</p>"
            f"<p class='metrics'>{metrics}</p><div class='columns'><div><h3>预期证据</h3>{''.join(expected) or '<p>无</p>'}</div>"
            f"<div><h3>实际返回</h3>{actual}</div></div></div>"
        )
    return """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>检索评估</title>
<style>body{font-family:system-ui,sans-serif;max-width:1500px;margin:24px auto;padding:0 18px;color:#17202a}
.case{border-top:2px solid #ccd6dd;padding:18px 0}.columns{display:grid;grid-template-columns:1fr 1fr;gap:20px}
article{border:1px solid #d9e1e8;border-radius:7px;margin:10px 0;padding:10px;background:#fafbfc}.relevant{border-color:#2e8b57;background:#eef9f2}
.source{font-size:13px;color:#52616b;margin-bottom:6px}pre{white-space:pre-wrap;word-break:break-word;font:14px/1.5 system-ui,sans-serif}
.question{font-size:17px}.metrics{background:#eef3f7;padding:10px}@media(max-width:900px){.columns{grid-template-columns:1fr}}</style>
<body><h1>检索评估：""" + html.escape(result.document_name) + "</h1>" + "".join(cases) + "</body></html>"


def write_summary(root: Path) -> None:
    rows = []
    detail_sections = []
    completed: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for path in sorted((root / "runs").glob("*/run.json")):
        run = json.loads(path.read_text(encoding="utf-8"))
        if run.get("status") != "completed":
            continue
        aggregate_doc = json.loads((path.parent / "aggregate.json").read_text(encoding="utf-8"))
        aggregate = aggregate_doc["overall"]["metrics"]
        completed.append((run, aggregate_doc))
        def value(name: str, metrics: dict[str, Any] = aggregate) -> str:
            raw = metrics[name]["value"]
            return "—" if raw is None else f"{raw:.2%}"
        rows.append(
            f"| {run['pipeline_id']} | `{run['build_config_fingerprint'][:12]}` | {run['query_config']['top_k']} | "
            f"{value('question_hit_rate_at_k')} | {value('macro_evidence_group_recall_at_k')} | "
            f"{value('complete_coverage_rate_at_k')} | {value('macro_chunk_precision_at_k')} | "
            f"{value('mean_reciprocal_rank_at_k')} | {value('macro_cross_document_contamination_at_k')} | `{run['run_id']}` |"
        )
        document_rows = []
        document_entries = {item["document_key"]: item for item in run["documents"]}
        document_results = {
            json.loads((path.parent / item["json_path"]).read_text(encoding="utf-8"))["document_id"]:
            (json.loads((path.parent / item["json_path"]).read_text(encoding="utf-8")), item)
            for item in document_entries.values()
        }
        for scope in aggregate_doc["documents"]:
            metrics = scope["metrics"]
            document, entry = document_results[scope["document_id"]]
            label = document["document_name"].replace("|", "／")
            link = f"runs/{path.parent.name}/{entry['html_path']}"
            document_rows.append(
                f"| [{label}]({link}) | {metrics['included_question_count']} | "
                f"{value('question_hit_rate_at_k', metrics)} | {value('macro_evidence_group_recall_at_k', metrics)} | "
                f"{value('complete_coverage_rate_at_k', metrics)} | {value('macro_chunk_precision_at_k', metrics)} |"
            )
        detail_sections.append(
            f"## {run['pipeline_id']} · K={run['query_config']['top_k']} · `{run['run_id']}`\n\n"
            "| PDF（人工审核） | Questions | Hit@K | Group Recall@K | Complete@K | Chunk Precision@K |\n"
            "|---|---:|---:|---:|---:|---:|\n" + "\n".join(document_rows)
        )
    text = "# RAG v2.0 检索效果汇总\n\n| Pipeline | Build fingerprint | K | Hit@K | Group Recall@K | Complete@K | Chunk Precision@K | MRR@K | Cross-doc contamination | Run |\n|---|---|---:|---:|---:|---:|---:|---:|---:|---|\n"
    root.mkdir(parents=True, exist_ok=True)
    (root / "summary.md").write_text(text + "\n".join(rows) + "\n\n" + "\n\n".join(detail_sections) + "\n", encoding="utf-8")
    _write_comparisons(root.parent, completed)


def _write_comparisons(output_root: Path, completed: list[tuple[dict[str, Any], dict[str, Any]]]) -> None:
    primary = (
        "question_hit_rate_at_k", "macro_evidence_group_recall_at_k",
        "complete_coverage_rate_at_k", "macro_chunk_precision_at_k",
        "mean_reciprocal_rank_at_k", "macro_cross_document_contamination_at_k",
    )
    comparisons = []
    for index, (baseline, baseline_aggregate) in enumerate(completed):
        for candidate, candidate_aggregate in completed[index + 1:]:
            codes = []
            if baseline["ground_truth"] != candidate["ground_truth"]:
                codes.append("ground_truth_fingerprint")
            if baseline["query_config"]["top_k"] != candidate["query_config"]["top_k"]:
                codes.append("top_k")
            if baseline["evaluation_protocol_version"] != candidate["evaluation_protocol_version"]:
                codes.append("evaluation_protocol_version")
            if baseline["test_set"]["annotation_rule_version"] != candidate["test_set"]["annotation_rule_version"]:
                codes.append("annotation_rule_version")
            for field, code in (("query_prefix", "query_prefix"), ("document_filter", "document_filter"),
                                ("distance_order", "distance_order"), ("tie_break_order", "tie_break_order")):
                if baseline["query_config"][field] != candidate["query_config"][field]:
                    codes.append(code)
            status = "strictly_comparable" if not codes else (
                "dataset_changed" if any(code in {"dataset_version", "ground_truth_fingerprint", "pdf_hash", "question_text"} for code in codes)
                else "protocol_changed"
            )
            deltas = []
            if status == "strictly_comparable":
                left = baseline_aggregate["overall"]["metrics"]
                right = candidate_aggregate["overall"]["metrics"]
                for name in primary:
                    left_value, right_value = left[name]["value"], right[name]["value"]
                    delta = None if left_value is None or right_value is None else right_value - left_value
                    deltas.append({"metric_name": name, "baseline_value": left_value,
                                   "candidate_value": right_value, "absolute_delta": delta})
            comparisons.append({"baseline_run_id": baseline["run_id"], "candidate_run_id": candidate["run_id"],
                                "status": status, "difference_codes": codes, "metric_deltas": deltas})
    payload = {"schema_version": "evaluation_comparison_v1",
               "generated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat().replace("+00:00", "Z"),
               "comparisons": comparisons}
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "comparison.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# 检索评估对比", ""]
    for item in comparisons:
        lines += [f"## `{item['baseline_run_id']}` → `{item['candidate_run_id']}`", "", f"状态：`{item['status']}`", ""]
        if item["metric_deltas"]:
            lines += ["| Metric | Baseline | Candidate | Delta |", "|---|---:|---:|---:|"]
            for metric in item["metric_deltas"]:
                lines.append(f"| {metric['metric_name']} | {metric['baseline_value']:.2%} | {metric['candidate_value']:.2%} | {metric['absolute_delta']:+.2%} |")
            lines.append("")
    (output_root / "comparison.md").write_text("\n".join(lines), encoding="utf-8")
