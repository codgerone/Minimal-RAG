from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pytest

from rag.jsonio import from_jsonable, to_jsonable


@dataclass(frozen=True)
class Box:
    x: float
    y: float


@dataclass(frozen=True)
class Text:
    text: str
    box: Box | None


@dataclass(frozen=True)
class Table:
    rows: tuple[tuple[str, ...], ...]
    widths: dict[int, float]
    kind: Literal["grid", "list"] = "grid"


@dataclass(frozen=True)
class Page:
    nodes: tuple[Text | Table, ...]
    tags: frozenset[str]
    pair: tuple[int, str]


def test_round_trip_rebuilds_equal_objects():
    page = Page((Text("a", Box(1, 2.5)), Text("b", None), Table((("x", "y"),), {0: 3.0}, "list")),
                frozenset({"t"}), (1, "one"))
    assert from_jsonable(Page, to_jsonable(page)) == page


def test_union_member_is_chosen_by_field_names():
    node = from_jsonable(Text | Table, {"rows": [["a"]], "widths": {}})
    assert node == Table((("a",),), {})


def test_rejects_unknown_fields_and_literals():
    with pytest.raises(ValueError):
        from_jsonable(Box, {"x": 1, "y": 2, "z": 3})
    with pytest.raises(ValueError):
        from_jsonable(Table, {"rows": [], "widths": {}, "kind": "tree"})
