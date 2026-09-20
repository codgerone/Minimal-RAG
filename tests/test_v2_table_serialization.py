from rag.v2.table_header import detect_header
from rag.v2.table_models import GridPosition, TableCandidate, TableCell, TableRegion
from rag.v2.table_serialization import serialize_table


def _candidate(cells: tuple[TableCell, ...], rows: int, cols: int,
               missing: tuple[GridPosition, ...] = (), unplaced: str | None = None) -> TableCandidate:
    return TableCandidate("table", "pymupdf", "lines", "raw",
                          (TableRegion(1, 10, 10, None, "raw", None, "missing_bbox"),), rows, cols,
                          tuple(float(i) for i in range(cols + 1)),
                          tuple(float(i) for i in range(rows + 1)), cells, missing, unplaced, ())


def _cell(name: str, text: str | None, r0: int, r1: int, c0: int, c1: int) -> TableCell:
    return TableCell(name, text, r0, r1, c0, c1, r1-r0, c1-c0, None, ("body",), "native", (name,))


def test_identified_header_uses_paths_json_escaping_and_blank_marker() -> None:
    table = _candidate((
        _cell("h1", "产品", 0, 1, 0, 1), _cell("h2", "数量", 0, 1, 1, 2),
        _cell("a", '大"号\n二行', 1, 2, 0, 1), _cell("b", "100", 1, 2, 1, 2),
        _cell("c", "B", 2, 3, 0, 1), _cell("d", "200", 2, 3, 1, 2),
        _cell("e", "C", 3, 4, 0, 1), _cell("f", None, 3, 4, 1, 2),
    ), 4, 2)
    serialized = serialize_table(table, detect_header(table))
    assert serialized.lines[0].text == '第2行：产品 = "大\\"号\\n二行"；数量 = "100"。'
    assert serialized.lines[-1].text.endswith("数量 = 〔空白〕。")
    assert serialized.text == "\n".join(line.text for line in serialized.lines)


def test_undetermined_keeps_first_row_missing_position_and_literal_marker() -> None:
    table = _candidate((
        _cell("a", "A", 0, 1, 0, 1), _cell("b", "〔空白〕", 0, 1, 1, 2),
        _cell("c", "B", 1, 2, 0, 1),
    ), 2, 2, (GridPosition(1, 1),))
    serialized = serialize_table(table, detect_header(table))
    assert serialized.lines[0].text == "表头未确定。"
    assert '第2列 = "〔空白〕"' in serialized.text
    assert "第2列 = 〔缺失单元格〕" in serialized.text


def test_merged_cells_are_expressed_once_and_unplaced_text_is_retained() -> None:
    table = _candidate((
        _cell("merged", "华东", 0, 2, 0, 1), _cell("v1", "1", 0, 1, 1, 2),
        _cell("v2", "2", 1, 2, 1, 2),
    ), 2, 2, unplaced="游离\n文字")
    serialized = serialize_table(table, detect_header(table))
    merged = [line for line in serialized.lines if line.kind == "merged"]
    assert len(merged) == 1
    assert merged[0].text == '第1行至第2行共享第1列，内容 = "华东"。'
    assert serialized.lines[-1].text == '结构无法定位："游离\\n文字"。'
