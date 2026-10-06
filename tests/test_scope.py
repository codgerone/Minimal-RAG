from pathlib import Path

import pytest

from conftest import make_pdf
from rag.config import ConfigError
from rag.index.builder import ingest, open_store
from rag.ingest.sources import discover
from rag.query.retriever import SemanticRetriever
from rag.query.scope import CATALOG_FILE, describe, load_catalog, resolve_scope


def catalog_file(documents: Path, body: str) -> None:
    (documents / CATALOG_FILE).write_text(body, encoding="utf-8")


def entry(file: str, *codes: str) -> str:
    quoted = ", ".join(f'"{c}"' for c in codes)
    return f'[[document]]\nfile = "{file}"\ncodes = [{quoted}]\n'


@pytest.fixture
def three_pdfs(workspace):
    make_pdf(workspace.documents / "a.pdf", ["Invoice total USD 968,000.00 for model HXF300."])
    make_pdf(workspace.documents / "b.pdf", ["Delivery in five batches of 2,000 units."])
    make_pdf(workspace.documents / "sub" / "c.pdf", ["Payment 30 days after shipment."])
    return discover(workspace.documents)


def ids(sources, *paths):
    return tuple(sorted(s.document_id for s in sources if s.relative_path in paths))


def test_codes_match_after_normalization_and_select_every_named_document(workspace, three_pdfs):
    catalog_file(workspace.documents, entry("a.pdf", "INVOICE-E001-602") + entry("sub/c.pdf", "ORDER2025001"))
    catalog = load_catalog(workspace.documents, three_pdfs)
    scope = resolve_scope("发票 invoice e001 602 的金额？", catalog, True)
    assert (scope.kind, scope.document_ids, scope.codes) == (
        "identified", ids(three_pdfs, "a.pdf"), ("INVOICE-E001-602",))
    both = resolve_scope("ＯＲＤＥＲ２０２５００１（全角）与 INVOICE-E001-602 对比", catalog, True)
    assert both.document_ids == ids(three_pdfs, "a.pdf", "sub/c.pdf")


def test_scope_kinds_are_distinguished(workspace, three_pdfs):
    catalog_file(workspace.documents, entry("a.pdf", "CONT2025001"))
    catalog = load_catalog(workspace.documents, three_pdfs)
    user = three_pdfs[1].document_id
    assert resolve_scope("CONT2025001", catalog, True, user).document_ids == (user,)
    assert resolve_scope("CONT2025001", catalog, True, user).kind == "user"
    assert resolve_scope("CONT2025001", catalog, False).kind == "disabled"
    assert resolve_scope("CONT2025001", None, True).kind == "no_catalog"
    unidentified = resolve_scope("合同总金额是多少？", catalog, True)
    assert (unidentified.kind, unidentified.document_ids) == ("unidentified", None)
    for kind_scope in (unidentified, resolve_scope("x", None, True), resolve_scope("x", catalog, False)):
        assert describe(kind_scope, {}).startswith("检索范围：全库（")


def test_catalog_reports_missing_and_unregistered_files(workspace, three_pdfs):
    catalog_file(workspace.documents, entry("a.pdf", "ORDER2025001") + entry("gone.pdf", "ORDER2025009"))
    catalog = load_catalog(workspace.documents, three_pdfs)
    assert catalog.missing_files == ("gone.pdf",)
    assert catalog.unregistered == ("b.pdf", "sub/c.pdf")
    assert resolve_scope("ORDER2025009", catalog, True).kind == "unidentified"


def test_no_catalog_file_means_none(workspace, three_pdfs):
    assert load_catalog(workspace.documents, three_pdfs) is None


@pytest.mark.parametrize("body, message", [
    (entry("a.pdf", "O-R-1"), "不足"),
    (entry("a.pdf", "ORDER2025001") + entry("b.pdf", "order-2025-001"), "同时登记"),
    (entry("a.pdf", "ORDER2025001") + entry("A.PDF", "ORDER2025002"), "登记了两次"),
    ('[[document]]\nfile = "a.pdf"\ncodes = []\n', "非空"),
    ('[other]\nx = 1\n', "只接受"),
])
def test_invalid_catalogs_are_rejected(workspace, three_pdfs, body, message):
    catalog_file(workspace.documents, body)
    with pytest.raises(ConfigError, match=message):
        load_catalog(workspace.documents, three_pdfs)


def test_retrieval_is_limited_to_the_document_set(workspace, fake_assembly, three_pdfs):
    ingest(workspace, fake_assembly)
    store = open_store(workspace, "fake")
    retriever = SemanticRetriever(fake_assembly.embedder, store)
    allowed = ids(three_pdfs, "b.pdf", "sub/c.pdf")
    hits = retriever.retrieve("invoice total", 3, allowed)
    assert hits and {h.chunk.document_id for h in hits} <= set(allowed)
    assert store.count(allowed) == len(store.list_chunks(allowed[0])) + len(store.list_chunks(allowed[1]))
    assert retriever.retrieve("invoice total", 3, ()) == []
    assert {h.chunk.document_id for h in retriever.retrieve("invoice total", 3)} >= set(ids(three_pdfs, "a.pdf"))
