import pytest

from rag.eval.query_compare import COMBOS, QueryConfig, evaluated_configs, metrics_at


def test_config_keys_are_readable_and_parse_back():
    assert QueryConfig().key == ""
    full = QueryConfig(True, "rr", True, ("P3.2.1", "P3.2.2"), True)
    assert full.key == "P3.1@rr-o_P3.2.1+P3.2.2@rrf" and QueryConfig.parse(full.key) == full
    assert QueryConfig(True).key == "P3.1" and not QueryConfig(True).merged
    assert QueryConfig(phrasings=("P3.2.3",), fuse=False).key == "P3.2.3"
    assert QueryConfig.parse("P3.1@rrf_P3.2.4") == QueryConfig(True, "rrf", False, ("P3.2.4",), False)
    stripped = QueryConfig(True, None, False, ("P3.2.2",), True, True)
    assert stripped.key == "P3.1_P3.2.0_P3.2.2@rrf" and QueryConfig.parse(stripped.key) == stripped
    assert QueryConfig.parse("P3.2.0") == QueryConfig(strip=True) and QueryConfig.parse("P3.2.0").settings().strip_codes
    with pytest.raises(ValueError):
        QueryConfig.parse("P3.2.2+P3.2.1@rrf")
    lang = QueryConfig(phrasings=("P3.2.2", "P3.2.4"), strip=True, languages=(("P3.2.4", "qd"),))
    assert lang.key == "P3.2.0_P3.2.2+P3.2.4~qd@rrf" and QueryConfig.parse(lang.key) == lang
    assert lang.settings().languages == {("step_back", "both")}
    assert QueryConfig.parse("P3.2.1~d").settings().languages == {("rewrite", "document")}
    for bad in ("P3.2.2~d", "P3.2.1~x", "P3.2.1~q"):
        with pytest.raises(ValueError):
            QueryConfig.parse(bad)


def test_invalid_configurations_are_rejected():
    for bad in (dict(merge="rr"), dict(decompose=True, original_route=True), dict(decompose=True, merge="x"),
                dict(phrasings=("P3.2.1", "P3.2.2"), fuse=False), dict(phrasings=("P3.3",))):
        with pytest.raises(ValueError):
            QueryConfig(**bad)


def test_metrics_at_k_derive_from_needed_units_and_the_unit_table_tokens():
    units = [{"tokens": t} for t in (10, 20, 30, 40, 5, 1)]
    facts = {"q1": {"n": [1, 4], "u": [0, 1, 2, 3]},
             "q2": {"n": [None], "u": [4, 4]},
             "q3": {"n": [2], "u": [5, 5, 5]}}
    assert metrics_at(facts, units, 3) == {"hit": 2, "complete": 1, "tokens": (60 + 10 + 3) / 3}
    assert metrics_at(facts, units, 4)["complete"] == 2 and metrics_at(facts, units, 1)["hit"] == 1


def test_the_384_evaluated_configurations_and_their_settings():
    configs = evaluated_configs()
    keys = [c.key for c in configs]
    assert len(configs) == 384 == len(set(keys)) and keys[0] == ""
    v314 = [c for c in configs if not c.languages]
    assert len(v314) == 240 and sum(not c.merged for c in v314) == 40
    assert sum(c.phrasings != () and not c.fuse for c in v314) == 48 and sum(c.strip for c in v314) == 120
    added = {c.key for c in configs if c.languages and c.strip and not c.decompose}
    assert added == {f"P3.2.0_{p}" for m in ("P3.2.1", "P3.2.4") for v in ("d", "qd")
                     for p in (f"{m}~{v}", f"{m}~{v}@rrf",
                               f"P3.2.1~{v}+P3.2.2@rrf" if m == "P3.2.1" else f"P3.2.2+P3.2.4~{v}@rrf")}
    settings = QueryConfig.parse("P3.1@rrf-o_P3.2.2+P3.2.3@rrf").settings()
    assert settings.merge == "rrf" and settings.original_route and settings.phrasings == {"translation", "hyde"}
    settings = QueryConfig.parse("P3.2.1").settings()
    assert settings.merge is None and not settings.fuse_phrasings and settings.phrasings == {"rewrite"}


def test_the_thirteen_standard_combinations():
    labels = [c.label for c in COMBOS]
    assert len(labels) == 13 == len(set(labels)) and labels[-1] == "S-T"


def test_completion_vectors_compare_by_dominance_and_report_the_leading_k_ranges():
    import math
    from rag.eval.query_compare import completion_vector, leads, verdict
    assert completion_vector([5, None, 2]) == [2, 5, math.inf]
    assert verdict([2, 3, 5], [1, 3, 5]) == "better" and verdict([1, 3], [1, 4]) == "worse"
    assert verdict([2, 3, 5], [1, 5, 6]) == "mixed" and verdict([1, 2], [1, 2]) == "same"
    assert verdict([1, math.inf], [1, 9]) == "better"
    assert leads([2, 3, 5], [1, 5, 6]) == {"left": [(3, 5)], "right": [(1, 1)]}
