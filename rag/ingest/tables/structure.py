"""TableStructurer interface: decide the structure a table's text is written from.

`header_rules`: the rule header (rag/ingest/tables/header.py), no sections or implicit merges.
`llm_annotation`: an LLM annotation (rag/ingest/tables/llm_tables.py), the rule header when it fails.
"""

from __future__ import annotations

from typing import Protocol

from rag.ingest.tables.models import AdoptedTable, HeaderDecision, TableLLMResult, TableStructure
from rag.models import PrimaryDocument


class TableStructurer(Protocol):
    def structure(self, primary: PrimaryDocument, tables: tuple[AdoptedTable, ...],
                  language: str) -> tuple[tuple[TableStructure, TableLLMResult | None], ...]: ...


def rule_structure(header: HeaderDecision) -> TableStructure:
    if header.outcome != "identified":
        return TableStructure("header_rules", ())
    assert header.header_start_row is not None and header.header_end_row is not None
    return TableStructure("header_rules", tuple(range(header.header_start_row, header.header_end_row)))


class HeaderRulesStructure:
    def structure(self, primary: PrimaryDocument, tables: tuple[AdoptedTable, ...],
                  language: str) -> tuple[tuple[TableStructure, TableLLMResult | None], ...]:
        return tuple((rule_structure(item.header_decision), None) for item in tables)
