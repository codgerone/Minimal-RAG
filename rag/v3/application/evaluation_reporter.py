"""Render immutable evaluation facts into JSON, HTML and comparison views."""

from __future__ import annotations

import hashlib
import html
import json
from dataclasses import asdict

from rag.v3.application.assembly import canonical_json_bytes
from rag.v3.application.evaluation_comparison import comparison_report
from rag.v3.contracts.evaluation import (
    BaselineSelection, ComparisonDocumentIdentity, ComparisonQueryConfig,
    ComparisonRunSnapshot, EvaluationAggregate, EvaluationDocumentResult,
    EvaluationRunRequest, RenderedEvaluation, RenderedEvaluationFile,
)


def run_directory_name(request: EvaluationRunRequest) -> str:
    name = request.configuration_name
    if (name not in {"plain_text", "structured"}
            or not request.run_id
            or any(not (c.isalnum() or c in "-_") for c in request.run_id)):
        raise ValueError("unsafe evaluation run identity")
    return (f"assembly-{name}__cfg-{request.index_identity.build_fingerprint[:12]}"
            f"__k-{request.query_config.top_k}__{request.run_id}")


def _file(location: str, path: str, media_type: str, content: bytes
          ) -> RenderedEvaluationFile:
    return RenderedEvaluationFile(location, path, media_type, content,
                                  hashlib.sha256(content).hexdigest())


def _metric_text(value: dict) -> str:
    if value["status"] == "not_applicable":
        return "not_applicable"
    return f'{value["value"]:.4f} ({value["numerator"]}/{value["denominator"]})'


_ALL_METRICS = (
    "macro_chunk_precision_at_k", "micro_chunk_precision_at_k",
    "macro_evidence_group_recall_at_k", "micro_evidence_group_recall_at_k",
    "question_hit_rate_at_k", "complete_coverage_rate_at_k",
    "mean_reciprocal_rank_at_k", "evidence_group_mean_reciprocal_rank_at_k",
    "macro_cross_document_contamination_at_k",
    "micro_cross_document_contamination_at_k",
)


def _build_summary(run: ComparisonRunSnapshot) -> str:
    config = json.loads(run.build_config_json)
    if run.build_config_schema == "legacy_build_config_v2":
        selected = {name: config[name] for name in ("parser", "chunker", "embedding")
                    if name in config}
    else:
        selected = {item["slot_id"]: {"plugin_id": item["plugin_id"],
                    "parameters": item["parameters"]} for item in config["bindings"]
                    if item["slot_id"] in {"document_processor.main_parser",
                                            "indexer.chunker", "indexer.embedder"}}
    return json.dumps(selected, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _document_html(json_bytes: bytes) -> bytes:
    """The matching document JSON is the sole input to the human review page."""
    data = json.loads(json_bytes)
    esc = html.escape
    parts = ["<!doctype html><html lang='zh'><meta charset='utf-8'>",
             "<title>V3 检索证据审核</title>",
             "<style>body{font:16px/1.55 system-ui;max-width:1100px;margin:2rem auto;padding:0 1rem}"
             "article{border-top:2px solid #999;padding:1rem 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}"
             "table{border-collapse:collapse}td,th{border:1px solid #aaa;padding:.3rem}"
             "details{margin:1rem 0}</style>",
             f"<h1>{esc(data['document_name'])}</h1>",
             f"<p>PDF SHA-256: <code>{esc(data['file_hash'])}</code></p>"]
    for case in sorted(data["cases"], key=lambda item: item["case_id"]):
        parts.extend([f"<article><h2>{esc(case['case_id'])}</h2>",
                      f"<p>问题：{esc(case['question'])}</p>",
                      f"<p>状态：{esc(case['status'])}；可回答：{str(case['answerable']).lower()}</p>",
                      f"<p>参考答案：{esc(case['reference_answer'])}</p>",
                      "<h3>预期证据</h3>"])
        if not case["expected_evidence"]:
            parts.append("<p>无证据组（不可回答题）</p>")
        for group in case["expected_evidence"]:
            parts.append(f"<section><h4>{esc(group['evidence_group_id'])} · "
                         f"{esc(group['mapping_status'])}</h4>")
            excerpt_maps = {item["excerpt_id"]: item
                            for item in group["excerpt_mappings"]}
            for excerpt in group["excerpts"]:
                pages = ", ".join(str(page) for page in excerpt["page_numbers"])
                excerpt_map = excerpt_maps[excerpt["excerpt_id"]]
                parts.append(f"<p>{esc(excerpt['document_name'])} · 页 {pages}</p>"
                             f"<pre>{esc(excerpt['text'])}</pre>"
                             f"<p>该 excerpt 的 chunk 映射（仅审核）："
                             f"{esc(str(excerpt_map['chunk_sets']))}</p>")
            parts.append("<p>可接受 chunk 组合：" + esc(str(group["acceptable_chunk_sets"]))
                         + "</p></section>")
        parts.append("<h3>实际检索结果</h3>")
        if not case["retrieved_chunks"]:
            parts.append("<p>零返回</p>")
        for hit in case["retrieved_chunks"]:
            pages = ", ".join(str(page) for page in hit["page_numbers"])
            groups = ", ".join(hit["matched_evidence_group_ids"]) or "无"
            parts.append(f"<section><h4>#{hit['rank']} · {esc(hit['document_name'])}"
                         f" · 页 {pages}</h4><p>命中证据组：{esc(groups)}；"
                         f"跨文档：{str(hit['cross_document']).lower()}</p>"
                         f"<pre>{esc(hit['text'])}</pre>"
                         f"<details><summary>检索诊断</summary><p>chunk ID: "
                         f"{esc(hit['chunk_id'])}；distance: {hit['distance']}；"
                         f"similarity: {hit['similarity']}</p></details></section>")
        missing = [group["evidence_group_id"] for group in case["expected_evidence"]
                   if not any(group["evidence_group_id"] in hit["matched_evidence_group_ids"]
                              for hit in case["retrieved_chunks"])]
        parts.append("<p>未命中组：" + esc(", ".join(missing) or "无") + "；unmappable："
                     + esc(", ".join(case["unmappable_evidence_group_ids"]) or "无")
                     + "</p>")
        if case["metrics"] is not None:
            parts.append("<h3>逐题指标与分子分母</h3><table><tr><th>指标</th><th>值</th></tr>")
            for name, value in case["metrics"].items():
                shown = _metric_text(value) if isinstance(value, dict) else str(value)
                parts.append(f"<tr><td>{esc(name)}</td><td>{esc(shown)}</td></tr>")
            parts.append("</table>")
        if case["error"] is not None:
            parts.append(f"<p>错误：{esc(str(case['error']))}</p>")
        parts.append("</article>")
    parts.append("</html>")
    return "\n".join(parts).encode("utf-8")


def _snapshot(request: EvaluationRunRequest,
              documents: tuple[EvaluationDocumentResult, ...],
              aggregate: EvaluationAggregate) -> ComparisonRunSnapshot:
    identities = tuple(sorted((ComparisonDocumentIdentity(item.document_id, item.file_hash,
        tuple(sorted((case.case_id, case.question) for case in item.cases)))
        for item in documents), key=lambda item: item.document_id))
    query = ComparisonQueryConfig(**asdict(request.query_config))
    run_path = ("validation/retrieval/system-v3.0/runs/"
                + run_directory_name(request) + "/run.json")
    return ComparisonRunSnapshot("3.0", request.run_id, run_path,
        request.configuration_name, request.index_identity.collection_name,
        "build_projection_v3",
        canonical_json_bytes(asdict(request.build_config)),
        request.index_identity.build_fingerprint, request.ground_truth,
        identities, query, request.evaluation_protocol_version,
        request.test_set.annotation_rule_version,
        tuple(sorted(aggregate.documents, key=lambda item: item.document_id)),
        aggregate.overall.metrics,
        "validation/retrieval/system-v3.0/summary.md")


class JsonHtmlEvaluationReporter:
    def render(self, request: EvaluationRunRequest,
               documents: tuple[EvaluationDocumentResult, ...],
               aggregate: EvaluationAggregate,
               history: tuple[ComparisonRunSnapshot, ...],
               baselines: BaselineSelection) -> RenderedEvaluation:
        if (not documents or aggregate.run_id != request.run_id
                or any(item.run_id != request.run_id for item in documents)
                or len({item.document_key for item in documents}) != len(documents)):
            raise ValueError("evaluation result identity differs")
        run_directory_name(request)
        files = []
        for document in documents:
            if "/" in document.document_key or "\\" in document.document_key:
                raise ValueError("unsafe document key")
            raw = canonical_json_bytes(asdict(document))
            files.append(_file("run", f"documents/{document.document_key}.json",
                               "application/json", raw))
            files.append(_file("run", f"documents/{document.document_key}.html",
                               "text/html", _document_html(raw)))
        files.append(_file("run", "aggregate.json", "application/json",
                           canonical_json_bytes(asdict(aggregate))))
        current = _snapshot(request, documents, aggregate)
        replaced_ids = {item.run_id for item in history
                        if item.system_version == "3.0"
                        and item.configuration_label == request.configuration_name}
        if any(item.system_version == "3.0" and item.run_id in replaced_ids
               for item in baselines.entries):
            raise ValueError("clear the selected V3 baseline before replacing its run")
        latest_v3: dict[str, ComparisonRunSnapshot] = {}
        for item in history:
            if item.system_version == "3.0":
                previous = latest_v3.get(item.configuration_label)
                if previous is None or item.run_id > previous.run_id:
                    latest_v3[item.configuration_label] = item
        visible = tuple(item for item in history if item.system_version != "3.0") + tuple(
            item for name, item in sorted(latest_v3.items())
            if name != request.configuration_name) + (current,)
        report = comparison_report(visible, request.started_at)
        comparison_json = canonical_json_bytes(asdict(report))
        files.append(_file("comparison_root", "comparison.json", "application/json",
                           comparison_json))
        lines = ["# 检索评估跨版本比较", "", f"生成时间：{report.generated_at}", "",
                 "| 系统 | 运行 | 装配 | 构建指纹 | 汇总 |", "| --- | --- | --- | --- | --- |"]
        for run in report.runs:
            lines.append(f"| {run.system_version} | {run.run_id} | {run.configuration_label} | "
                         f"`{run.build_config_fingerprint}` | [{run.summary_path}]({run.summary_path}) |")
        lines.extend(["", "## 运行配置与整体指标", ""])
        for run in sorted(visible, key=lambda item: (item.system_version, item.run_id)):
            lines.extend([f"### {run.system_version} · {run.configuration_label} · {run.run_id}",
                "", f"collection：`{run.collection_name}`；构建指纹："
                f"`{run.build_config_fingerprint}`；K={run.query_config.top_k}；"
                f"数据集：`{run.ground_truth.ground_truth_fingerprint}`。", "",
                "主要构建字段：", "", "```json", _build_summary(run), "```", "",
                "| 指标 | 整体值（分子/分母） |", "| --- | ---: |"])
            for name in _ALL_METRICS:
                lines.append(f"| {name} | {_metric_text(asdict(getattr(run.overall_metrics, name)))} |")
            lines.append("")
        lines.extend(["", "| 左 | 右 | 可比性 | 差异原因 |", "| --- | --- | --- | --- |"])
        for item in report.comparisons:
            lines.append(f"| {item.left.system_version}/{item.left.run_id} | "
                         f"{item.right.system_version}/{item.right.run_id} | "
                         f"{item.status} | {', '.join(item.difference_codes) or '无'} |")
            if item.metric_deltas:
                lines.append("\n| 指标 | 左 | 右 | 右减左 |\n| --- | ---: | ---: | ---: |")
                for delta in item.metric_deltas:
                    left = (f"{delta.left_value:.4f}" if delta.left_value is not None
                            else "N/A")
                    right = (f"{delta.right_value:.4f}" if delta.right_value is not None
                             else "N/A")
                    difference = (f"{delta.absolute_delta:+.4f}"
                                  if delta.absolute_delta is not None else "N/A")
                    lines.append(f"| {delta.metric_name} | {left} | {right} | {difference} |")
        files.append(_file("comparison_root", "comparison.md", "text/markdown",
                           ("\n".join(lines) + "\n").encode("utf-8")))
        current_key = (request.ground_truth.ground_truth_fingerprint,
            request.evaluation_protocol_version, request.query_config,
            request.test_set.annotation_rule_version)
        selected = [item for item in baselines.entries if
            (item.ground_truth_fingerprint, item.evaluation_protocol_version,
             item.query_config, item.annotation_rule_version) == current_key]
        if len(selected) > 1:
            raise ValueError("duplicate formal baseline")
        summary = ["# V3.0 检索评估汇总", "",
                   f"数据集：{request.ground_truth.dataset_version} / "
                   f"`{request.ground_truth.ground_truth_fingerprint}`；"
                   f"PDF {len(documents)} 份；题目 {sum(len(d.cases) for d in documents)} 道。", "",
                   "正式基线：" + (f"{selected[0].system_version}/{selected[0].run_id}"
                                  if selected else "正式基线未选定"), "",
                   "| 装配 | run ID | 索引 | K | 状态 | 整体 Question Hit | 组召回 | 完整覆盖 |"
                   " Chunk Precision | MRR | 跨文档污染 |", "| --- | --- | --- | ---: | --- |"
                   " ---: | ---: | ---: | ---: | ---: | ---: |"]
        for item in sorted((run for run in visible
                            if run.system_version == "3.0"), key=lambda run: run.run_id):
            m = item.overall_metrics
            values = [getattr(m, name).value for name in (
                "question_hit_rate_at_k", "macro_evidence_group_recall_at_k",
                "complete_coverage_rate_at_k", "macro_chunk_precision_at_k",
                "mean_reciprocal_rank_at_k", "macro_cross_document_contamination_at_k")]
            summary.append(f"| {item.configuration_label} | {item.run_id} | "
                f"`{item.build_config_fingerprint}` | {item.query_config.top_k} | completed | "
                + " | ".join(f"{value:.4f}" if value is not None else "N/A"
                             for value in values) + " |")
        summary.extend(["", "## 运行配置与逐 PDF 指标", ""])
        for item in sorted((run for run in visible
                            if run.system_version == "3.0"), key=lambda run: run.run_id):
            summary.extend([f"### {item.configuration_label} · {item.run_id}", "",
                f"collection：`{item.collection_name}`；构建指纹："
                f"`{item.build_config_fingerprint}`；K={item.query_config.top_k}；"
                f"查询前缀：`{item.query_config.query_prefix}`；状态：completed；失败数：0。",
                "", "主要构建字段：", "", "```json", _build_summary(item), "```", "",
                f"完整配置：[run.json]({item.source_run_path.removeprefix('validation/retrieval/system-v3.0/')})",
                "", "| PDF / 汇总 | 可回答 | 不可回答 | unmappable | "
                + " | ".join(_ALL_METRICS) + " |",
                "| --- | ---: | ---: | ---: | " + " | ".join(
                    "---:" for _ in _ALL_METRICS) + " |"])
            for scope in (*item.document_summaries,):
                label = next((doc.document_id for doc in item.documents
                              if doc.document_id == scope.document_id), scope.document_id)
                values = " | ".join(_metric_text(asdict(getattr(scope.metrics, name)))
                                     for name in _ALL_METRICS)
                summary.append(f"| {label} | {scope.answerable_count} | "
                    f"{scope.unanswerable_count} | {scope.unmappable_group_count} | "
                    + values + " |")
            overall_count = sum(scope.answerable_count for scope in item.document_summaries)
            unknown_count = sum(scope.unanswerable_count for scope in item.document_summaries)
            unmappable_count = sum(scope.unmappable_group_count for scope in item.document_summaries)
            summary.append(f"| 整体 | {overall_count} | {unknown_count} | "
                f"{unmappable_count} | " + " | ".join(
                    _metric_text(asdict(getattr(item.overall_metrics, name)))
                    for name in _ALL_METRICS) + " |")
            summary.append("")
        summary.extend(["## 可比性", "",
                        "| 左 | 右 | 状态 | 差异原因 |",
                        "| --- | --- | --- | --- |"])
        for comparison in report.comparisons:
            summary.append(f"| {comparison.left.system_version}/{comparison.left.run_id} | "
                f"{comparison.right.system_version}/{comparison.right.run_id} | "
                f"{comparison.status} | {', '.join(comparison.difference_codes) or '无'} |")
        summary.extend(["", "## 本次 PDF 结果", ""])
        for document in documents:
            prefix = "runs/" + run_directory_name(request) + "/documents/" + document.document_key
            summary.append(f"- {document.document_name} · SHA `{document.file_hash}` · "
                           f"[JSON]({prefix}.json) · [HTML]({prefix}.html)")
        summary.append("")
        files.append(_file("system_version", "summary.md", "text/markdown",
                           "\n".join(summary).encode("utf-8")))
        return RenderedEvaluation(request.run_id, documents, aggregate, tuple(files))
