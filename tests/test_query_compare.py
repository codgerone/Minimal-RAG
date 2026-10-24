import pytest

from rag.eval.query_compare import COMBOS, comparison_error, config_key, metrics_at


def test_config_key_follows_module_order_and_rejects_unknown_modules():
    assert config_key(["P3.3", "P3.1"]) == "P3.1+P3.3" and config_key([]) == ""
    with pytest.raises(ValueError):
        config_key(["P9"])


def test_left_must_be_a_proper_subset_of_right_unless_both_are_empty():
    assert comparison_error([], []) is None
    assert comparison_error([], ["P3.1"]) is None
    assert comparison_error(["P3.1"], ["P3.1", "P3.3"]) is None
    assert "右侧为空" in comparison_error(["P3.1"], [])
    assert "不在右侧" in comparison_error(["P3.2.1"], ["P3.2.2", "P3.3"])
    assert "至少多选" in comparison_error(["P3.1"], ["P3.1"])


def test_metrics_at_k_derive_from_needed_units_first_relevant_rank_and_tokens():
    facts = {"q1": {"needed": [1, 4], "first_relevant": 1, "tokens": [10, 20, 30, 40]},
             "q2": {"needed": [None], "first_relevant": 2, "tokens": [5, 5]},
             "q3": {"needed": [2], "first_relevant": 2, "tokens": [1, 1, 1]}}
    assert metrics_at(facts, 3) == {"hit": 2, "complete": 1, "mrr": (1 + 0.5 + 0.5) / 3,
                                    "tokens": (60 + 10 + 3) / 3}
    assert metrics_at(facts, 4)["complete"] == 2 and metrics_at(facts, 1)["hit"] == 1


def test_the_thirteen_standard_combinations():
    labels = [c.label for c in COMBOS]
    assert len(labels) == 13 == len(set(labels)) and labels[-1] == "S-T"
