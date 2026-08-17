from dataclasses import replace
from pathlib import Path

import pytest

from rag.config import Settings, update_env_key, validate_settings
from rag.errors import ConfigurationError


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        project_root=tmp_path,
        documents_dir=tmp_path / "documents",
        db_path=tmp_path / ".rag" / "chroma",
        manifest_path=tmp_path / ".rag" / "manifest.json",
        collection_name="minimal_rag_documents",
        embedding_model="intfloat/multilingual-e5-small",
        chunk_size=700,
        chunk_overlap=100,
        top_k=4,
        openrouter_api_key=None,
        openrouter_model="test-model",
    )


def test_valid_settings(settings: Settings) -> None:
    validate_settings(settings)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("chunk_size", 0),
        ("chunk_overlap", -1),
        ("chunk_overlap", 700),
        ("top_k", 0),
    ],
)
def test_invalid_numeric_settings(
    settings: Settings, field: str, value: int
) -> None:
    with pytest.raises(ConfigurationError):
        validate_settings(replace(settings, **{field: value}))


def test_documents_directory_cannot_escape_project(
    settings: Settings, tmp_path: Path
) -> None:
    outside = tmp_path.parent / "outside-documents"
    with pytest.raises(ConfigurationError, match="项目目录内"):
        validate_settings(replace(settings, documents_dir=outside))


def test_update_env_key_replaces_only_the_first_key_and_preserves_content(
    tmp_path: Path,
) -> None:
    path = tmp_path / ".env"
    path.write_text("# keep\r\nOTHER=value\r\nOPENROUTER_API_KEY=old\r\nLAST=x\r\n", encoding="utf-8", newline="")

    update_env_key(path, "OPENROUTER_API_KEY", "new")

    assert path.read_bytes() == b"# keep\r\nOTHER=value\r\nOPENROUTER_API_KEY=new\r\nLAST=x\r\n"


def test_update_env_key_rejects_any_other_key(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="OPENROUTER_API_KEY"):
        update_env_key(tmp_path / ".env", "CHUNK_SIZE", "1")
