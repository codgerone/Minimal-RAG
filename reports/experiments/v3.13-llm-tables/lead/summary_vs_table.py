"""对照：表格证据所在的表，用摘要做向量（S）与用表格 chunk 做向量（RM）时，与问题的相似度。

S 的整表以摘要 chunk 入库，RM 的整表以表格 chunk 入库；两者的非表格 chunk 相同。
对每道表格题的每个表格证据组，取证据所在表在两种入库下与问题最相似的一块，比较相似度与向量名次。

用法（仓库根目录）：python reports/experiments/v3.13-llm-tables/lead/summary_vs_table.py
结果写入同目录 summary_vs_table.json。
"""
import json
from pathlib import Path

from rag.config import resolve_config
from rag.eval import table_metrics as tm
from rag.eval.dataset import load_dataset
from rag.eval.judge import judge_case
from rag.eval.runner import _judge_chunks, _table_boxes
from rag.index.builder import open_store
from rag.ingest.sources import discover
from rag.paths import Workspace
from rag.query.retriever import SemanticRetriever
from rag.query.scope import load_catalog, resolve_scope
from rag.registry import assemble

HERE = Path(__file__).parent
# Run on the indexes before prompt v5 (config names of that time; now structured / structured_summary).
CONFIGS = {"RM": "structured_markdown_rows", "S": "structured_summary_gemini38f"}


def main() -> None:
    workspace = Workspace(Path("."))
    dataset = load_dataset(workspace.ground_truth)
    catalog = load_catalog(workspace.documents, discover(workspace.documents))
    result: dict[tuple[str, str], dict] = {}
    for code, name in CONFIGS.items():
        assembly = assemble(resolve_config(workspace.root, name))
        store = open_store(workspace, name)
        judged = _judge_chunks(workspace, name, {c.chunk_id for c in store.list_chunks()})
        boxes = _table_boxes(workspace, name)
        semantic = SemanticRetriever(assembly.embedder, store)
        for case in dataset.cases:
            own = {x.excerpt_id for x in case.excerpts if tm.is_table_excerpt(x, boxes.get(x.document_id, []))}
            if not own:
                continue
            evidence = judge_case(case, judged)
            ranked = semantic.rank_all(case.question, resolve_scope(case.question, catalog, True).document_ids)
            for group in tm.table_groups(case, evidence, own):
                pos = next(((r, c, s) for r, (c, s) in enumerate(ranked, 1) if c.chunk_id in group.any_word_chunks), None)
                row = result.setdefault((case.case_id, group.group_id), {"case_id": case.case_id, "group_id": group.group_id,
                                                                         "question": case.question})
                row[code] = {"vector_rank": pos[0], "similarity": round(pos[2], 4), "kind": pos[1].kind} if pos else None
    rows = list(result.values())
    for r in rows:
        print(r["case_id"], r["group_id"], r["RM"], "->", r["S"])
    (HERE / "summary_vs_table.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
