import pytest

from rag.cli import build_parser


def test_parser_exposes_all_v1_commands() -> None:
    parser = build_parser()
    help_text = parser.format_help()
    for command in ("documents", "ingest", "browse", "chunks", "search", "ask", "chat", "eval"):
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


@pytest.mark.parametrize(
    "argv",
    [
        ["documents"], ["ingest"], ["browse"], ["chunks", "--document", "a.pdf"],
        ["search", "q"], ["ask", "q"], ["chat"], ["eval"],
    ],
)
def test_all_commands_default_to_v2(argv: list[str]) -> None:
    assert build_parser().parse_args(argv).pipeline == "v2"


def test_pipeline_can_be_selected_explicitly() -> None:
    assert build_parser().parse_args(["search", "q", "--pipeline", "v1"]).pipeline == "v1"


@pytest.mark.parametrize(
    "command,tail",
    [
        ("documents", []), ("ingest", ["--all"]), ("browse", []),
        ("chunks", ["--document", "a.pdf"]), ("search", ["q"]),
        ("ask", ["q"]), ("chat", []), ("eval", []),
    ],
)
def test_pipeline_can_be_selected_for_every_command(command: str, tail: list[str]) -> None:
    args = build_parser().parse_args([command, *tail, "--pipeline", "v1"])
    assert args.pipeline == "v1"


def test_ingest_all_is_explicit_and_file_is_mutually_exclusive() -> None:
    parser = build_parser()
    args = parser.parse_args(["ingest", "--all", "--pipeline", "v2"])
    assert args.all is True
    assert args.file is None
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["ingest", "--all", "--file", "a.pdf"])
    assert exc_info.value.code == 2


def test_browse_parses_pagination_and_document_filter() -> None:
    args = build_parser().parse_args(
        ["browse", "--pipeline", "v2", "--document", "order.pdf",
         "--offset", "10", "--limit", "5", "--full-metadata"]
    )
    assert args.document == "order.pdf"
    assert args.offset == 10
    assert args.limit == 5
    assert args.full_metadata is True
