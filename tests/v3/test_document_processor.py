import hashlib
from pathlib import Path

import fitz

from rag.v3.adapters.processor_plugins import PlainPageMainParser
from rag.v3.application.document_processor import DocumentProcessor
from rag.v3.contracts.documents import SourceDocument
from rag.v3.contracts.processing import DocumentRequest
from rag.v3.contracts.storage import IndexIdentity


def test_plain_processor_produces_complete_public_result(tmp_path: Path) -> None:
    path = tmp_path / "plain.pdf"
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((40, 40), "hello world")
        pdf.save(path)
    source = SourceDocument("doc", path.name, path.name, path,
                            hashlib.sha256(path.read_bytes()).hexdigest())
    identity = IndexIdentity("a" * 64, "index_store_v3", "rag_v3_" + "a" * 32,
                             ".rag/system-v3/indexes/" + "a" * 64)
    result = DocumentProcessor(PlainPageMainParser()).process(
        DocumentRequest(source, identity, "build", False)
    )
    assert result.native_parser_evidence.format_id == "pymupdf_pages_v1"
    assert result.primary_document.elements[0].text == "hello world"
    assert result.parsed_document.nodes[0].text == "hello world"
    assert result.extraction_reports == result.resolutions == result.prepared_tables == ()
    assert result.grouping_report is result.scoring_report is None
