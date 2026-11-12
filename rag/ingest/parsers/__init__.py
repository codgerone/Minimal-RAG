"""Parser interface: one PDF in, reading-order primary document out."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from rag.models import PrimaryDocument, SourceDocument


@dataclass(frozen=True)
class ParseResult:
    primary: PrimaryDocument
    native: dict[str, Any]        # raw tool output, saved for audit only
    shared: object | None = None  # per-document resource reused by table extractors


class Parser(Protocol):
    provides_table_slots: bool    # whether primary.table_slots can anchor table extraction

    def parse(self, source: SourceDocument, cache: Any = None) -> ParseResult:
        """`cache` (rag/index/step_cache.py StepCache) keeps the tool's raw output when the tool is
        expensive; parsers whose tool is cheap ignore it."""
        ...

    def identity(self) -> dict[str, Any]:
        """Rules version and library versions: what besides the config decides the output."""
        ...
