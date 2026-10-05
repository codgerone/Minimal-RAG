"""Read completed V2 reports as immutable comparison facts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from rag.v3.application.assembly import canonical_json_bytes
from rag.v3.contracts.evaluation import (
    AggregateMetrics, AggregateScope, ComparisonDocumentIdentity, ComparisonQueryConfig,
    ComparisonRunSnapshot, GroundTruthIdentity, MetricValue,
)


class EvaluationHistoryError(ValueError):
    pass


_COUNTS = ("included_question_count", "returned_chunk_count", "relevant_chunk_count",
           "required_group_count", "covered_group_count", "cross_document_count")
_METRICS = tuple(name for name in AggregateMetrics.__dataclass_fields__ if name not in _COUNTS)


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise EvaluationHistoryError("historical JSON must be an object")
    return value


def _read_hashed(directory: Path, relative: str, expected: str) -> bytes:
    if (not relative or "\\" in relative or relative.startswith("/")
            or ".." in relative.split("/")):
        raise EvaluationHistoryError("unsafe historical file path")
    path = (directory / relative).resolve()
    if not path.is_relative_to(directory.resolve()) or path.is_symlink():
        raise EvaluationHistoryError("unsafe historical file path")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected:
        raise EvaluationHistoryError("historical file hash differs")
    return content


def _metrics(raw: dict) -> AggregateMetrics:
    if set(raw) != set(AggregateMetrics.__dataclass_fields__):
        raise EvaluationHistoryError("historical metric fields differ")
    counts = {name: raw[name] for name in _COUNTS}
    if any(type(value) is not int or value < 0 for value in counts.values()):
        raise EvaluationHistoryError("historical metric count invalid")
    metrics = {}
    for name in _METRICS:
        value = raw[name]
        if not isinstance(value, dict) or set(value) != {
                "status", "numerator", "denominator", "value"}:
            raise EvaluationHistoryError("historical metric value invalid")
        metrics[name] = MetricValue(**value)
    return AggregateMetrics(**counts, **metrics)


def read_v2_snapshot(workspace_root: Path, run_directory: Path) -> ComparisonRunSnapshot:
    root = workspace_root.resolve()
    expected_root = (root / "validation" / "retrieval" / "system-v2.0" / "runs").resolve()
    directory = run_directory.resolve()
    if directory.parent != expected_root or run_directory.is_symlink():
        raise EvaluationHistoryError("V2 run is outside historical root")
    try:
        run = _read_json(directory / "run.json")
        if (run.get("schema_version") != "evaluation_run_v1"
                or run.get("status") != "completed"
                or run.get("system_version") != "2.0"
                or run.get("pipeline_id") not in {"v1", "v2"}
                or run.get("run_id") not in directory.name):
            raise EvaluationHistoryError("V2 run marker invalid")
        config = canonical_json_bytes(run["build_config"])
        fingerprint = hashlib.sha256(config).hexdigest()
        if fingerprint != run["build_config_fingerprint"]:
            raise EvaluationHistoryError("V2 build fingerprint differs")
        aggregate = json.loads(_read_hashed(directory, run["aggregate_path"],
                                            run["aggregate_sha256"]))
        if (aggregate.get("schema_version") != "evaluation_aggregate_v1"
                or aggregate.get("run_id") != run["run_id"]):
            raise EvaluationHistoryError("V2 aggregate identity differs")
        metrics = _metrics(aggregate["overall"]["metrics"])
        documents = []
        for entry in run["documents"]:
            document = json.loads(_read_hashed(directory, entry["json_path"],
                                               entry["json_sha256"]))
            _read_hashed(directory, entry["html_path"], entry["html_sha256"])
            if (document["document_key"] != entry["document_key"]
                    or document["run_id"] != run["run_id"]):
                raise EvaluationHistoryError("V2 document identity differs")
            cases = tuple(sorted((item["case_id"], item["question"])
                                 for item in document["cases"]))
            if (len(set(item[0] for item in cases)) != len(cases)
                    or not all(item[0] and item[1] for item in cases)):
                raise EvaluationHistoryError("V2 case identity invalid")
            documents.append(ComparisonDocumentIdentity(
                document["document_id"], document["file_hash"], cases))
        documents.sort(key=lambda item: item.document_id)
        if (len({item.document_id for item in documents}) != len(documents)
                or len(aggregate["documents"]) != len(documents)):
            raise EvaluationHistoryError("V2 document set differs")
        scopes = tuple(sorted((AggregateScope("document", item["document_id"],
            item["answerable_count"], item["unanswerable_count"],
            item["unmappable_group_count"], _metrics(item["metrics"]))
            for item in aggregate["documents"]), key=lambda item: item.document_id))
        if tuple(item.document_id for item in scopes) != tuple(
                item.document_id for item in documents):
            raise EvaluationHistoryError("V2 aggregate PDF identities differ")
        query = ComparisonQueryConfig(**run["query_config"])
        ground = GroundTruthIdentity(**run["ground_truth"])
        return ComparisonRunSnapshot("2.0", run["run_id"],
            (directory / "run.json").relative_to(root).as_posix(),
            f'pipeline-{run["pipeline_id"]}', run["collection_name"],
            "legacy_build_config_v2", config,
            fingerprint, ground, tuple(documents), query,
            run["evaluation_protocol_version"],
            run["test_set"]["annotation_rule_version"], scopes, metrics,
            "validation/retrieval/system-v2.0/summary.md")
    except (OSError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, EvaluationHistoryError):
            raise
        raise EvaluationHistoryError("V2 completed run readback failed") from exc


def list_v2_snapshots(workspace_root: Path) -> tuple[ComparisonRunSnapshot, ...]:
    root = (workspace_root.resolve() / "validation" / "retrieval" / "system-v2.0"
            / "runs")
    if not root.is_dir():
        return ()
    return tuple(sorted((read_v2_snapshot(workspace_root, directory)
                         for directory in root.iterdir() if directory.is_dir()),
                        key=lambda item: item.run_id))
