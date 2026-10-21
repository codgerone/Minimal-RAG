"""从 LLM 缓存取回提示词 v4 的表格摘要（V3.13 改为 v5 之前 S-T 入库所用的摘要）。

按 v5 入库的中间结果重建每张表的摘要请求；先用 v5 提示词算缓存键，确认与入库结果一致，
再把系统提示词还原成 v4（去掉"不写识别文档本身的编号"一句）、版本号改为 v4，按同一请求取回缓存。
不调用 LLM：缓存缺失即报错。

用法（仓库根目录）：python reports/experiments/v3.13-llm-tables/summary/recover_v4.py
结果写入同目录 summaries.json。
"""
import json
from pathlib import Path

from rag.index.audit import load_audit
from rag.ingest.tables import llm_tables as lt

HERE = Path(__file__).parent
MODEL = "google/gemini-3.8-flash"
V5_SENTENCE = (" Do not mention numbers or codes that identify the document itself, unless they are\n"
               "   values in the table's cells: the summary is matched to questions by meaning, and such numbers\n"
               "   do not help.")


def key(messages: list[dict[str, str]], version: str) -> str:
    saved = lt.PROMPT_VERSION
    lt.PROMPT_VERSION = version
    try:
        return lt.LLMCache(Path(".rag/llm-cache")).key(MODEL, messages, lt.SUMMARY_SCHEMA)
    finally:
        lt.PROMPT_VERSION = saved


def main() -> None:
    assert V5_SENTENCE in lt.SUMMARY_SYSTEM
    cache = lt.LLMCache(Path(".rag/llm-cache"))
    out = []
    for folder in sorted(Path(".rag/indexes/structured_summary/documents").iterdir()):
        audit = load_audit(folder)
        for i, table in enumerate(audit.prepared_tables):
            llm = table.llm
            grid = lt.render_grid(table.adopted_table)
            for limit in range(480, 513):    # the passage prefix is not stored; find the one that matches
                lengths = lt.Lengths(audit.document.language, limit)
                messages = lt.build_messages("summary", grid, llm.context, lengths)
                hit = cache.get(key(messages, "llm_tables_v5"))
                if hit and json.loads(hit["text"])["summary"] == llm.summary:
                    break
            else:
                raise SystemExit(f"v5 request not reconstructed: {folder.name} table {i}")
            old = [dict(m) for m in messages]
            old[0]["content"] = old[0]["content"].replace(V5_SENTENCE, "")
            v4 = cache.get(key(old, "llm_tables_v4"))
            if v4 is None:
                raise SystemExit(f"v4 summary not in cache: {folder.name} table {i}")
            out.append({"document": folder.name, "table": i, "table_id": table.table_id,
                        "v5": llm.summary, "v4": json.loads(v4["text"])["summary"]})
            print(folder.name, i, "ok", flush=True)
    (HERE / "summaries.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
