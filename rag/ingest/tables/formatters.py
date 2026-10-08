"""TableFormatter interface: turn an adopted table plus header decision into chunkable text."""

from __future__ import annotations

from typing import Protocol

from rag.ingest.tables.formatter import serialize_table
from rag.ingest.tables.markdown_rows import serialize_markdown_rows
from rag.ingest.tables.models import HeaderDecision, SerializedTable, StructuredTable


class TableFormatter(Protocol):
    def format(self, table: StructuredTable, header: HeaderDecision, *,
               table_node_id: str | None = None) -> SerializedTable: ...


class RowTextFormatter:
    """`row_text_v1`: one line per row, `字段 = "值"` pairs; merged cells stated once."""

    def format(self, table: StructuredTable, header: HeaderDecision, *,
               table_node_id: str | None = None) -> SerializedTable:
        return serialize_table(table, header, table_node_id=table_node_id)


class MarkdownRowsFormatter:
    """`markdown_rows_v1`: header once, `| 值 | 值 |` rows, merged cells as `←` / `↑`."""

    def format(self, table: StructuredTable, header: HeaderDecision, *,
               table_node_id: str | None = None) -> SerializedTable:
        return serialize_markdown_rows(table, header, table_node_id=table_node_id)
