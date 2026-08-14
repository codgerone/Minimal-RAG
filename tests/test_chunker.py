from rag.chunker import chunk_pages, make_splitter
from rag.models import PageText


def _page(document_id: str, page_number: int, text: str) -> PageText:
    return PageText(
        document_id=document_id,
        document_name=f"{document_id}.pdf",
        relative_path=f"{document_id}.pdf",
        page_number=page_number,
        text=text,
    )


def test_chunks_never_cross_pages_and_indices_reset() -> None:
    splitter = make_splitter(chunk_size=20, chunk_overlap=5)
    pages = [
        _page("doc", 1, "First sentence. Second sentence."),
        _page("doc", 2, "Third sentence. Fourth sentence."),
    ]

    chunks = chunk_pages(pages, "hash", splitter)

    assert {chunk.page_number for chunk in chunks} == {1, 2}
    assert [c.chunk_index for c in chunks if c.page_number == 1][0] == 0
    assert [c.chunk_index for c in chunks if c.page_number == 2][0] == 0
    assert all("-p1-" in c.chunk_id for c in chunks if c.page_number == 1)
    assert all("-p2-" in c.chunk_id for c in chunks if c.page_number == 2)


def test_chunking_is_stable_and_has_no_blank_or_adjacent_duplicates() -> None:
    splitter = make_splitter(chunk_size=12, chunk_overlap=3)
    pages = [_page("doc", 1, "Alpha beta gamma delta epsilon")]

    first = chunk_pages(pages, "hash", splitter)
    second = chunk_pages(pages, "hash", splitter)

    assert first == second
    assert all(chunk.text.strip() for chunk in first)
    assert all(a.text != b.text for a, b in zip(first, first[1:]))

