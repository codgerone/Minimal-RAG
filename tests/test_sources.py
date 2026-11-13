"""Document discovery by file type, and folder names."""

from rag.ingest.sources import discover, unsupported
from rag.paths import readable_name


def test_pdf_and_word_are_discovered_other_types_are_listed_as_unsupported(tmp_path):
    for name in ("a.pdf", "b.DOCX", "c.xlsx", "d.doc", "~$b.docx", "documents.toml"):
        (tmp_path / name).write_bytes(b"x")
    found = discover(tmp_path)
    assert [(s.relative_path, s.file_type) for s in found] == [("a.pdf", "pdf"), ("b.DOCX", "docx")]
    assert unsupported(tmp_path) == ("c.xlsx", "d.doc")


def test_word_folder_names_end_in_their_type_so_same_named_files_differ():
    assert readable_name("x/ORDER 1.pdf") == "x__ORDER 1"
    assert readable_name("ORDER 1.docx") == "ORDER 1-docx"
    assert readable_name("Bases .docx") == "Bases-docx"
