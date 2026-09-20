"""Build both pipelines in isolation and compare retrieval on the fixed eval set."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from time import perf_counter

from rag.bootstrap import create_runtime
from rag.cli_readiness import inspect_index_for_cli
from rag.config import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_OPENROUTER_MODEL,
    Settings,
    select_pipeline,
)
from rag.embeddings import DEFAULT_EMBEDDING_REVISION, E5Embedder
from rag.evaluator import Evaluator, load_evaluation_cases
from rag.pipeline_indexer import RuntimeIndexer
from rag.retriever import Retriever
from rag.vector_store import ChromaVectorStore


def _settings(root: Path) -> Settings:
    return Settings(
        root,
        root / "documents",
        root / ".rag/system-v2/chroma",
        root / ".rag/system-v2/artifacts",
        DEFAULT_EMBEDDING_MODEL,
        DEFAULT_EMBEDDING_REVISION,
        700,
        100,
        512,
        32,
        3,
        True,
        None,
        DEFAULT_OPENROUTER_MODEL,
    )


def _copy_inputs(source_dir: Path, target_dir: Path) -> list[str]:
    pdfs = sorted(source_dir.glob("*.pdf"), key=lambda item: item.name.casefold())
    if not pdfs:
        raise SystemExit(f"no PDF files found under {source_dir}")
    target_dir.mkdir(parents=True)
    for source in pdfs:
        shutil.copy2(source, target_dir / source.name)
    return [item.name for item in pdfs]


def _run_pipeline(settings: Settings, pipeline_id: str, cases) -> dict[str, object]:
    selected = select_pipeline(settings, pipeline_id)
    runtime = create_runtime(selected)
    embedder = E5Embedder(settings.embedding_model, settings.embedding_model_revision)
    store = ChromaVectorStore(settings.db_path, runtime.collection_name)

    started = perf_counter()
    summary = RuntimeIndexer(selected, runtime, embedder, store).ingest_all()
    ingest_seconds = perf_counter() - started
    inspection = inspect_index_for_cli(selected, store)
    document_results = [
        {"relative_path": item.relative_path, "state": item.state, "detail": item.detail}
        for item in summary.results
    ]
    if summary.failed or not inspection.health.usable:
        return {
            "pipeline_id": pipeline_id,
            "ingest_seconds": round(ingest_seconds, 3),
            "scanned": summary.scanned,
            "failed": summary.failed,
            "chunk_count": summary.collection_count,
            "health_usable": inspection.health.usable,
            "health_issues": [item.code for item in inspection.health.issues],
            "documents": document_results,
            "retrieval": {"status": "not_run", "cases": []},
            "answer_quality": {"status": "not_run", "reason": "live LLM disabled"},
        }

    retrieval_started = perf_counter()
    results = Evaluator(Retriever(selected, embedder, store)).evaluate_retrieval(cases)
    retrieval_seconds = perf_counter() - retrieval_started
    scored = [item for item in results if item.passed is not None]
    passed = sum(item.passed is True for item in scored)
    return {
        "pipeline_id": pipeline_id,
        "ingest_seconds": round(ingest_seconds, 3),
        "scanned": summary.scanned,
        "failed": summary.failed,
        "chunk_count": summary.collection_count,
        "health_usable": inspection.health.usable,
        "health_issues": [item.code for item in inspection.health.issues],
        "documents": document_results,
        "retrieval": {
            "status": "complete",
            "seconds": round(retrieval_seconds, 3),
            "passed": passed,
            "scored": len(scored),
            "pass_rate": round(passed / len(scored), 4) if scored else None,
            "cases": [
                {
                    "case_id": item.case_id,
                    "question": item.question,
                    "passed": item.passed,
                    "detail": item.detail,
                }
                for item in results
            ],
        },
        "answer_quality": {
            "status": "not_run",
            "reason": "live LLM evaluation is deliberately disabled in this script",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--documents", type=Path, default=Path("documents"))
    parser.add_argument("--eval", type=Path, default=Path("eval/questions.json"))
    parser.add_argument("--output", type=Path, default=Path("tmp/v1-v2-comparison"))
    args = parser.parse_args()

    source_dir = args.documents.resolve()
    root = args.output.resolve()
    if root.exists():
        raise SystemExit(f"refusing to overwrite existing comparison directory: {root}")
    root.mkdir(parents=True)
    pdf_names = _copy_inputs(source_dir, root / "documents")
    cases = load_evaluation_cases(args.eval.resolve())
    settings = _settings(root)
    pipelines = [_run_pipeline(settings, pipeline_id, cases) for pipeline_id in ("v1", "v2")]
    report = {
        "schema_version": "v1_v2_retrieval_comparison_v1",
        "documents": pdf_names,
        "evaluation_case_count": len(cases),
        "fixed_parameters": {
            "embedding_model": settings.embedding_model,
            "embedding_revision": settings.embedding_model_revision,
            "top_k": settings.top_k,
            "max_input_tokens": settings.v2_max_input_tokens,
            "live_llm": False,
        },
        "pipelines": pipelines,
        "interpretation": (
            "This report compares retrieval evidence only. Functional acceptance and "
            "answer-quality conclusions must be reported separately."
        ),
    }
    report_path = root / "comparison.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    print(f"report={report_path.as_posix()}")
    return 0 if all(item["failed"] == 0 for item in pipelines) else 1


if __name__ == "__main__":
    raise SystemExit(main())
