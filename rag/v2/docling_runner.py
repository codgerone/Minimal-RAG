"""Locked Docling conversion configuration for the V2 primary layout parse."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any


def convert_document(source_pdf: Path) -> Any:
    from docling.datamodel.base_models import DocumentStream, InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions()
    options.do_ocr = False
    options.do_table_structure = True
    options.table_structure_options.mode = TableFormerMode.ACCURATE
    options.table_structure_options.do_cell_matching = True
    options.generate_page_images = False
    options.do_formula_enrichment = False
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )
    # A byte stream avoids Docling or its PDF backend retaining a Windows file
    # handle after conversion. The filename remains ASCII so format detection is
    # independent of the original source path.
    stream = BytesIO(source_pdf.read_bytes())
    try:
        return converter.convert(DocumentStream(name="source.pdf", stream=stream)).document
    finally:
        stream.close()
