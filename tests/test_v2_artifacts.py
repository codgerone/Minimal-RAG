import html
import json
from pathlib import Path

from docling_core.types.doc import DoclingDocument, Size

from rag.models import ChunkSource, V2DocumentChunk
from rag.v2.artifacts import (
    SelectionWinnerSummary, TableSelectionSummary, _winner_html, stage_artifacts,
)
from rag.v2.document_models import ParsedDocument, StructuredTable, TableNode, TextNode
from rag.v2.table_header import HeaderDecision, HeaderPath
from rag.v2.table_models import TableCell
from rag.v2.table_serialization import SerializedTable, SerializedTableLine
from rag.v2.table_scoring import ScoringReport
from rag.v2.table_selection import GroupingReport


def test_stage_writes_and_verifies_required_envelopes_and_zero_winner_html(tmp_path: Path) -> None:
    raw = DoclingDocument(name="fixture")
    raw.add_page(1, Size(width=100, height=100))
    parsed = ParsedDocument("doc", "a.pdf", "documents/a.pdf", "filehash",
                            (TextNode("node", "paragraph", "正文", "#/texts/0", ()),), ())
    chunk = V2DocumentChunk("doc-v2-c000000", "doc", "v2", 0, "text", "正文", 5,
                            (ChunkSource("node", (), 0, 2, False, "none"),), None, 0, 1)
    grouping = GroupingReport((), (), (), (), (), ())
    scoring = ScoringReport("a.pdf", (), ())

    result = stage_artifacts(
        artifacts_root=tmp_path, raw_document=raw, parsed=parsed, chunks=(chunk,),
        grouping=grouping, scoring=scoring, build_id="build", build_config_fingerprint="fingerprint",
        diagnostics_enabled=True, created_at="2026-09-16T00:00:00Z",
    )

    assert result.winner_count == 0 and result.chunk_count == 1
    assert result.raw_docling_path.name == "raw-docling-document.json"
    envelope = json.loads(result.chunks_path.read_text(encoding="utf-8"))
    assert envelope["schema_version"] == "document_chunks_v1"
    assert envelope["build_id"] == "build"
    assert envelope["payload"]["chunks"][0]["chunk_id"] == "doc-v2-c000000"
    html = result.winner_review_path.read_text(encoding="utf-8")
    assert 'data-winner-count="0"' in html
    assert result.diagnostics_path.is_dir()


def test_winner_html_uses_actual_tables_chunks_sources_and_header_coloring() -> None:
    header = HeaderDecision("undetermined", "no_supported_candidate", (), (), (), None, None, ())
    line = SerializedTableLine("line", "header", "表头未确定。", (), ())
    serialized = SerializedTable(line.text, (line,))
    table = StructuredTable("table", "docling", "accurate", 1, 1, (), (), None, (), ())
    nodes = tuple(
        TableNode(f"node-{index}", f"slot-{index}", f"#/tables/{index}",
                  "selected_winner", table, f"group-{index}", header, serialized, ())
        for index in (1, 2)
    )
    parsed = ParsedDocument("doc", "a.pdf", "documents/a.pdf", "hash", nodes, ())
    chunks = tuple(
        V2DocumentChunk(f"chunk-{index}", "doc", "v2", index - 1, "table",
                        f"chunk text {index}", 10,
                        (ChunkSource(f"node-{index}", (), 0, len(line.text),
                                     index == 2, "table_header" if index == 2 else "none"),),
                        None, 0, 1)
        for index in (1, 2)
    )
    winners = tuple(
        SelectionWinnerSummary(f"group-{index}", f"slot-{index}", f"candidate-{index}",
                               "docling", "accurate", "highest_total_score", 1.0)
        for index in (1, 2)
    )
    html = _winner_html(parsed, chunks, TableSelectionSummary((), (), winners, ()))

    assert 'data-winner-count="2"' in html
    assert html.count('data-header-outcome="undetermined"') == 2
    assert html.count("表头识别失败") == 2
    assert "chunk text 1" in html and "chunk text 2" in html
    assert "range=0:6" not in html and "context=table_header" not in html


def test_winner_html_renders_visual_grid_spans_header_cells_and_embedding_text() -> None:
    cells = (
        TableCell("header", "订单信息", 0, 1, 0, 2, 1, 2, None,
                  ("column_header",), "native", ()),
        TableCell("name", "产品", 1, 2, 0, 1, 1, 1, None, ("body",), "native", ()),
        TableCell("value", "电表", 1, 2, 1, 2, 1, 1, None, ("body",), "native", ()),
    )
    header = HeaderDecision(
        "identified", "unique_supported_candidate", (), (), (), 0, 1,
        (HeaderPath(0, ("header",), ("订单信息",)),
         HeaderPath(1, ("header",), ("订单信息",))),
    )
    serialized = SerializedTable(
        '表头路径：第1列 = "订单信息"。\n第2行：订单信息 = "产品"。',
        (SerializedTableLine("h", "header", '表头路径：第1列 = "订单信息"。', (0,), ("header",)),
         SerializedTableLine("d", "data", '第2行：订单信息 = "产品"。', (1,), ("name",))),
    )
    table = StructuredTable("table", "docling", "accurate", 2, 2, cells, (), None, (), ())
    node = TableNode("node", "slot", "#/tables/0", "selected_winner", table,
                     "group", header, serialized, ())
    parsed = ParsedDocument("doc", "order.pdf", "order.pdf", "hash", (node,), ())
    winner = SelectionWinnerSummary("group", "slot", "candidate", "docling", "accurate",
                                    "highest_total_score", 1.0)

    chunk = V2DocumentChunk(
        "chunk", "doc", "v2", 0, "table", serialized.text, 20,
        (ChunkSource("node", (), 0, len(serialized.text), False, "none"),), None, 0, 1,
    )
    rendered = _winner_html(parsed, (chunk,), TableSelectionSummary((), (), (winner,), ()))

    assert '<th class="identified-header" colspan="2">订单信息</th>' in rendered
    assert "<td>产品</td>" in rendered and "<td>电表</td>" in rendered
    assert "表头识别成功" in rendered
    assert "表格转化后的 Embedding 文本" in rendered
    assert html.escape(serialized.text) in rendered
