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

    def parse(self, source: SourceDocument) -> ParseResult: ...
