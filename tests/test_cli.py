import pytest

from rag.cli import build_parser


def test_parser_exposes_all_v1_commands() -> None:
    parser = build_parser()
    help_text = parser.format_help()
    for command in ("documents", "ingest", "chunks", "search", "ask", "chat", "eval"):
        assert command in help_text


def test_chunks_requires_document() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["chunks"])
    assert exc_info.value.code == 2


def test_search_parses_filter_and_top_k() -> None:
    args = build_parser().parse_args(
        ["search", "付款条件", "--top-k", "2", "--document", "order.pdf"]
    )
    assert args.question == "付款条件"
    assert args.top_k == 2
    assert args.document == "order.pdf"
