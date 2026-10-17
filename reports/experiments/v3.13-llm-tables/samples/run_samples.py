"""样例：8 个模型 × 4 张表 × 3 项调用（记录、引导句、摘要），结果写入 results/。

用法：python reports/experiments/v3.13-llm-tables/samples/run_samples.py [模型...]
读取 .rag/indexes/structured/ 的中间结果；LLM 结果缓存在 .rag/llm-cache/。
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from rag.index.analyzer import detect_language
from rag.ingest.chunkers.tokens import E5TokenCounter
from rag.ingest.tables.llm_tables import (LLMCache, Limits, TableContext, pack_records,
                                          render_grid, run_call)
from rag.jsonio import dumps, write_atomic
from rag.query.llm import OpenRouterLLM

HERE = Path(__file__).parent
INDEX = Path(".rag/indexes/structured/documents")
MODELS = ["anthropic/claude-haiku-5.5", "openai/gpt-6-luna", "deepseek/deepseek-v4.1-flash",
          "google/gemini-3.8-flash", "anthropic/claude-sonnet-5.5", "openai/gpt-6.1-sol",
          "google/gemini-4-argon", "anthropic/claude-opus-5.5"]
TABLES = [  # (key, 文档目录名片段, 表格节点 ID, 显示名)
    ("po2519-qty", "25-19", "node_ea2f030118e96505f55e", "PO 25-19 分批交付数量表"),
    ("contract-schedule", "Contrato", "node_73df07d05cf37ea3c86e", "合同 交货计划表 CRONOGRAMA DE ENTREGA"),
    ("contract-object", "Contrato", "node_7504dd672d005aae9ac0", "合同 c0004 合同标的"),
    ("contract-specs", "Contrato", "node_af2e93dd3f6afeb30bcf", "合同 c0006 技术规格"),
]
MAX_TOKENS, LEAD_MAX = 512, 48
BODY = {"paragraph", "caption", "other_text", "table_note"}


def node_text(node: dict) -> str:
    if "items" in node:
        return "\n".join(item.get("text", "") for item in node["items"])
    return node.get("text") or ""


def context_of(nodes: list[dict], index: int) -> TableContext:
    """Nearest title before the table; nearest body text or list before it, not crossing a table."""
    heading = before = None
    for node in reversed(nodes[:index]):
        if "table" in node:
            break
        kind = node.get("kind")
        if heading is None and kind == "title":
            heading = node["text"]
        if before is None and (kind in BODY or "items" in node):
            before = node_text(node)
        if heading is not None and before is not None:
            break
    return TableContext(heading, before)


def as_table(data: dict) -> SimpleNamespace:
    cells = tuple(SimpleNamespace(**cell) for cell in data["cells"])
    return SimpleNamespace(row_count=data["row_count"], column_count=data["column_count"],
                           cells=cells, unplaced_text=data.get("unplaced_text"))


def load(fragment: str, node_id: str):
    folder = next(p for p in INDEX.iterdir() if fragment in p.name)
    parsed = json.loads((folder / "parsed.json").read_text(encoding="utf-8"))
    nodes = parsed["nodes"]
    index = next(i for i, n in enumerate(nodes) if n["node_id"] == node_id)
    language = detect_language(node_text(n) for n in nodes if "table" not in n)
    chunks = json.loads((folder / "chunks.json").read_text(encoding="utf-8"))
    chunks = chunks["chunks"] if isinstance(chunks, dict) else chunks
    rule_chunks = [c["text"] for c in chunks if c["kind"] == "table"
                   and any(s["node_id"] == node_id for s in c["sources"])]
    return nodes[index], context_of(nodes, index), language, rule_chunks


def main(models: list[str]) -> None:
    counter = E5TokenCounter()
    prefix = counter.count_passage("")
    limits = Limits.from_budget(MAX_TOKENS, LEAD_MAX, prefix)
    cache = LLMCache(Path(".rag/llm-cache"))
    inputs = {}
    for key, fragment, node_id, title in TABLES:
        node, context, language, rule_chunks = load(fragment, node_id)
        grid = render_grid(as_table(node["table"]))
        inputs[key] = (title, node_id, grid, context, language, rule_chunks,
                       node["serialized"]["text"])
        write_atomic(HERE / "results" / "_inputs" / f"{key}.json", dumps({
            "title": title, "node_id": node_id, "language": language, "context": context,
            "grid": grid.markdown, "rule_text": node["serialized"]["text"], "rule_chunks": rule_chunks,
            "limits": limits, "max_tokens": MAX_TOKENS}))

    def run_model(model: str) -> None:
        llm = OpenRouterLLM(model=model, env_override=False)
        for key, (title, node_id, grid, context, language, _, _) in inputs.items():
            out = {}
            for kind in ("records", "lead", "summary"):
                try:
                    out[kind] = run_call(kind, llm, grid, context, language, limits,
                                         counter.count_text, cache)
                except Exception as exc:  # recorded, so one failing model does not stop the rest
                    out[kind] = {"error": f"{type(exc).__name__}: {exc}"}
            records = out["records"]
            if not isinstance(records, dict) and records.records:
                out["chunks"] = pack_records(records.records, counter.count_passage, MAX_TOKENS)
                lead = out["lead"]
                if not isinstance(lead, dict) and lead.text:
                    out["chunks_with_lead"] = pack_records(records.records, counter.count_passage,
                                                           MAX_TOKENS, lead.text)
            write_atomic(HERE / "results" / model.replace("/", "__") / f"{key}.json", dumps(out))
            print(model, key, {k: ("error" if isinstance(v, dict) else v.valid)
                               for k, v in out.items() if k in ("records", "lead", "summary")}, flush=True)

    counter.count_text("warm up")  # load the tokenizer once before threads share it
    with ThreadPoolExecutor(len(models)) as pool:
        list(pool.map(run_model, models))


if __name__ == "__main__":
    main(sys.argv[1:] or MODELS)
