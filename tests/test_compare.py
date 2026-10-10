import pytest

from rag.jsonio import read_json, write_json
from rag.reports.compare import CompareError, _changes, render, write_comparison

METRIC_KEYS = ("hit_rate", "complete_coverage", "group_recall", "mrr", "chunk_precision", "cross_document")


def hit(cid, relevant):
    return {"rank": 1, "chunk_id": cid, "relevant": relevant, "similarity": 0.8, "pages": [1],
            "text": "t", "document_name": "D"}


def run(config, model, statuses, version="3.1.0", irrelevant="z"):
    """statuses: case_id → miss / partial / complete."""
    cases = [{"case_id": cid, "document_id": "d", "document_name": "D", "question": "q",
              "answerable": True, "hit": s != "miss", "complete": s == "complete",
              "first_relevant_rank": None if s == "miss" else 1,
              "hits": [hit(irrelevant, False)]} for cid, s in statuses.items()]
    return {"config": config, "top_k": 3, "dataset": {"version": version},
            "build_settings": {"parser": config, "embedder": {"use": model},
                               "embedder_identity": {"model": f"org/{model}"}},
            "metrics": {k: {"value": 0.5, "numerator": 1, "denominator": 2} for k in METRIC_KEYS},
            "cases": cases}


def test_status_moves_are_counted_against_the_baseline():
    base = run("structured", "small", {"q1": "miss", "q2": "complete", "q3": "partial"})
    other = run("structured", "large", {"q1": "partial", "q2": "partial", "q3": "partial"})
    assert _changes(base, other) == {"q1": 1, "q2": -1, "q3": 0}


def test_runs_differing_only_in_embedder_share_a_tab_and_variants_are_labelled():
    page = render("x", [("a", run("structured", "small", {"q1": "miss"})),
                        ("b", run("structured", "large", {"q1": "complete"})),
                        ("c", run("structured", "small", {"q1": "miss"}, version="3.1.0+translated")),
                        ("d", run("plain_text", "small", {"q1": "miss"}))])
    assert page.count('data-tab="') == 2
    assert "small · translated" in page and "large" in page


def test_irrelevant_chunks_recurring_in_three_questions_are_listed():
    page = render("x", [("a", run("structured", "small", dict.fromkeys(["q1", "q2", "q3"], "miss"),
                                  irrelevant="hub-chunk"))])
    assert "3 题" in page and "hub-chunk" in page


def test_comparison_is_saved_for_rerendering_and_rejects_different_questions(workspace):
    write_json(workspace.eval_reports() / "a" / "result.json", run("structured", "small", {"q1": "miss"}))
    write_json(workspace.eval_reports() / "b" / "result.json", run("structured", "large", {"q1": "partial"}))
    write_json(workspace.eval_reports() / "c" / "result.json", run("structured", "large", {"q9": "partial"}))
    folder = write_comparison(workspace, "v", ["a", "b"])
    assert read_json(folder / "runs.json") == {"name": "v", "runs": ["a", "b"]}
    assert (folder / "index.html").is_file()
    with pytest.raises(CompareError, match="题目"):
        write_comparison(workspace, "w", ["a", "c"])
