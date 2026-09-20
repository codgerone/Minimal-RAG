from pathlib import Path

import pymupdf
from docling_core.types.doc import BoundingBox, DocItemLabel, DoclingDocument, ProvenanceItem, Size

from rag.build_config import (
    DocumentTextConfig, EmbeddingConfig, TableHeaderConfig, TableSelectionConfig,
    TableSerializationConfig, TokenizerConfig, V2BuildConfig, V2ChunkerConfig,
    V2DependencyVersions, V2ParserConfig,
)
from rag.document_processor import V2DocumentProcessor
from rag.models import SourceDocument
from rag.pipeline_registry import build_config_fingerprint
from rag.v2.table_extraction import StrategyAdapter


class Counter:
    def count_passage(self, text: str) -> int:
        return len(text) + 3

    def count_text(self, text: str) -> int:
        return len(text)


def _config() -> V2BuildConfig:
    return V2BuildConfig(
        V2ParserConfig(), TableSelectionConfig(), TableHeaderConfig(), TableSerializationConfig(),
        DocumentTextConfig(), V2ChunkerConfig(128, 16), TokenizerConfig("model", "revision"),
        EmbeddingConfig("model", "revision", 3),
        V2DependencyVersions("1", "1", "2.121.0", "2.92.0", "1", "1", "1", "1"),
    )


def test_v2_processor_runs_layout_to_staged_build_without_using_v1(tmp_path: Path) -> None:
    pdf_path = tmp_path / "签字-订单.pdf"
    pdf = pymupdf.open()
    page = pdf.new_page(width=100, height=100)
    page.insert_text((10, 20), "Hello")
    pdf.save(pdf_path)
    pdf.close()

    raw = DoclingDocument(name="source")
    raw.add_page(1, Size(width=100, height=100))
    raw.add_text(DocItemLabel.TEXT, "Hello", prov=ProvenanceItem(
        page_no=1, charspan=(0, 5), bbox=BoundingBox(l=10, t=10, r=50, b=30)))
    empty_adapters = {}
    for tool, strategies in {
        "pymupdf": ("lines", "lines_strict", "text"),
        "camelot": ("lattice", "stream", "network", "hybrid"),
        "unstructured": ("hi_res",),
    }.items():
        for strategy in strategies:
            empty_adapters[(tool, strategy)] = StrategyAdapter(
                tool, strategy, lambda _path, _page: ())
    config = _config()
    converted_paths = []
    processor = V2DocumentProcessor(
        build_config=config, build_config_fingerprint=build_config_fingerprint(config),
        artifacts_root=tmp_path / "artifacts", diagnostics_enabled=False,
        token_counter=Counter(), docling_converter=lambda path: (converted_paths.append(path) or raw),
        extra_adapters=empty_adapters,
    )
    source = SourceDocument("doc", "签字-订单.pdf", "documents/签字-订单.pdf", pdf_path.resolve(), "hash")
    result = processor.process(source, "build")

    assert result.pipeline_id == "v2"
    assert [chunk.text for chunk in result.chunks] == ["Hello"]
    assert result.artifact_stage is not None
    assert result.artifact_stage.parsed_document_path.is_file()
    assert result.artifact_stage.winner_review_path.is_file()
    assert converted_paths == [source.absolute_path]
