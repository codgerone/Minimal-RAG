"""Run an isolated real-PDF pipeline build, publication, health, and retrieval smoke test."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from rag.bootstrap import create_runtime
from rag.cli_readiness import inspect_index_for_cli
from rag.config import (
    DEFAULT_EMBEDDING_MODEL, DEFAULT_OPENROUTER_MODEL, Settings, select_pipeline,
)
from rag.embeddings import DEFAULT_EMBEDDING_REVISION, E5Embedder
from rag.pipeline_indexer import RuntimeIndexer
from rag.retriever import Retriever
from rag.vector_store import ChromaVectorStore


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("tmp/v2-runtime-validation"))
    parser.add_argument("--query", default="这份文档的主要内容是什么？")
    parser.add_argument("--pipeline", choices=("v1", "v2"), default="v2")
    args = parser.parse_args()
    source = args.pdf.resolve()
    root = args.output.resolve()
    if not source.is_file() or source.suffix.casefold() != ".pdf":
        parser.error("--pdf must point to an existing PDF")
    if root.exists():
        raise SystemExit(f"refusing to overwrite existing validation directory: {root}")
    documents = root / "documents"
    documents.mkdir(parents=True)
    shutil.copy2(source, documents / source.name)
    settings = Settings(
        root, documents, root / ".rag/system-v2/chroma",
        root / ".rag/system-v2/artifacts",
        DEFAULT_EMBEDDING_MODEL, DEFAULT_EMBEDDING_REVISION,
        700, 100, 512, 32, 3, True, None, DEFAULT_OPENROUTER_MODEL,
    )
    selected = select_pipeline(settings, args.pipeline)
    runtime = create_runtime(selected)
    embedder = E5Embedder(settings.embedding_model, settings.embedding_model_revision)
    store = ChromaVectorStore(settings.db_path, runtime.collection_name)
    summary = RuntimeIndexer(selected, runtime, embedder, store).ingest_all()
    inspection = inspect_index_for_cli(selected, store)
    hits = []
    if summary.failed == 0 and inspection.health.usable:
        hits = Retriever(selected, embedder, store).search(args.query, top_k=3)
    result = {
        "pipeline_id": runtime.pipeline_id,
        "collection_name": runtime.collection_name,
        "manifest_path": runtime.manifest_path.relative_to(root).as_posix(),
        "ingest": {
            "scanned": summary.scanned, "added": summary.added,
            "updated": summary.updated, "failed": summary.failed,
            "collection_count": summary.collection_count,
        },
        "health_usable": inspection.health.usable,
        "issues": [item.code for item in inspection.health.issues],
        "hit_count": len(hits),
        "hits": [
            {"chunk_id": item.chunk_id, "similarity": item.similarity,
             "text_preview": item.text[:160]} for item in hits
        ],
        "output": root.as_posix(),
    }
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if summary.failed == 0 and inspection.health.usable and hits else 1


if __name__ == "__main__":
    raise SystemExit(main())
