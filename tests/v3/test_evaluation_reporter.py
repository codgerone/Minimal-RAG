import json
import os
import pickle
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from rag.v3.adapters.evaluation_history import list_v2_snapshots
from rag.v3.adapters.evaluation_run_store import LocalImmutableEvaluationRunStore
from rag.v3.adapters import evaluation_run_store
from rag.v3.application.assembly import (
    build_projection, builtin_configuration, index_identity,
)
from rag.v3.application.evaluation_metrics import EvidenceMetricCalculator
from rag.v3.application.evaluation_reporter import JsonHtmlEvaluationReporter
from rag.v3.contracts.evaluation import (
    BaselineSelection, BaselineSelectionEntry, CaseEvaluationFact, EvaluationDocumentResult,
    EvaluationQueryConfig, EvaluationRunRequest, GroundTruthIdentity,
    TestSetIdentity as SetIdentity,
)


ROOT = Path(__file__).resolve().parents[2]


def test_report_is_from_machine_facts_and_escapes_full_evidence(tmp_path, monkeypatch):
    configuration = builtin_configuration("plain_text")
    request = EvaluationRunRequest("run1", "plain_text", index_identity(configuration),
        build_projection(configuration), EvaluationQueryConfig(3, "query: ", None,
            "strip_v1", "ascending", "chunk_id_ascending", True),
        GroundTruthIdentity("2.0.0", "a" * 64),
        SetIdentity("3.0.0", "b" * 64, "evidence_chunk_mapping_v2"),
        "retrieval_evaluation_v2", "2026-09-26T00:00:00Z")
    fact = CaseEvaluationFact("q1", "completed", "<unsafe question>", False,
                              "No <answer>", (), (), (), None, None)
    document = EvaluationDocumentResult("evaluation_document_result_v3", "run1",
        "doc--id", "id", "Doc.pdf", "Doc.pdf", "c" * 64, (fact,))
    aggregate = EvidenceMetricCalculator().aggregate("run1", (document,))
    rendered = JsonHtmlEvaluationReporter().render(request, (document,), aggregate,
        list_v2_snapshots(ROOT), BaselineSelection("evaluation_baselines_v3", (), None))
    files = {(item.location, item.relative_path): item for item in rendered.files}
    assert len(files) == 6
    raw = files["run", "documents/doc--id.json"].content
    assert json.loads(raw)["cases"][0]["question"] == "<unsafe question>"
    page = files["run", "documents/doc--id.html"].content.decode("utf-8")
    assert "&lt;unsafe question&gt;" in page
    assert "<unsafe question>" not in page
    assert "No &lt;answer&gt;" in page
    assert "正式基线未选定" in files["system_version", "summary.md"].content.decode("utf-8")
    comparison = json.loads(files["comparison_root", "comparison.json"].content)
    assert len(comparison["runs"]) == 3
    assert comparison["runs"][-1]["run_id"] == "run1"
    assert files["run", "aggregate.json"].content == json.dumps(
        asdict(aggregate), ensure_ascii=False, separators=(",", ":"),
        sort_keys=True).encode("utf-8")
    store = LocalImmutableEvaluationRunStore(tmp_path)
    completed = store.publish(request, rendered)
    assert completed.status == "completed"
    run_root = tmp_path / "validation/retrieval/system-v3.0/runs"
    folder, = run_root.iterdir()
    assert json.loads((folder / "run.json").read_bytes())["status"] == "completed"
    assert (folder / "documents/doc--id.html").read_bytes() == files[
        "run", "documents/doc--id.html"].content
    assert not (tmp_path / "validation/retrieval/system-v3.0/publication-journal.json").exists()
    assert store.list_completed(request.ground_truth) == (completed,)
    assert len(store.list_comparison_runs()) == 1

    comparison_path = tmp_path / "validation/retrieval/comparison.json"
    summary_path = tmp_path / "validation/retrieval/system-v3.0/summary.md"
    before = (comparison_path.read_bytes(), summary_path.read_bytes())
    request2 = replace(request, run_id="run2")
    document2 = replace(document, run_id="run2")
    aggregate2 = EvidenceMetricCalculator().aggregate("run2", (document2,))
    selected = BaselineSelection("evaluation_baselines_v3", (
        BaselineSelectionEntry(request.ground_truth.ground_truth_fingerprint,
            request.evaluation_protocol_version, request.query_config,
            request.test_set.annotation_rule_version, "3.0", "run1",
            "2026-09-26T00:00:00Z"),), "2026-09-26T00:00:00Z")
    with pytest.raises(ValueError, match="clear the selected V3 baseline"):
        JsonHtmlEvaluationReporter().render(request2, (document2,), aggregate2,
            store.list_comparison_runs(), selected)
    rendered2 = JsonHtmlEvaluationReporter().render(request2, (document2,),
        aggregate2, store.list_comparison_runs(),
        BaselineSelection("evaluation_baselines_v3", (), None))
    original_atomic = evaluation_run_store._atomic
    failed_once = False

    def fail_version_once(path, content):
        nonlocal failed_once
        if path == comparison_path and not failed_once:
            failed_once = True
            raise OSError("injected comparison publication failure")
        original_atomic(path, content)

    monkeypatch.setattr(evaluation_run_store, "_atomic", fail_version_once)
    with pytest.raises(OSError, match="injected comparison"):
        store.publish(request2, rendered2)
    assert (comparison_path.read_bytes(), summary_path.read_bytes()) == before
    assert store.list_completed(request.ground_truth) == (completed,)
    failed = [path for path in run_root.iterdir() if "run2" in path.name]
    assert len(failed) == 1
    assert json.loads((failed[0] / "run.json").read_bytes())["status"] == "failed"
    assert not store.journal.exists()

    monkeypatch.undo()
    request3 = replace(request, run_id="run3")
    document3 = replace(document, run_id="run3")
    aggregate3 = EvidenceMetricCalculator().aggregate("run3", (document3,))
    rendered3 = JsonHtmlEvaluationReporter().render(request3, (document3,),
        aggregate3, store.list_comparison_runs(),
        BaselineSelection("evaluation_baselines_v3", (), None))
    payload = tmp_path / "crash_payload.pkl"
    payload.write_bytes(pickle.dumps((request3, rendered3)))
    crash_code = """
import os, pickle, sys
from pathlib import Path
from rag.v3.adapters import evaluation_run_store as module
request, rendered = pickle.loads(Path(sys.argv[2]).read_bytes())
original = module._atomic
target = Path(sys.argv[1]) / 'validation/retrieval/system-v3.0/summary.md'
def crash(path, content):
    original(path, content)
    if path == target:
        os._exit(73)
module._atomic = crash
module.LocalImmutableEvaluationRunStore(Path(sys.argv[1])).publish(request, rendered)
"""
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    crashed = subprocess.run([sys.executable, "-c", crash_code, str(tmp_path),
                              str(payload)], cwd=ROOT, env=env, check=False)
    assert crashed.returncode == 73
    assert store.journal.exists()
    recovery = subprocess.run([sys.executable, "-c",
        "import sys; from pathlib import Path; from rag.v3.adapters.evaluation_run_store "
        "import LocalImmutableEvaluationRunStore; "
        "LocalImmutableEvaluationRunStore(Path(sys.argv[1])).recover()",
        str(tmp_path)], cwd=ROOT, env=env, capture_output=True, text=True)
    assert recovery.returncode == 0, recovery.stderr
    assert (comparison_path.read_bytes(), summary_path.read_bytes()) == before
    assert not store.journal.exists()
    assert store.list_completed(request.ground_truth) == (completed,)
    failed3, = [path for path in run_root.iterdir() if "run3" in path.name]
    assert json.loads((failed3 / "run.json").read_bytes())["status"] == "failed"

    request4 = replace(request, run_id="run4")
    document4 = replace(document, run_id="run4")
    aggregate4 = EvidenceMetricCalculator().aggregate("run4", (document4,))
    rendered4 = JsonHtmlEvaluationReporter().render(request4, (document4,),
        aggregate4, store.list_comparison_runs(),
        BaselineSelection("evaluation_baselines_v3", (), None))
    assert [item["run_id"] for item in json.loads(next(file.content for file in
        rendered4.files if file.relative_path == "comparison.json"))["runs"]] == ["run4"]
    assert store.publish(request4, rendered4).status == "completed"
    assert [path.name for path in run_root.iterdir()] == [
        evaluation_run_store.run_directory_name(request4)]
    assert [item.run_id for item in store.list_comparison_runs()] == ["run4"]
