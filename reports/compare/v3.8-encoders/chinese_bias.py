"""V3.8 Chinese-bias check (decision 005 method), per embedder, on the structured chunks.

Encode every chunk twice: as indexed, and with one irrelevant Chinese sentence prepended to
table chunks only. For each answerable question whose evidence contains no table chunk, rank
the chunks of its document by cosine similarity and count table chunks in the Top-3.

usage: python chinese_bias.py <baseline structured result.json> <out.json>
"""
import json
import sys
from pathlib import Path

import numpy as np

from rag.index import embedder as embedders

SENTENCE = "今天天气很好，适合出门散步。"
MODELS = ["e5_small", "e5_large_instruct", "bge_m3", "qwen3_embedding_0_6b"]
TOP = 3

result = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
chunks = [c for path in sorted(Path(".rag/indexes/structured/documents").glob("*/chunks.json"))
          for c in json.loads(path.read_text(encoding="utf-8"))["chunks"]]
kind = {c["chunk_id"]: c["kind"] for c in chunks}
questions = []
for case in result["cases"]:
    if not case["answerable"]:
        continue
    evidence = {cid for g in case["groups"] for s in g["acceptable_sets"] for cid in s}
    if not any(kind[cid] == "table" for cid in evidence):
        questions.append(case)
tables_per_doc = {}
for c in chunks:
    tables_per_doc.setdefault(c["document_id"], 0)
    tables_per_doc[c["document_id"]] += c["kind"] == "table"

report = {"sentence": SENTENCE, "top": TOP, "questions": [q["case_id"] for q in questions],
          "slots": TOP * len(questions), "models": {}}
for name in MODELS:
    model = getattr(embedders, name)()
    plain = np.array(model.encode_passages([c["text"] for c in chunks]))
    biased = np.array(model.encode_passages(
        [(SENTENCE + "\n" + c["text"]) if c["kind"] == "table" else c["text"] for c in chunks]))
    counts = {}
    for label, matrix in (("as_indexed", plain), ("chinese_prefixed", biased)):
        total = 0
        for case in questions:
            q = np.array(model.encode_query(case["question"]))
            idx = [i for i, c in enumerate(chunks) if c["document_id"] == case["document_id"]]
            order = sorted(idx, key=lambda i: -float(matrix[i] @ q))[:TOP]
            total += sum(chunks[i]["kind"] == "table" for i in order)
        counts[label] = total
    report["models"][name] = counts
    print(name, counts, flush=True)
Path(sys.argv[2]).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
