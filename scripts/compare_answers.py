"""Run resumable live V1/V2 answers against an existing comparison index."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from time import perf_counter

from rag.config import load_settings, select_pipeline
from rag.embeddings import E5Embedder
from rag.llm import OpenRouterClient
from rag.pipeline import RAGPipeline
from rag.retriever import Retriever
from rag.vector_store import ChromaVectorStore


def _load_cases(eval_dir: Path) -> list[dict[str, object]]:
    cases = []
    for source in sorted(eval_dir.glob("*.json"), key=lambda item: item.name.casefold()):
        raw = json.loads(source.read_text(encoding="utf-8"))
        for item in raw:
            cases.append({
                "case_id": f"{source.stem}:{item['id']}",
                "question": item.get("question") or item.get("question_zh"),
                "reference_answer": item.get("reference_answer"),
                "answerable": bool(item["answerable"]),
                "expected_documents": item["expected_documents"],
                "expected_pages": item["expected_pages"],
            })
    return cases


def _write(path: Path, report: dict[str, object]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index-root", type=Path, required=True)
    parser.add_argument("--eval-dir", type=Path, default=Path("eval"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    index_root = args.index_root.resolve()
    output = args.output.resolve()
    if output.exists() and not args.resume:
        raise SystemExit(f"refusing to overwrite existing answer report: {output}")

    base = load_settings(require_api_key=True)
    settings = replace(
        base,
        project_root=index_root,
        documents_dir=index_root / "documents",
        db_path=index_root / ".rag/system-v2/chroma",
        artifacts_path=index_root / ".rag/system-v2/artifacts",
        v1_chunk_size=700,
        v1_chunk_overlap=100,
        top_k=3,
    )
    cases = _load_cases(args.eval_dir.resolve())
    if output.exists():
        report = json.loads(output.read_text(encoding="utf-8"))
        report["results"] = [
            item for item in report["results"] if item["status"] == "complete"
        ]
    else:
        report = {
            "schema_version": "v1_v2_live_answers_v1",
            "model": settings.openrouter_model,
            "temperature": 0,
            "top_k": settings.top_k,
            "index_root": index_root.as_posix(),
            "results": [],
        }
    completed = {(item["pipeline_id"], item["case_id"]) for item in report["results"]}

    for pipeline_id in ("v1", "v2"):
        selected = select_pipeline(settings, pipeline_id)
        embedder = E5Embedder(settings.embedding_model, settings.embedding_model_revision)
        store = ChromaVectorStore(settings.db_path, selected.collection_name)
        pipeline = RAGPipeline(
            Retriever(selected, embedder, store), OpenRouterClient(selected)
        )
        for case in cases:
            key = (pipeline_id, case["case_id"])
            if key in completed:
                continue
            started = perf_counter()
            try:
                answer = pipeline.ask(str(case["question"]))
                result = {
                    "pipeline_id": pipeline_id,
                    **case,
                    "status": "complete",
                    "seconds": round(perf_counter() - started, 3),
                    "answer": answer.answer,
                    "hits": [
                        {
                            "chunk_id": hit.chunk_id,
                            "document": hit.document_name,
                            "pages": (
                                list(hit.page_numbers)
                                if hasattr(hit, "page_numbers")
                                else [hit.page_number]
                            ),
                            "similarity": hit.similarity,
                        }
                        for hit in answer.hits
                    ],
                }
            except Exception as exc:
                result = {
                    "pipeline_id": pipeline_id,
                    **case,
                    "status": "error",
                    "seconds": round(perf_counter() - started, 3),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            report["results"].append(result)
            _write(output, report)
            print(f"{pipeline_id} {case['case_id']} {result['status']}", flush=True)

    failures = [item for item in report["results"] if item["status"] != "complete"]
    print(f"report={output.as_posix()} results={len(report['results'])} failures={len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
