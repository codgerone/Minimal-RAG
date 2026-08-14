import pytest

import rag.config as config
from rag.cli import _validate_cli_args, build_parser, main


def test_file_and_prune_are_rejected_together() -> None:
    parser = build_parser()
    args = parser.parse_args(["ingest", "--file", "a.pdf", "--prune"])
    with pytest.raises(SystemExit) as exc_info:
        _validate_cli_args(parser, args)
    assert exc_info.value.code == 2


def test_ask_without_api_key_is_clean_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(config, "load_dotenv", lambda *_args, **_kwargs: False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    exit_code = main(["ask", "question"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "OPENROUTER_API_KEY" in captured.err
    assert "Traceback" not in captured.err
