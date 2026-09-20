from pathlib import Path

from rag.cli_readiness import ReadinessResult, _read_masked_windows_secret, display_status, ensure_llm_credentials
from rag.config import SelectedPipelineSettings, Settings, select_pipeline
from rag.models import DocumentState, DocumentStatus


class _Terminal:
    def __init__(self, secret: str | None = None, choice: str = "0") -> None:
        self.secret = secret
        self.choice = choice
        self.output: list[str] = []

    def is_interactive(self) -> bool:
        return True

    def write(self, text: str = "") -> None:
        self.output.append(text)

    def confirm(self, prompt: str, *, default: bool) -> bool:
        return default

    def read_secret(self, prompt: str) -> str | None:
        return self.secret

    def choose(self, prompt: str, choices: set[str], default: str) -> str:
        return self.choice


def _settings(tmp_path: Path) -> SelectedPipelineSettings:
    return select_pipeline(Settings(
        tmp_path, tmp_path / "documents", tmp_path / ".rag/system-v2/chroma",
        tmp_path / ".rag/system-v2/artifacts", "e", "revision",
        10, 1, 512, 32, 1, False, None, "llm",
    ), "v1")


def test_display_status_distinguishes_manual_processing() -> None:
    status = DocumentStatus(DocumentState.UNPROCESSABLE, "id", "scan.pdf", "scan.pdf", detail="no text")

    assert display_status(status) == ("需人工处理", "no text")


def test_missing_key_can_be_used_for_this_run_only(tmp_path: Path) -> None:
    terminal = _Terminal("secret", "1")
    result, settings = ensure_llm_credentials(_settings(tmp_path), terminal)

    assert result is ReadinessResult.READY
    assert settings.openrouter_api_key == "secret"
    assert not (tmp_path / ".env").exists()
    assert "已录入 API Key：******" in terminal.output


def test_key_with_whitespace_is_rejected_before_saving(tmp_path: Path) -> None:
    terminal = _Terminal('uv run python -m rag ask "question" secret', "2")

    result, settings = ensure_llm_credentials(_settings(tmp_path), terminal)

    assert result is ReadinessResult.STOPPED
    assert settings.openrouter_api_key is None
    assert not (tmp_path / ".env").exists()
    assert any("格式无效" in item for item in terminal.output)


def test_windows_masked_secret_echoes_one_star_per_character(monkeypatch, capsys) -> None:
    keys = iter(["a", "b", "\b", "c", "\r"])
    monkeypatch.setattr("rag.cli_readiness.msvcrt.getwch", lambda: next(keys))

    value = _read_masked_windows_secret("API Key：")

    assert value == "ac"
    assert capsys.readouterr().out == "API Key：**\b \b*\n"


def test_empty_secret_stops_without_echoing_it(tmp_path: Path) -> None:
    terminal = _Terminal("", "1")

    result, _ = ensure_llm_credentials(_settings(tmp_path), terminal)

    assert result is ReadinessResult.STOPPED
    assert not terminal.output
