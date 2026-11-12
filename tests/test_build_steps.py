"""A document's build record holds exactly the steps it passes through, so a change to one step
marks only the documents passing through that step as out of date."""

from dataclasses import replace
from pathlib import Path

import pytest

from rag.config import Component, load_config
from rag.index.build_steps import DocumentFacts, changed_steps, fingerprint, step_settings
from rag.registry import assemble

ROOT = Path(__file__).resolve().parents[1]
KINDS = {"digital": DocumentFacts(False, False), "digital_tables": DocumentFacts(False, True),
         "scanned": DocumentFacts(True, False), "scanned_tables": DocumentFacts(True, True)}


@pytest.fixture(scope="module")
def structured():
    return assemble(load_config(ROOT / "configs" / "structured.toml"))


def stale_after(before, after) -> dict[str, tuple[str, ...]]:
    """Per kind of document: the steps whose record differs between the two assemblies."""
    return {kind: changed_steps(step_settings(before, facts), step_settings(after, facts))
            for kind, facts in KINDS.items()}


def test_steps_follow_the_document_facts(structured):
    assert list(step_settings(structured, KINDS["digital"])) == ["pages", "parse", "compose", "chunk", "embed", "lexical"]
    assert list(step_settings(structured, KINDS["scanned_tables"])) == [
        "pages", "ocr", "parse", "table_extraction", "table_text", "compose", "chunk", "embed", "lexical"]
    assert list(step_settings(structured)) == list(step_settings(structured, KINDS["scanned_tables"]))


def test_table_settings_reach_only_documents_with_tables(structured):
    config = replace(structured.config, table_formatter=Component("labeled_rows"))
    assert stale_after(structured, replace(structured, config=config)) == {
        "digital": (), "digital_tables": ("table_text",), "scanned": (), "scanned_tables": ("table_text",)}


def test_ocr_settings_reach_only_documents_with_scanned_pages(structured):
    from rag.ingest.ocr.step import OcrStep
    ocr = OcrStep(structured.ocr.use, structured.ocr.engine, 200, structured.ocr.root)
    assert stale_after(structured, replace(structured, ocr=ocr)) == {
        "digital": (), "digital_tables": (), "scanned": ("ocr",), "scanned_tables": ("ocr",)}


def test_common_settings_reach_every_document(structured):
    config = replace(structured.config, chunker=Component("structured_tokens", {"max_tokens": 480}))
    assert set(stale_after(structured, replace(structured, config=config)).values()) == {("chunk",)}


@pytest.mark.parametrize("module, step, kinds", [
    ("rag.ingest.ocr.pages", "pages", set(KINDS)),
    ("rag.ingest.tables.select", "table_extraction", {"digital_tables", "scanned_tables"}),
    ("rag.ingest.tables.formatters", "table_text", {"digital_tables", "scanned_tables"}),
    ("rag.ingest.assemble", "compose", set(KINDS)),
    ("rag.ingest.chunkers", "chunk", set(KINDS)),
    ("rag.index.analyzer", "lexical", set(KINDS)),
    ("rag.ingest.parsers.docling_layout", "parse", set(KINDS)),
    ("rag.ingest.tables.camelot_tables", "table_extraction", {"digital_tables", "scanned_tables"}),
    ("rag.ingest.ocr.step", "ocr", {"scanned", "scanned_tables"}),
])
def test_a_rules_bump_reaches_only_the_documents_passing_through_its_step(structured, monkeypatch, module, step, kinds):
    import importlib
    before = {kind: step_settings(structured, facts) for kind, facts in KINDS.items()}
    monkeypatch.setattr(importlib.import_module(module), "RULES_VERSION", "v9.9")
    after = {kind: step_settings(structured, facts) for kind, facts in KINDS.items()}
    assert {kind for kind in KINDS if changed_steps(before[kind], after[kind])} == kinds
    assert all(changed_steps(before[kind], after[kind]) in ((), (step,)) for kind in KINDS)


@pytest.mark.parametrize("library, steps", [
    ("unstructured", {"table_extraction"}),
    ("torch", {"embed"}),
    ("snowballstemmer", {"lexical"}),
    ("docling", {"parse", "table_extraction"}),     # the parser and the Docling table extractor
    ("onnxruntime", {"ocr"}),
])
def test_a_library_upgrade_reaches_the_steps_that_call_it(structured, monkeypatch, library, steps):
    from rag.ingest import libraries
    facts = KINDS["scanned_tables"]
    before = step_settings(structured, facts)
    real = libraries._version
    monkeypatch.setattr(libraries, "_version", lambda name: "99.0" if name == library else real(name))
    assert set(changed_steps(before, step_settings(structured, facts))) == steps


def test_query_settings_leave_every_record_unchanged(structured):
    config = replace(structured.config, top_k=9, bm25_weight=0.5, document_filter=False,
                     table_return_unit="chunk", query_mode="multi_round")
    assert all(fingerprint(step_settings(structured, facts)) ==
               fingerprint(step_settings(replace(structured, config=config), facts)) for facts in KINDS.values())
