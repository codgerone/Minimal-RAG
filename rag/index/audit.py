"""A document's intermediate results under `.rag/indexes/<config>/documents/<folder>/`.

Ingest writes them once; the ingest review pages and evaluation read them back, so a page can be
rebuilt from what was ingested without processing the PDF again.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from rag.ingest.pipeline import ProcessedDocument
from rag.ingest.tables.models import (
    ContentResolution, ContinuationReport, GroupingReport, PreparedTableContent, ScoringReport,
    TableExtractionReport, TableSlot,
)
from rag.jsonio import from_jsonable, read_json, write_json
from rag.models import ChunkBatch, ParsedDocument


@dataclass(frozen=True)
class DocumentAudit:
    document: ParsedDocument
    table_slots: tuple[TableSlot, ...]
    extraction_reports: tuple[TableExtractionReport, ...]
    grouping: GroupingReport | None
    scoring: ScoringReport | None
    resolutions: tuple[ContentResolution, ...]
    continuations: ContinuationReport | None
    prepared_tables: tuple[PreparedTableContent, ...]
    batch: ChunkBatch


def write_audit(folder: Path, processed: ProcessedDocument, batch: ChunkBatch) -> None:
    if folder.exists():
        shutil.rmtree(folder)
    write_json(folder / "parse-native.json", processed.parse.native)
    write_json(folder / "parsed.json", processed.document)
    if processed.parse.primary.table_slots:
        write_json(folder / "tables.json", {
            "slots": processed.parse.primary.table_slots,
            "extraction_reports": processed.extraction_reports,
            "grouping": processed.grouping,
            "scoring": processed.scoring,
            "resolutions": processed.resolutions,
            "continuations": processed.continuations,
            "prepared_tables": processed.prepared_tables,
        })
    write_json(folder / "chunks.json", batch)


def load_audit(folder: Path) -> DocumentAudit:
    tables = read_json(folder / "tables.json") if (folder / "tables.json").is_file() else None

    def part(name: str, kind, empty):
        return from_jsonable(kind, tables[name]) if tables else empty
    return DocumentAudit(
        from_jsonable(ParsedDocument, read_json(folder / "parsed.json")),
        part("slots", tuple[TableSlot, ...], ()),
        part("extraction_reports", tuple[TableExtractionReport, ...], ()),
        part("grouping", GroupingReport | None, None),
        part("scoring", ScoringReport | None, None),
        part("resolutions", tuple[ContentResolution, ...], ()),
        part("continuations", ContinuationReport | None, None),
        part("prepared_tables", tuple[PreparedTableContent, ...], ()),
        from_jsonable(ChunkBatch, read_json(folder / "chunks.json")),
    )
