import json

import pytest

from conftest import make_pdf
from rag.index import builder
from rag.index.builder import IngestError, current_status, ingest, open_store
from rag.index.manifest import Manifest

PAGES = ["Invoice total USD 968,000.00 for model HXF300.", "Delivery in five batches of 2,000 units."]


def states(workspace, assembly):
    return {d.relative_path: d.state for d in current_status(workspace, assembly).documents}


def test_incremental_ingest_skip_update_and_prune(workspace, fake_assembly):
    make_pdf(workspace.documents / "a.pdf", PAGES)
    make_pdf(workspace.documents / "sub" / "b.pdf", ["Payment 30 days after shipment."])
    first = ingest(workspace, fake_assembly)
    assert [o.action for o in first] == ["added", "added"]
    assert states(workspace, fake_assembly) == {"a.pdf": "current", "sub/b.pdf": "current"}
    assert [o.action for o in ingest(workspace, fake_assembly)] == ["skipped", "skipped"]

    make_pdf(workspace.documents / "a.pdf", PAGES + ["New page about warranty."])
    assert states(workspace, fake_assembly)["a.pdf"] == "changed"
    assert [o.action for o in ingest(workspace, fake_assembly)] == ["updated", "skipped"]

    (workspace.documents / "sub" / "b.pdf").unlink()
    assert states(workspace, fake_assembly)["sub/b.pdf"] == "missing"
    ingest(workspace, fake_assembly)                       # without --prune nothing is removed
    assert states(workspace, fake_assembly)["sub/b.pdf"] == "missing"
    assert [o.action for o in ingest(workspace, fake_assembly, prune=True)][-1] == "pruned"
    assert set(open_store(workspace, "fake").ids_by_document()) == {
        next(iter(Manifest.load(workspace.index_dir("fake") / "manifest.json").documents))}
    assert not (workspace.index_dir("fake") / "documents" / "sub__b").exists()


def test_vector_write_failure_leaves_document_flagged_and_repairable(workspace, fake_assembly, monkeypatch):
    make_pdf(workspace.documents / "a.pdf", PAGES)
    ingest(workspace, fake_assembly)
    make_pdf(workspace.documents / "a.pdf", PAGES + ["changed"])
    original = builder.ChromaStore.replace_document

    def crash_after_delete(self, document_id, chunks, vectors):
        self._open(create=True).delete(where={"document_id": document_id})
        raise RuntimeError("disk full")

    monkeypatch.setattr(builder.ChromaStore, "replace_document", crash_after_delete)
    outcome = ingest(workspace, fake_assembly)
    assert outcome[0].action == "failed" and "disk full" in outcome[0].error
    assert states(workspace, fake_assembly)["a.pdf"] == "changed"   # manifest still old
    monkeypatch.setattr(builder.ChromaStore, "replace_document", original)
    assert ingest(workspace, fake_assembly)[0].action == "updated"
    assert states(workspace, fake_assembly)["a.pdf"] == "current"


def test_missing_vectors_are_detected_as_incomplete(workspace, fake_assembly):
    make_pdf(workspace.documents / "a.pdf", PAGES)
    ingest(workspace, fake_assembly)
    open_store(workspace, "fake").delete_document(
        current_status(workspace, fake_assembly).documents[0].source.document_id)
    assert states(workspace, fake_assembly)["a.pdf"] == "incomplete"
    assert ingest(workspace, fake_assembly)[0].action == "updated"


def test_full_rebuild_failure_leaves_index_untouched(workspace, fake_assembly, monkeypatch):
    make_pdf(workspace.documents / "a.pdf", PAGES)
    make_pdf(workspace.documents / "b.pdf", ["Second document."])
    ingest(workspace, fake_assembly)
    manifest_before = (workspace.index_dir("fake") / "manifest.json").read_text(encoding="utf-8")
    count_before = open_store(workspace, "fake").count()
    real_prepare = builder.prepare

    def fail_on_b(source, assembly):
        if source.relative_path == "b.pdf":
            raise RuntimeError("parser crashed")
        return real_prepare(source, assembly)

    monkeypatch.setattr(builder, "prepare", fail_on_b)
    with pytest.raises(IngestError, match="现有索引未改动"):
        ingest(workspace, fake_assembly, force=True)
    assert (workspace.index_dir("fake") / "manifest.json").read_text(encoding="utf-8") == manifest_before
    assert open_store(workspace, "fake").count() == count_before
    assert not (workspace.index_dir("fake") / "documents.staging").exists()


def test_config_change_requires_full_rebuild(workspace, fake_assembly):
    make_pdf(workspace.documents / "a.pdf", PAGES)
    ingest(workspace, fake_assembly)
    path = workspace.index_dir("fake") / "manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["fingerprint"] = "old"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert current_status(workspace, fake_assembly).state == "config_changed"
    with pytest.raises(IngestError, match="--force"):
        ingest(workspace, fake_assembly)
    assert [o.action for o in ingest(workspace, fake_assembly, force=True)] == ["rebuilt"]
    assert current_status(workspace, fake_assembly).state == "ready"


def test_ingest_writes_readable_report_and_audit_folders(workspace, fake_assembly):
    make_pdf(workspace.documents / "sub" / "订单 A.pdf", PAGES)
    ingest(workspace, fake_assembly)
    assert (workspace.index_dir("fake") / "documents" / "sub__订单 A" / "chunks.json").is_file()
    report = workspace.ingest_reports("fake") / "sub__订单 A"
    assert (report / "1-解析.html").is_file() and (report / "3-分块.html").is_file()
    assert not (report / "2-表格.html").exists()


def test_full_rebuild_accepts_an_embedder_with_another_vector_dimension(workspace, fake_assembly):
    from dataclasses import replace

    class WiderEmbedder(type(fake_assembly.embedder)):
        def identity(self):
            return {"model": "wider"}

        def _vector(self, text):
            return super()._vector(text) + [0.0]

    make_pdf(workspace.documents / "a.pdf", PAGES)
    ingest(workspace, fake_assembly)
    wider = replace(fake_assembly, embedder=WiderEmbedder())
    assert [o.action for o in ingest(workspace, wider, force=True)] == ["rebuilt"]
    assert current_status(workspace, wider).state == "ready"
    assert len(open_store(workspace, "fake").query(WiderEmbedder().encode_query("page"), 1)) == 1
