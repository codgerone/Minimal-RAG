"""从 LLM 缓存取回提示词 v4 的引导句（做法同 recover_v4.py：按 v5 入库的中间结果重建结构标注请求，
用 v5 提示词核对缓存键，再还原成 v4 取回第一次调用的结果）。

用法（仓库根目录）：python reports/experiments/v3.13-llm-tables/summary/recover_v4_leads.py
结果写入同目录 leads.json。
"""
import json
from pathlib import Path

from rag.index.audit import load_audit
from rag.ingest.tables import llm_tables as lt

HERE = Path(__file__).parent
MODEL = "google/gemini-3.8-flash"
V5_SENTENCE = (" Do not mention numbers\n   or codes that identify the document itself: the lead is matched to questions"
               " by meaning, and\n   such numbers do not help.")


def key(messages: list[dict[str, str]], version: str) -> str:
    saved = lt.PROMPT_VERSION
    lt.PROMPT_VERSION = version
    try:
        return lt.LLMCache(Path(".rag/llm-cache")).key(MODEL, messages, lt.ANNOTATION_SCHEMA)
    finally:
        lt.PROMPT_VERSION = saved


def main() -> None:
    assert V5_SENTENCE in lt.ANNOTATION_SYSTEM
    cache = lt.LLMCache(Path(".rag/llm-cache"))
    out = []
    for folder in sorted(Path(".rag/indexes/structured_llm_lead/documents").iterdir()):
        audit = load_audit(folder)
        for i, table in enumerate(audit.prepared_tables):
            llm = table.llm
            lengths = lt.Lengths(audit.document.language, 0)     # the annotation prompt uses only the lead limit
            messages = lt.build_messages("annotation", lt.render_grid(table.adopted_table), llm.context, lengths)
            hit = cache.get(key(messages, "llm_tables_v5"))
            if not hit or json.loads(hit["text"])["lead"] != llm.lead:
                raise SystemExit(f"v5 request not reconstructed: {folder.name} table {i}")
            old = [dict(m) for m in messages]
            old[0]["content"] = old[0]["content"].replace(V5_SENTENCE, "")
            v4 = cache.get(key(old, "llm_tables_v4"))
            if v4 is None:
                raise SystemExit(f"v4 annotation not in cache: {folder.name} table {i}")
            out.append({"document": folder.name, "table": i, "table_id": table.table_id,
                        "v5": llm.lead, "v4": json.loads(v4["text"])["lead"]})
            print(folder.name[:30], i, "| v4:", out[-1]["v4"], "| v5:", llm.lead, flush=True)
    (HERE / "leads.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
