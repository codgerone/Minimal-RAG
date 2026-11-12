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


def test_runs_differing_in_rules_version_share_a_tab_and_tabs_stay_unique():
    old, new = run("structured", "small", {"q1": "partial"}), run("structured", "small", {"q1": "complete"})
    old["build_settings"]["rules_version"], new["build_settings"]["rules_version"] = "v3.7", "v3.10"
    other_budget = run("structured", "small", {"q1": "miss"})
    other_budget["build_settings"]["chunker"] = {"max_tokens": 480}
    page = render("x", [("a", old), ("b", new), ("c", other_budget)])
    assert page.count('data-tab="') == 2 and 'data-tab="structured_2"' in page
    assert "规则 v3.7 · small" in page and "规则 v3.10 · small" in page


def test_display_names_are_shown_and_kept_for_rerendering(workspace):
    write_json(workspace.eval_reports() / "a" / "result.json", run("structured", "small", {"q1": "miss"}))
    write_json(workspace.eval_reports() / "b" / "result.json", run("structured", "large", {"q1": "partial"}))
    folder = write_comparison(workspace, "v", ["a=V3.9", "b"])
    assert read_json(folder / "runs.json") == {"name": "v", "runs": ["a=V3.9", "b"]}
    page = (folder / "index.html").read_text(encoding="utf-8")
    assert "V3.9 · small" in page and "large" in page
    assert "display_name" not in read_json(workspace.eval_reports() / "a" / "result.json")


def test_tabs_differing_only_in_k_are_titled_by_k():
    runs = []
    for k in (3, 6):
        for name, model in (("a", "small"), ("b", "large")):
            result = run("structured", model, {"q1": "miss"})
            result["top_k"] = k
            runs.append((f"{name}{k}", result))
    page = render("x", runs)
    assert 'data-tab="k3"' in page and 'data-tab="k6"' in page and ">K=6</a>" in page


def test_improvement_and_regression_are_counted_against_the_baseline():
    from rag.reports.compare import _coverage_moves
    base = run("structured", "small", {"q1": "miss", "q2": "complete", "q3": "partial", "q4": "complete"})
    other = run("structured", "large", {"q1": "complete", "q2": "partial", "q3": "partial", "q4": "complete"})
    assert _coverage_moves(base, other) == ((1, 2), (1, 2))


def test_table_evidence_tab_can_be_left_out_and_is_saved(workspace):
    write_json(workspace.eval_reports() / "a" / "result.json", run("structured", "small", {"q1": "miss"}))
    folder = write_comparison(workspace, "v", ["a"], table_evidence=False)
    assert read_json(folder / "runs.json")["table_evidence"] is False


def with_types(result, types):
    """types: document_id → (type, label); each case's document is taken from its case_id prefix."""
    for case in result["cases"]:
        case["document_id"] = case["case_id"].split("-")[0]
    result["documents"] = {doc: {"document_name": doc, "document_type": kind, "questions": 1,
                                 "metrics": result["metrics"]} for doc, (kind, _) in types.items()}
    result["document_types"] = {kind: {"label": label, "documents": 1, "answerable": 1,
                                       "metrics": result["metrics"], "returned_tokens": {"value": 100}}
                                for kind, label in dict(types.values()).items()}
    return result


def test_changes_by_document_type_count_only_that_types_questions():
    from rag.reports.compare import _by_type
    types = {"dig": ("digital", "数字 PDF"), "scan": ("scanned", "扫描 PDF")}
    base = with_types(run("structured", "small", {"dig-q1": "miss", "scan-q1": "complete"}), types)
    other = with_types(run("structured", "small", {"dig-q1": "complete", "scan-q1": "complete"}), types)
    html = _by_type([("a", base), ("b", other)])
    digital, scanned = html.split("<h3>")[1:3]
    assert "数字 PDF" in digital and "↑1</span>" in digital
    assert "扫描 PDF" in scanned and "↑0</span>" in scanned


def test_runs_without_document_types_are_noted():
    from rag.reports.compare import _by_type
    assert "V4.1 之前" in _by_type([("a", run("structured", "small", {"q1": "miss"}))])


def test_results_recorded_per_build_step_group_with_earlier_flat_results():
    from rag.reports.compare import _group_key, _model
    parser, chunker = {"use": "docling_layout"}, {"use": "structured_tokens", "max_tokens": 512}
    extractor, ocr = {"use": "camelot"}, {"use": "rapidocr", "dpi": 300}
    flat = run("structured", "small", {"q1": "partial"})
    flat["build_settings"] = {
        "rules_version": "v3.13", "parser": parser, "table_extractors": [extractor], "chunker": chunker,
        "ocr": ocr, "ocr_identity": {"version": "3.9.2"}, "embedder": {"use": "small"},
        "embedder_identity": {"model": "org/small"}, "table_formatter": {"use": "markdown_rows"}}
    stepped = run("structured", "small", {"q1": "complete"})
    stepped["build_settings"] = {
        "pages": {"rules": "v4.1.1"},
        "ocr": {**ocr, "identity": {"version": "3.9.2"}, "rules": "v4.1.1", "libraries": {}},
        "parse": {**parser, "identity": {"rules": "v4.1.1"}},
        "table_extraction": {"extractors": [{**extractor, "identity": {}}], "rules": "v4.1.1"},
        "table_text": {"table_formatter": {"use": "labeled_rows"}, "rules": "v4.1.1"},
        "chunk": {**chunker, "identity": {"rules": "v4.1.1"}},
        "embed": {"use": "small", "identity": {"model": "org/small"}}}
    assert _group_key(flat) == _group_key(stepped)
    assert _model(stepped) == _model(flat).removeprefix("规则 v3.13 · ")
