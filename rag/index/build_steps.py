"""Build settings per document: for each step the document passes through, its settings, rules
version and library versions. A document is out of date exactly when this record differs from
the one it was built with; steps it does not pass through never make it out of date.

Which steps a document passes through follows from two facts recorded when it was built:
whether it has scanned pages (OCR) and whether the parser found tables (table steps). Both are
decided by steps that every PDF passes through (page classification, parsing), so whenever
either fact could change, the document is already out of date for that step.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rag.registry import Assembly

STEPS = ("pages", "ocr", "parse", "table_extraction", "table_text", "compose", "chunk", "embed", "lexical")
STEP_LABELS = {"pages": "页面判定", "ocr": "OCR", "parse": "解析", "table_extraction": "表格提取与选优",
               "table_text": "表格处理", "compose": "组装", "chunk": "分块", "embed": "编码",
               "lexical": "BM25"}


@dataclass(frozen=True)
class DocumentFacts:
    scanned_pages: bool     # page classification found scanned pages
    tables: bool            # the parser found tables


def step_settings(assembly: "Assembly", facts: DocumentFacts | None = None) -> dict[str, Any]:
    """The steps a document with these facts passes through; facts None: every step of the config."""
    from rag.ingest import assemble
    from rag.ingest.libraries import versions
    from rag.ingest.ocr import pages
    from rag.ingest.tables import formatters, select
    config = assembly.config
    if facts is None:
        facts = DocumentFacts(True, assembly.parser.provides_table_slots)
    steps: dict[str, Any] = {"pages": {"rules": pages.RULES_VERSION, "libraries": versions("pymupdf")}}
    if assembly.ocr is not None and facts.scanned_pages:
        steps["ocr"] = assembly.ocr.settings()
    steps["parse"] = {**config.parser.as_dict(), "identity": assembly.parser.identity()}
    if facts.tables:
        steps["table_extraction"] = {
            "extractors": [{**component.as_dict(), "identity": extractor.identity()}
                           for component, extractor in zip(config.table_extractors, assembly.extractors)],
            "rules": select.RULES_VERSION, "libraries": versions("pymupdf")}
        steps["table_text"] = {**config.table_settings(), "rules": formatters.RULES_VERSION}
    steps["compose"] = {"rules": assemble.RULES_VERSION}
    steps["chunk"] = {**config.chunker.as_dict(), "identity": assembly.chunker.identity()}
    steps["embed"] = {**config.embedder.as_dict(), "identity": assembly.embedder.identity()}
    if assembly.lexical is not None:
        steps["lexical"] = {**assembly.lexical.settings(), "identity": assembly.lexical.identity()}
    return steps


def fingerprint(settings: dict[str, Any]) -> str:
    raw = json.dumps(settings, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def changed_steps(old: dict[str, Any], new: dict[str, Any]) -> tuple[str, ...]:
    """Steps whose record differs, including steps present on one side only, in pipeline order."""
    return tuple(step for step in STEPS if old.get(step) != new.get(step))
