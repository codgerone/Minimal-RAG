"""Table evidence metrics: which excerpts are table excerpts, units needed, fewest units."""

from types import SimpleNamespace

from rag.eval import table_metrics as tm
from rag.eval.judge import GroupEvidence


def excerpt(*words):
    return SimpleNamespace(anchor=(("piece", tuple(words)),))


def test_an_excerpt_is_a_table_excerpt_when_every_word_lies_in_a_table_box():
    tables = [(2, (100.0, 100.0, 300.0, 200.0))]
    inside, outside = (2, (110, 110, 130, 120)), (2, (110, 250, 130, 260))
    assert tm.is_table_excerpt(excerpt(inside, inside), tables)
    assert not tm.is_table_excerpt(excerpt(inside, outside), tables)
    assert not tm.is_table_excerpt(excerpt((3, (110, 110, 130, 120))), tables)


def test_units_needed_is_the_largest_over_table_groups_and_none_when_one_is_never_covered():
    groups = [tm.TableGroup("g1", ("x1",), frozenset({"a"}), (frozenset({"a"}),)),
              tm.TableGroup("g2", ("x2",), frozenset({"c"}), (frozenset({"c", "d"}), frozenset({"e"})))]
    units = [("b",), ("a",), ("e",), ("c", "d")]
    assert tm.required_units(units, groups) == ({"g1": 2, "g2": 3}, 3)
    assert tm.required_units([("a",)], groups) == ({"g1": 1}, None)
    assert tm.chunk_rank(["b", "c", "a"], frozenset({"a", "c"})) == 2


def test_fewest_units_counts_the_return_units_a_choice_of_sets_spreads_over():
    groups = [GroupEvidence("g1", "coordinate", (frozenset({"a1", "a2"}), frozenset({"b"}))),
              GroupEvidence("g2", "coordinate", (frozenset({"a3"}),))]
    table_of = {"a1": "T", "a2": "T", "a3": "T"}
    assert tm.fewest_units(groups, lambda c: table_of.get(c, c)) == 1      # one whole table
    assert tm.fewest_units(groups, lambda c: c) == 2                       # chunks: b + a3
