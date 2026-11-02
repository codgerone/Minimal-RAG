from __future__ import annotations

from rag.reports.table_compare import contrast_counts, per_table_cost


def question(ranks: dict[str, int | None], k: int | None, tokens: int | None) -> dict:
    return {"groups": [{"group_id": g, "chunk_rank": r} for g, r in ranks.items()], "k": k, "tokens": tokens, "lower": 1}


def test_metric_1_counts_each_question_once():
    before = {"q1": question({"e1": 1, "e2": 1, "e3": 1}, 1, 100),     # one chunk drops for three groups
              "q2": question({"e1": 3, "e2": 5}, 5, 900),
              "q3": question({"e1": 2, "e2": 4}, 4, 800)}
    after = {"q1": question({"e1": 2, "e2": 2, "e3": 2}, 2, 300),
             "q2": question({"e1": 2, "e2": 5}, 5, 950),
             "q3": question({"e1": 1, "e2": 6}, 6, 1000)}
    counts = contrast_counts(before, after)
    assert counts["m1"] == {"better": 1, "worse": 1, "mixed": 1, "floor": 1}
    assert counts["m2"] == {"better": 0, "worse": 2}
    assert counts["m3"] == {"better": 0, "worse": 3, "same_k": 1}    # q2: same units, longer text


def test_never_covered_counts_as_worst():
    counts = contrast_counts({"q": question({"e1": 4}, 4, 500)}, {"q": question({"e1": None}, None, None)})
    assert counts["m1"]["worse"] == 1 and counts["m2"]["worse"] == 1 and counts["m3"]["worse"] == 1


def test_cost_is_per_table_and_per_call():
    run = {"ingest_cost": {"tables": 4, "calls": 5, "retries": 1, "degraded_tables": 0, "cost_usd": 0.02, "seconds": 10.0}}
    cost = per_table_cost(run)
    assert cost["usd"] == 0.005 and cost["seconds"] == 2.5 and cost["calls"] == 1.25
    assert cost["usd_call"] == 0.004 and cost["seconds_call"] == 2.0
    assert per_table_cost({"ingest_cost": None}) is None


def test_budgets_of_one_query_execution_are_shown_once():
    from rag.reports.table_compare import one_per_execution
    budgets = {"3": "a_k3", "6": "a_k6"}
    runs = [("a_k3", {"budget_runs": budgets}), ("a_k6", {"budget_runs": budgets}), ("b", {})]
    assert [name for name, _ in one_per_execution(runs)] == ["a_k3", "b"]
