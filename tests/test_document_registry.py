from pathlib import Path

import pytest

from rag.document_registry import (
    discover_documents,
    make_document_id,
    resolve_document_selector,
)
from rag.errors import DocumentSelectionError


def test_discovery_is_recursive_case_insensitive_and_stable(tmp_path: Path) -> None:
    documents = tmp_path / "documents"
    (documents / "b").mkdir(parents=True)
    (documents / "a.pdf").write_bytes(b"a")
    (documents / "b" / "B.PDF").write_bytes(b"b")
    (documents / "ignore.txt").write_text("ignore")
    (documents / ".hidden.pdf").write_bytes(b"hidden")
    (documents / "~$temp.pdf").write_bytes(b"temp")

    discovered = discover_documents(documents)

    assert [item.relative_path for item in discovered] == ["a.pdf", "b/B.PDF"]


def test_document_id_depends_on_path_not_content(tmp_path: Path) -> None:
    documents = tmp_path / "documents"
    documents.mkdir()
    pdf = documents / "Order.PDF"
    pdf.write_bytes(b"first")
    first = discover_documents(documents)[0]
    pdf.write_bytes(b"second")
    second = discover_documents(documents)[0]

    assert first.document_id == second.document_id
    assert first.file_hash != second.file_hash
    assert make_document_id("Order.PDF") == make_document_id("order.pdf")


def test_selector_supports_unique_basename_and_rejects_traversal(
    tmp_path: Path,
) -> None:
    documents = tmp_path / "documents"
    (documents / "nested").mkdir(parents=True)
    (documents / "nested" / "order.pdf").write_bytes(b"data")
    discovered = discover_documents(documents)

    assert resolve_document_selector("order.pdf", discovered).relative_path == (
        "nested/order.pdf"
    )
    with pytest.raises(DocumentSelectionError):
        resolve_document_selector("../order.pdf", discovered)


def test_ambiguous_basename_lists_candidates(tmp_path: Path) -> None:
    documents = tmp_path / "documents"
    (documents / "a").mkdir(parents=True)
    (documents / "b").mkdir()
    (documents / "a" / "order.pdf").write_bytes(b"a")
    (documents / "b" / "order.pdf").write_bytes(b"b")

    with pytest.raises(DocumentSelectionError, match="不唯一"):
        resolve_document_selector("order.pdf", discover_documents(documents))

