from pathlib import Path

import pytest

from rag.models import SourceDocument
from rag.v2.document_models import (
    LayoutDocument, LayoutList, LayoutListItem, LayoutTablePlaceholder,
)
from rag.v2.table_selection import TableSlot


def test_nested_list_parent_must_precede_child() -> None:
    with pytest.raises(ValueError, match="更早的父项"):
        LayoutList("node", ("#/texts/1",), (
            LayoutListItem("child", "#/texts/1", "child", 1, None, "parent", ()),
        ), ())


def test_layout_slots_and_placeholders_are_one_to_one_in_reading_order() -> None:
    slot = TableSlot("slot_001", "#/tables/0", None, None, None, None,
                     "deferred", "missing_slot_provenance", ())
    placeholder = LayoutTablePlaceholder("node", "#/tables/0", "slot_001", ())
    document = LayoutDocument("doc", 1, (placeholder,), (slot,), ())
    assert document.table_slots == (slot,)

    with pytest.raises(ValueError, match="placeholder"):
        LayoutDocument("doc", 1, (), (slot,), ())
