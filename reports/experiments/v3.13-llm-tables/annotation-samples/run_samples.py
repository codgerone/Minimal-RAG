"""样例：模型 × 5 张表（2 张真实表 + 3 张人造样表），每张表两次调用（结构标注含引导句、摘要）。
第 2 轮跑了 3 个模型（页面保留为 round2.html）；第 3 轮只跑选定的 gemini。

用法（仓库根目录）：python reports/experiments/v3.13-llm-tables/annotation-samples/run_samples.py [模型...]
真实表读取 .rag/indexes/structured/ 的解析结果；LLM 结果缓存在 .rag/llm-cache/，重跑不重复计费。
结果写入 results/<模型>/<表>.json，输入写入 results/_inputs/<表>.json。
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from synthetic import TABLES as SYNTHETIC, build  # noqa: E402

from rag.ingest.chunkers.tokens import E5TokenCounter  # noqa: E402
from rag.ingest.language import document_language  # noqa: E402
from rag.ingest.tables.header import detect_header  # noqa: E402
from rag.ingest.tables.llm_tables import (  # noqa: E402
    LEAD_MAX_TOKENS, LLMCache, Lengths, plan_chunks, plan_issues, render_grid, run_annotation,
    run_summary, table_context,
)
from rag.ingest.tables.markdown_rows import serialize_markdown_rows  # noqa: E402
from rag.ingest.tables.models import StructuredTable, TableAnnotation, TableCell  # noqa: E402
from rag.jsonio import dumps, write_atomic  # noqa: E402
from rag.models import BoundingBox  # noqa: E402
from rag.query.llm import OpenRouterLLM  # noqa: E402

HERE = Path(__file__).parent
RESULTS = HERE / "results"
INDEX = Path(".rag/indexes/structured/documents")
MODELS = ["openai/gpt-6-luna", "deepseek/deepseek-v4.1-flash", "google/gemini-3.8-flash"]
NO_TEMPERATURE = {"openai/gpt-6-luna"}   # no provider of it accepts temperature
REAL = [  # key, 文档目录名片段, 表格节点 ID, 显示名
    ("po2519-qty", "25-19", "node_ea2f030118e96505f55e", "PO 25-19 分批交付数量表"),
    ("contract-object", "Contrato", "node_7504dd672d005aae9ac0", "合同 c0004 合同标的"),
]
MAX_TOKENS = 512


def node_text(node: dict) -> str:
    if "items" in node:
        return "\n".join(item.get("text", "") for item in node["items"])
    return node.get("text") or ""


def node_kind(node: dict) -> str:
    return "table" if "table" in node else "list" if "items" in node else node.get("kind", "other")


def as_table(data: dict) -> StructuredTable:
    cells = tuple(TableCell(c["cell_id"], c["text"], c["start_row_offset_idx"], c["end_row_offset_idx"],
                            c["start_col_offset_idx"], c["end_col_offset_idx"], c["row_span"], c["col_span"],
                            BoundingBox(**c["bbox"]) if c.get("bbox") else None, tuple(c["roles"]),
                            c["span_source"], tuple(c["source_refs"])) for c in data["cells"])
    return StructuredTable(data["table_id"], data["tool"], data["strategy"], data["row_count"],
                           data["column_count"], cells, (), data.get("unplaced_text"), (), ())


def load_real(fragment: str, node_id: str, counter: E5TokenCounter):
    folder = next(p for p in INDEX.iterdir() if fragment in p.name)
    nodes = json.loads((folder / "parsed.json").read_text(encoding="utf-8"))["nodes"]
    index = next(i for i, n in enumerate(nodes) if n["node_id"] == node_id)
    pairs = [(node_kind(n), node_text(n)) for n in nodes]
    language = document_language(text for kind, text in pairs if kind != "table")
    table = as_table(nodes[index]["table"])
    # Rebuilt from the parse result; must equal what the index serialized.
    assert serialize_markdown_rows(table, detect_header(table), table_node_id=node_id,
                                   labeled=True).text == nodes[index]["serialized"]["text"]
    return table, table_context(pairs, index, counter.count_text), language


def renderings(table, node_id, annotation: TableAnnotation | None, fits) -> dict:
    """Chunks of the markdown and labeled forms; rule forms when `annotation` is None."""
    header = detect_header(table) if annotation is None else None
    out = {}
    for name, labeled in (("markdown", False), ("labeled", True)):
        serialized = serialize_markdown_rows(table, header, table_node_id=node_id, labeled=labeled,
                                             annotation=annotation)
        out[name] = (serialized, plan_chunks(serialized, annotation, fits))
    return out


def checks(serialized, chunks, annotation: TableAnnotation) -> list[str]:
    """Code checks shown on the page: header in every chunk, section titles where their rows are."""
    found = []
    line_of = {l.line_id: l for l in serialized.lines}
    headers = {l.line_id for l in serialized.lines if l.kind == "header"}
    row_line = {l.source_rows[0]: l.line_id for l in serialized.lines
                if l.source_rows and l.kind != "unplaced_text"}
    for i, chunk in enumerate(chunks, 1):
        present = set(chunk.context_line_ids) | set(chunk.line_ids)
        if headers - present:
            found.append(f"chunk {i} 缺表头行")
        rows = {r for lid in chunk.line_ids for r in line_of[lid].source_rows
                if line_of[lid].kind != "unplaced_text"}
        for s in annotation.sections:
            if rows & set(range(s.first_row, s.last_row + 1)):
                missing = [t for t in s.title_rows if row_line[t] not in present]
                if missing:
                    found.append(f"chunk {i} 含第 {s.first_row} 行起的节的行，缺标题行 {missing}")
        if chunk.oversize:
            found.append(f"chunk {i} 单行超出预算（{chunk.oversize}）")
    return found


def chunk_records(chunks, counter, lead=None) -> list[dict]:
    out = []
    for c in chunks:
        text = f"{lead}\n{c.text}" if lead else c.text
        out.append({"text": text, "tokens": counter.count_passage(text),
                    "repeated": len(c.context_line_ids), "lines": len(c.line_ids)})
    return out


def main(models: list[str]) -> None:
    counter = E5TokenCounter()
    prefix = counter.count_passage("")
    rule_fits = lambda text: counter.count_passage(text) <= MAX_TOKENS
    # Validation leaves room for the longest accepted lead; packing counts the actual one.
    check_fits = lambda text: counter.count_passage(text) <= MAX_TOKENS - LEAD_MAX_TOKENS
    cache = LLMCache(Path(".rag/llm-cache"))
    inputs = {}
    for key, fragment, node_id, title in REAL:
        table, context, language = load_real(fragment, node_id, counter)
        inputs[key] = (title, node_id, table, context, language, None)
    for key, title, language, nodes, rows, expected in SYNTHETIC:
        table = build(key, rows())
        context = table_context(nodes + [("table", "")], len(nodes), counter.count_text)
        inputs[key] = (title, key, table, context, language, expected)

    for key, (title, node_id, table, context, language, expected) in inputs.items():
        grid = render_grid(table)
        rule = renderings(table, node_id, None, rule_fits)
        header = detect_header(table)
        write_atomic(RESULTS / "_inputs" / f"{key}.json", dumps({
            "title": title, "node_id": node_id, "language": language, "context": context,
            "grid": grid.markdown, "expected": expected,
            "rule_header_rows": (list(range(header.header_start_row, header.header_end_row))
                                 if header.outcome == "identified" else None),
            "rule_chunks": {name: chunk_records(chunks, counter) for name, (_, chunks) in rule.items()},
            "lengths": Lengths(language, MAX_TOKENS - prefix), "max_tokens": MAX_TOKENS,
            "lead_max_tokens": LEAD_MAX_TOKENS}))

    def run_model(model: str) -> None:
        llm = OpenRouterLLM(model=model, temperature=None if model in NO_TEMPERATURE else 0,
                            env_override=False)
        for key, (title, node_id, table, context, language, _) in inputs.items():
            grid = render_grid(table)
            lengths = Lengths(language, MAX_TOKENS - prefix)
            out: dict = {}

            def extra(annotation: TableAnnotation) -> list[str]:
                found = []
                for serialized, chunks in renderings(table, node_id, annotation, check_fits).values():
                    found += plan_issues(chunks, serialized)
                return list(dict.fromkeys(found))

            try:
                result = run_annotation(llm, table, grid, context, lengths, counter.count_text, cache,
                                        extra)
                out["annotation"] = result
                fallback = result.annotation is None
                out["fallback"] = {"annotation": fallback, "lead": result.lead is None}
                for name, (serialized, chunks) in renderings(table, node_id, result.annotation, rule_fits).items():
                    out[name] = chunk_records(chunks, counter)
                    out[name + "_checks"] = [] if fallback else checks(serialized, chunks, result.annotation)
                if result.lead:
                    lead = result.lead
                    lead_fits = lambda text: counter.count_passage(lead + "\n" + text) <= MAX_TOKENS
                    for name, (serialized, chunks) in renderings(table, node_id, result.annotation, lead_fits).items():
                        out[name + "_lead"] = chunk_records(chunks, counter, lead)
                        out[name + "_lead_checks"] = [] if fallback else checks(serialized, chunks, result.annotation)
                else:
                    out["markdown_lead"] = out["labeled_lead"] = None
            except Exception as exc:  # recorded, so one failing model does not stop the rest
                out["annotation"] = {"error": f"{type(exc).__name__}: {exc}"}
            try:
                out["summary"] = run_summary(llm, grid, context, lengths, counter.count_text, cache)
                if out["summary"].summary:
                    out["summary_tokens"] = counter.count_passage(out["summary"].summary)
            except Exception as exc:
                out["summary"] = {"error": f"{type(exc).__name__}: {exc}"}
            write_atomic(RESULTS / model.replace("/", "__") / f"{key}.json", dumps(out))
            a = out["annotation"]
            print(model, key, "annotation", "error" if isinstance(a, dict) else
                  (a.annotation is not None, a.lead is not None, len(a.attempts)), flush=True)

    counter.count_text("warm up")  # load the tokenizer once before threads share it
    with ThreadPoolExecutor(len(models)) as pool:
        list(pool.map(run_model, models))


if __name__ == "__main__":
    main(sys.argv[1:] or MODELS)
