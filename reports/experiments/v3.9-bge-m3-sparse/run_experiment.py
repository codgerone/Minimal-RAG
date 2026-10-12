"""Offline experiment: bge-m3 sparse (lexical weights) vs BM25, alone and fused with dense.

Does not change the system: it reuses rag.eval.runner with a patched retriever factory and
writes every run / comparison next to this script (a Workspace whose `reports` points here).
Run from the repository root: .venv/Scripts/python.exe reports/experiments/v3.9-bge-m3-sparse/run_experiment.py

Sparse scoring follows FlagEmbedding's BGEM3: weight(token) = relu(sparse_linear(last hidden
state)), max over repeated tokens, special tokens dropped; score = sum over shared token ids
of query weight * chunk weight. Chunk vectors are cached per config in cache/ (not in git).
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from dataclasses import replace
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = next(p for p in OUT.parents if (p / "pyproject.toml").is_file())
sys.path.insert(0, str(ROOT))

import torch
from huggingface_hub import hf_hub_download
from transformers import AutoModel, AutoTokenizer

import rag.eval.runner as runner
import rag.reports.compare as compare
import rag.reports.evaluation as evaluation
from rag.config import resolve_config
from rag.index.builder import open_store
from rag.paths import Workspace
from rag.query.retriever import Hit, HybridRetriever, build_retriever, _check
from rag.registry import assemble

MODEL, REVISION = "BAAI/bge-m3", "5617a9f61b028005a4858fdac845db406aefb181"   # same as rag.index.embedder.bge_m3


class ExpWorkspace(Workspace):
    @property
    def reports(self) -> Path:
        return OUT


class SparseEncoder:
    def __init__(self):
        # Only the sparse head beyond what the dense embedder already uses (the repo also holds a 2.2 GB onnx/).
        head = hf_hub_download(MODEL, "sparse_linear.pt", revision=REVISION)
        self.tok = AutoTokenizer.from_pretrained(MODEL, revision=REVISION)
        self.model = AutoModel.from_pretrained(MODEL, revision=REVISION, dtype=torch.float32).eval()
        self.linear = torch.nn.Linear(self.model.config.hidden_size, 1)
        self.linear.load_state_dict(torch.load(head, map_location="cpu"))
        self.special = {self.tok.cls_token_id, self.tok.eos_token_id,
                        self.tok.pad_token_id, self.tok.unk_token_id}

    def encode(self, texts: list[str], batch_size: int = 8) -> list[dict[int, float]]:
        out = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start:start + batch_size]
            enc = self.tok(batch, return_tensors="pt", padding=True, truncation=True, max_length=8192)
            with torch.no_grad():
                weights = torch.relu(self.linear(self.model(**enc).last_hidden_state))[:, :, 0]
            for ids, mask, w in zip(enc["input_ids"].tolist(), enc["attention_mask"].tolist(), weights.tolist()):
                vec: dict[int, float] = {}
                for i, m, x in zip(ids, mask, w):
                    if m and i not in self.special and x > 0:
                        vec[i] = max(vec.get(i, 0.0), x)
                out.append(vec)
        return out


class SparseRetriever:
    """Same contract as BM25Retriever: chunks in scope with score > 0, by (score desc, chunk_id).
    The sparse rank / score go in Hit.lexical_rank / Hit.bm25 (labelled as sparse in the reports)."""

    def __init__(self, encoder: SparseEncoder, store, cache: Path, strip_codes: bool = False):
        self.encoder, self.store = encoder, store
        # Diagnostic variant: the catalog codes already chose the scope; drop them from the sparse query.
        self.codes = [re.compile(re.escape(c), re.IGNORECASE) for c in catalog_codes()] if strip_codes else []
        chunks = store.list_chunks()
        cached = json.loads(cache.read_text(encoding="utf-8")) if cache.is_file() else {}
        if set(cached) != {c.chunk_id for c in chunks}:
            vectors = encoder.encode([c.text for c in chunks])
            cached = {c.chunk_id: {str(k): v for k, v in vec.items()} for c, vec in zip(chunks, vectors)}
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(cached), encoding="utf-8")
        self.vectors = {cid: {int(k): v for k, v in vec.items()} for cid, vec in cached.items()}
        self.doc_of = {c.chunk_id: c.document_id for c in chunks}

    def rank_all(self, question: str, document_ids=None) -> list[tuple[str, float]]:
        for code in self.codes:
            question = code.sub(" ", question)
        q = self.encoder.encode([question.strip()])[0]
        scores = {}
        for cid, vec in self.vectors.items():
            if document_ids is not None and self.doc_of[cid] not in document_ids:
                continue
            s = sum(w * vec[t] for t, w in q.items() if t in vec)
            if s > 0:
                scores[cid] = s
        return sorted(scores.items(), key=lambda item: (-item[1], item[0]))

    def retrieve(self, question: str, top_k: int, document_ids=None) -> list[Hit]:
        _check(top_k)
        ranked = self.rank_all(question, document_ids)[:top_k]
        chunks = self.store.get_chunks([cid for cid, _ in ranked])
        return [Hit(chunks[cid], s, lexical_rank=rank, bm25=s) for rank, (cid, s) in enumerate(ranked, 1)]


def catalog_codes() -> list[str]:
    with open(ROOT / "documents" / "documents.toml", "rb") as f:
        return sorted((c for d in tomllib.load(f)["document"] for c in d["codes"]), key=len, reverse=True)


SPARSE_USES = ("sparse", "hybrid_sparse", "sparse_nocode", "hybrid_sparse_nocode")
ENCODER: SparseEncoder | None = None


def patched_build(assembly, store, lexical):
    use = assembly.config.retriever.use
    if use not in SPARSE_USES:
        return build_retriever(assembly, store, lexical)
    global ENCODER
    ENCODER = ENCODER or SparseEncoder()
    sparse = SparseRetriever(ENCODER, store, OUT / "cache" / f"{assembly.name}.json", use.endswith("_nocode"))
    if use.startswith("sparse"):
        return sparse
    semantic = build_retriever(replace(assembly, config=replace(
        assembly.config, retriever=replace(assembly.config.retriever, use="semantic"))), store, lexical)
    return HybridRetriever(semantic, sparse)


def hit_scores(hit: dict, retriever: str) -> str:
    second = "稀疏" if "sparse" in retriever else "BM25"
    fused = retriever.startswith("hybrid")
    parts = []
    if hit.get("similarity") is not None:
        parts.append(f"向量第 {hit['semantic_rank']} 名 · 相似度 {hit['similarity']:.3f}" if fused
                     else f"相似度 {hit['similarity']:.3f}")
    if hit.get("bm25") is not None:
        parts.append(f"{second} 第 {hit['lexical_rank']} 名 · {hit['bm25']:.3f}")
    elif fused:
        parts.append(f"{second} 无匹配词")
    return " · ".join(parts)


LABELS = {"semantic": "dense", "bm25": "BM25", "sparse": "bge-m3 sparse",
          "hybrid": "dense + BM25", "hybrid_sparse": "dense + bge-m3 sparse",
          "sparse_nocode": "bge-m3 sparse（查询去编码）",
          "hybrid_sparse_nocode": "dense + bge-m3 sparse（查询去编码）"}


def model_label(result: dict) -> str:
    return LABELS[result.get("retriever", "semantic")]


runner.build_retriever = patched_build
evaluation.hit_scores = compare.hit_scores = hit_scores
compare._model = model_label


def main() -> None:
    workspace = ExpWorkspace(ROOT)
    runs: dict[str, dict[str, str]] = {}
    for config_name in ("structured", "plain_text"):
        base = resolve_config(ROOT, config_name)
        for use in ("semantic", "bm25", "sparse", "sparse_nocode", "hybrid", "hybrid_sparse", "hybrid_sparse_nocode"):
            assembly = assemble(replace(base, retriever=replace(base.retriever, use="semantic")))
            assembly = replace(assembly, config=replace(assembly.config,
                                                        retriever=replace(assembly.config.retriever, use=use)))
            folder = runner.run_evaluation(workspace, assembly, assembly.config.eval_top_k)
            runs.setdefault(config_name, {})[use] = folder.name
            m = json.loads((folder / "result.json").read_text(encoding="utf-8"))["metrics"]
            print(f"{config_name:11s} {LABELS[use]:22s} " + "  ".join(
                f"{k}={m[k]['numerator']}/{m[k]['denominator']}({m[k]['value']:.1%})"
                for k in ("hit_rate", "complete_coverage", "group_recall", "mrr", "chunk_precision")), flush=True)
    order = {"lexical": ("bm25", "sparse", "sparse_nocode"),
             "fused": ("hybrid", "hybrid_sparse", "hybrid_sparse_nocode", "semantic")}
    for page, uses in order.items():
        names = [runs[c][u] for c in runs for u in uses]
        print(compare.write_comparison(workspace, page, names))
    (OUT / "runs.json").write_text(json.dumps(runs, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
