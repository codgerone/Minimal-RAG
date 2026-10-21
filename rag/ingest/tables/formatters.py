"""TableFormatter interface: write an adopted table as chunkable text from its structure."""

from __future__ import annotations

from typing import Protocol

from rag.ingest.tables.markdown_rows import serialize_markdown_rows
from rag.ingest.tables.models import SerializedTable, StructuredTable, TableStructure


class TableFormatter(Protocol):
    labeled: bool          # LLM annotations are validated against the form the formatter writes
    def format(self, table: StructuredTable, structure: TableStructure, *,
               table_node_id: str | None = None) -> SerializedTable: ...


class MarkdownRowsFormatter:
    """`| 值 | 值 |` rows under the header, merged cells as `←` / `↑`."""
    labeled = False

    def format(self, table: StructuredTable, structure: TableStructure, *,
               table_node_id: str | None = None) -> SerializedTable:
        return serialize_markdown_rows(table, structure, table_node_id=table_node_id)


class LabeledRowsFormatter:
    """As `markdown_rows`, but single-column values read `列名: 值`, no header line."""
    labeled = True

    def format(self, table: StructuredTable, structure: TableStructure, *,
               table_node_id: str | None = None) -> SerializedTable:
        return serialize_markdown_rows(table, structure, table_node_id=table_node_id, labeled=True)
