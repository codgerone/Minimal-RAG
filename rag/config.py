"""Project settings loaded from the project-local .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from rag.errors import ConfigurationError


DEFAULT_OPENROUTER_MODEL = "google/gemini-2.5-flash-lite"
DEFAULT_COLLECTION = "minimal_rag_documents"
DEFAULT_EMBEDDING_MODEL = "intfloat/multilingual-e5-small"


@dataclass(frozen=True)
class Settings:
    project_root: Path
    documents_dir: Path
    db_path: Path
    manifest_path: Path
    collection_name: str
    embedding_model: str
    chunk_size: int
    chunk_overlap: int
    top_k: int
    openrouter_api_key: str | None
    openrouter_model: str


def _parse_int(name: str, raw_value: str) -> int:
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ConfigurationError(
            f"配置项 {name} 必须是整数，当前值为 {raw_value!r}。",
            "请修改项目根目录中的 .env。",
            cause=exc,
        ) from exc


def _resolve_from_root(project_root: Path, raw_value: str) -> Path:
    path = Path(raw_value)
    return path.resolve() if path.is_absolute() else (project_root / path).resolve()


def load_settings(require_api_key: bool = False) -> Settings:
    project_root = Path(__file__).resolve().parent.parent
    load_dotenv(project_root / ".env", override=False)

    api_key = os.getenv("OPENROUTER_API_KEY", "").strip() or None
    settings = Settings(
        project_root=project_root,
        documents_dir=_resolve_from_root(
            project_root, os.getenv("DOCUMENTS_DIR", "documents")
        ),
        db_path=_resolve_from_root(
            project_root, os.getenv("RAG_DB_PATH", ".rag/chroma")
        ),
        manifest_path=_resolve_from_root(
            project_root, os.getenv("RAG_MANIFEST_PATH", ".rag/manifest.json")
        ),
        collection_name=os.getenv("RAG_COLLECTION", DEFAULT_COLLECTION).strip(),
        embedding_model=os.getenv(
            "EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL
        ).strip(),
        chunk_size=_parse_int("CHUNK_SIZE", os.getenv("CHUNK_SIZE", "700")),
        chunk_overlap=_parse_int(
            "CHUNK_OVERLAP", os.getenv("CHUNK_OVERLAP", "100")
        ),
        top_k=_parse_int("TOP_K", os.getenv("TOP_K", "4")),
        openrouter_api_key=api_key,
        openrouter_model=os.getenv(
            "OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL
        ).strip(),
    )
    validate_settings(settings)

    if require_api_key and not settings.openrouter_api_key:
        raise ConfigurationError(
            "缺少配置项 OPENROUTER_API_KEY。",
            "复制 .env.example 为 .env，并填写 OpenRouter API Key。",
        )
    return settings


def validate_settings(settings: Settings) -> None:
    root = settings.project_root.resolve()
    documents_dir = settings.documents_dir.resolve()

    if not documents_dir.is_relative_to(root):
        raise ConfigurationError(
            f"DOCUMENTS_DIR 必须位于项目目录内，当前路径为 {documents_dir}。",
            "请把 DOCUMENTS_DIR 设置为项目内的相对路径，例如 documents。",
        )
    if settings.chunk_size <= 0:
        raise ConfigurationError("CHUNK_SIZE 必须大于 0。", "请修改 .env。")
    if settings.chunk_overlap < 0:
        raise ConfigurationError("CHUNK_OVERLAP 不能小于 0。", "请修改 .env。")
    if settings.chunk_overlap >= settings.chunk_size:
        raise ConfigurationError(
            "CHUNK_OVERLAP 必须严格小于 CHUNK_SIZE。", "请修改 .env。"
        )
    if settings.top_k <= 0:
        raise ConfigurationError("TOP_K 必须是正整数。", "请修改 .env。")
    if not settings.collection_name:
        raise ConfigurationError("RAG_COLLECTION 不能为空。", "请修改 .env。")
    if not settings.embedding_model:
        raise ConfigurationError("EMBEDDING_MODEL 不能为空。", "请修改 .env。")
    if not settings.openrouter_model:
        raise ConfigurationError("OPENROUTER_MODEL 不能为空。", "请修改 .env。")

