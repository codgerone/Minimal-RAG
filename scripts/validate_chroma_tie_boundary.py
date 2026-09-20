"""Validate Chroma's candidate stability when equal distances cross top-k."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import chromadb


IDS = tuple(f"chunk-{index:02d}" for index in range(8))
EMBEDDING = [1.0, 0.0, 0.0]
COLLECTION = "equal_distance_boundary"


def _query(path: Path) -> dict[str, list[str]]:
    collection = chromadb.PersistentClient(path=str(path)).get_collection(COLLECTION)
    return {
        str(k): collection.query(
            query_embeddings=[EMBEDDING],
            n_results=k,
            include=["distances"],
        )["ids"][0]
        for k in (1, 3, 5, 8)
    }


def _stable_query(path: Path, top_k: int) -> list[str]:
    collection = chromadb.PersistentClient(path=str(path)).get_collection(COLLECTION)
    available = collection.count()
    candidate_count = min(available, top_k + 1)
    while True:
        result = collection.query(
            query_embeddings=[EMBEDDING],
            n_results=candidate_count,
            include=["distances"],
        )
        ranked = sorted(
            zip(result["distances"][0], result["ids"][0]),
            key=lambda item: (item[0], item[1]),
        )
        if candidate_count >= available or ranked[top_k - 1][0] != ranked[-1][0]:
            return [chunk_id for _, chunk_id in ranked[:top_k]]
        candidate_count = min(available, max(candidate_count + 1, candidate_count * 2))


def _observation(path: Path) -> dict[str, dict[str, list[str]]]:
    return {
        "raw": _query(path),
        "stable": {str(k): _stable_query(path, k) for k in (1, 3, 5, 8)},
    }


def _child(path: Path) -> int:
    print(json.dumps(_observation(path), sort_keys=True))
    return 0


def _run() -> int:
    with tempfile.TemporaryDirectory(
        prefix="minimal-rag-chroma-ties-", ignore_cleanup_errors=True
    ) as raw:
        path = Path(raw)
        client = chromadb.PersistentClient(path=str(path))
        collection = client.create_collection(COLLECTION)
        collection.add(
            ids=list(IDS),
            embeddings=[EMBEDDING for _ in IDS],
            documents=[f"document {item}" for item in IDS],
        )

        in_process = [_observation(path) for _ in range(10)]
        command = [sys.executable, str(Path(__file__).resolve()), "--child", str(path)]
        cross_process = [
            json.loads(subprocess.check_output(command, text=True).strip())
            for _ in range(4)
        ]
        observations = in_process + cross_process
        reference = observations[0]["stable"]
        stable = all(item["stable"] == reference for item in observations[1:])
        raw_observations = {
            json.dumps(item["raw"], sort_keys=True): item["raw"] for item in observations
        }
        nested = all(
            reference[str(smaller)] == reference[str(larger)][:smaller]
            for smaller, larger in ((1, 3), (3, 5), (5, 8))
        )
        result = {
            "chromadb_version": chromadb.__version__,
            "candidate_ids": list(IDS),
            "query_counts": {
                "same_process": len(in_process),
                "independent_process": len(cross_process),
            },
            "raw_unique_observation_count": len(raw_observations),
            "raw_observed_results": list(raw_observations.values()),
            "stable_reference_results": reference,
            "adaptive_results_stable_across_queries": stable,
            "adaptive_top_k_results_are_nested": nested,
            "passed": stable and nested,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", type=Path)
    args = parser.parse_args()
    return _child(args.child) if args.child is not None else _run()


if __name__ == "__main__":
    raise SystemExit(main())
