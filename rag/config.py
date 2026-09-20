"""Project settings loaded from the project-local .env file."""

from __future__ import annotations

import os
import re
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from rag.embeddings import DEFAULT_EMBEDDING_REVISION
from rag.errors import ConfigurationError
from rag.pipeline_registry import PipelineIdentity, PipelineRegistry


DEFAULT_OPENROUTER_MODEL = "google/gemini-2.5-flash-lite"
DEFAULT_EMBEDDING_MODEL = "intfloat/multilingual-e5-small"


@dataclass(frozen=True)
class Settings:
    project_root: Path
    documents_dir: Path
    db_path: Path
    artifacts_path: Path
    embedding_model: str
    embedding_model_revision: str
    v1_chunk_size: int
    v1_chunk_overlap: int
    v2_max_input_tokens: int
    v2_text_overlap_tokens: int
    top_k: int
    diagnostics_enabled: bool
    openrouter_api_key: str | None
    openrouter_model: str


@dataclass(frozen=True)
class SelectedPipelineSettings:
    application: Settings
    identity: PipelineIdentity

    def __getattr__(self, name: str):
        return getattr(self.application, name)

    @property
    def manifest_path(self) -> Path:
        return self.identity.manifest_path

    @property
    def collection_name(self) -> str:
        return self.identity.collection_name

    @property
    def chunk_size(self) -> int:
        return self.application.v1_chunk_size

    @property
    def chunk_overlap(self) -> int:
        return self.application.v1_chunk_overlap


def _parse_int(name: str, raw_value: str) -> int:
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ConfigurationError(
            f"配置项 {name} 必须是整数，当前值为 {raw_value!r}。",
            "请修改项目根目录中的 .env。",
            cause=exc,
        ) from exc


def _parse_bool(name: str, raw_value: str) -> bool:
    normalized = raw_value.strip().casefold()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ConfigurationError(
        f"配置项 {name} 必须是 true|false|1|0|yes|no。",
        "请修改项目根目录中的 .env。",
    )


def select_pipeline(settings: Settings, pipeline_id: str) -> SelectedPipelineSettings:
    return SelectedPipelineSettings(
        settings, PipelineRegistry(settings.project_root).get(pipeline_id)
    )


def _resolve_from_root(project_root: Path, raw_value: str) -> Path:
    path = Path(raw_value)
    return path.resolve() if path.is_absolute() else (project_root / path).resolve()


def load_settings(require_api_key: bool = False) -> Settings:
    project_root = Path(__file__).resolve().parent.parent
    load_dotenv(project_root / ".env", override=False)

    api_key = os.getenv("OPENROUTER_API_KEY", "").strip() or None
    raw_db_path = os.getenv("RAG_DB_PATH", ".rag/system-v2/chroma").strip()
    raw_artifacts_path = os.getenv(
        "RAG_ARTIFACTS_PATH", ".rag/system-v2/artifacts"
    ).strip()
    storage_aliases = []
    if raw_db_path.replace("\\", "/").casefold() == ".rag/chroma":
        raw_db_path = ".rag/system-v2/chroma"
        storage_aliases.append("RAG_DB_PATH=.rag/chroma")
    if raw_artifacts_path.replace("\\", "/").casefold() == ".rag/artifacts":
        raw_artifacts_path = ".rag/system-v2/artifacts"
        storage_aliases.append("RAG_ARTIFACTS_PATH=.rag/artifacts")
    settings = Settings(
        project_root=project_root,
        documents_dir=_resolve_from_root(
            project_root, os.getenv("DOCUMENTS_DIR", "documents")
        ),
        db_path=_resolve_from_root(project_root, raw_db_path),
        artifacts_path=_resolve_from_root(project_root, raw_artifacts_path),
        embedding_model=os.getenv(
            "EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL
        ).strip(),
        embedding_model_revision=os.getenv(
            "EMBEDDING_MODEL_REVISION", DEFAULT_EMBEDDING_REVISION
        ).strip(),
        v1_chunk_size=_parse_int("CHUNK_SIZE", os.getenv("CHUNK_SIZE", "700")),
        v1_chunk_overlap=_parse_int(
            "CHUNK_OVERLAP", os.getenv("CHUNK_OVERLAP", "100")
        ),
        v2_max_input_tokens=_parse_int(
            "V2_MAX_INPUT_TOKENS", os.getenv("V2_MAX_INPUT_TOKENS", "512")
        ),
        v2_text_overlap_tokens=_parse_int(
            "V2_TEXT_OVERLAP_TOKENS", os.getenv("V2_TEXT_OVERLAP_TOKENS", "32")
        ),
        top_k=_parse_int("TOP_K", os.getenv("TOP_K", "4")),
        diagnostics_enabled=_parse_bool(
            "RAG_DIAGNOSTICS", os.getenv("RAG_DIAGNOSTICS", "false")
        ),
        openrouter_api_key=api_key,
        openrouter_model=os.getenv(
            "OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL
        ).strip(),
    )
    validate_settings(settings)

    deprecated = [
        name for name in ("RAG_COLLECTION", "RAG_MANIFEST_PATH")
        if os.getenv(name) is not None
    ]
    if deprecated:
        warnings.warn(
            f"配置 {', '.join(deprecated)} 已弃用并被忽略；v1/v2 使用固定索引身份。",
            FutureWarning,
            stacklevel=2,
        )
    if storage_aliases:
        warnings.warn(
            f"配置 {', '.join(storage_aliases)} 是旧布局别名；已映射到 .rag/system-v2/。",
            FutureWarning,
            stacklevel=2,
        )

    if require_api_key and not settings.openrouter_api_key:
        raise ConfigurationError(
            "缺少配置项 OPENROUTER_API_KEY。",
            "复制 .env.example 为 .env，并填写 OpenRouter API Key。",
        )
    return settings


def validate_settings(settings: Settings) -> None:
    root = settings.project_root.resolve()
    for name, configured in (
        ("DOCUMENTS_DIR", settings.documents_dir),
        ("RAG_DB_PATH", settings.db_path),
        ("RAG_ARTIFACTS_PATH", settings.artifacts_path),
    ):
        resolved = configured.resolve()
        if not resolved.is_relative_to(root):
            raise ConfigurationError(
                f"{name} 必须位于项目目录内，当前路径为 {resolved}。",
                "请使用项目内的相对路径。",
            )
    if settings.v1_chunk_size <= 0:
        raise ConfigurationError("CHUNK_SIZE 必须大于 0。", "请修改 .env。")
    if settings.v1_chunk_overlap < 0:
        raise ConfigurationError("CHUNK_OVERLAP 不能小于 0。", "请修改 .env。")
    if settings.v1_chunk_overlap >= settings.v1_chunk_size:
        raise ConfigurationError(
            "CHUNK_OVERLAP 必须严格小于 CHUNK_SIZE。", "请修改 .env。"
        )
    if settings.v2_max_input_tokens <= 0:
        raise ConfigurationError("V2_MAX_INPUT_TOKENS 必须大于 0。", "请修改 .env。")
    if settings.v2_text_overlap_tokens < 0 or settings.v2_text_overlap_tokens >= settings.v2_max_input_tokens:
        raise ConfigurationError("V2_TEXT_OVERLAP_TOKENS 必须非负且小于 V2_MAX_INPUT_TOKENS。", "请修改 .env。")
    if settings.top_k <= 0:
        raise ConfigurationError("TOP_K 必须是正整数。", "请修改 .env。")
    if not settings.embedding_model:
        raise ConfigurationError("EMBEDDING_MODEL 不能为空。", "请修改 .env。")
    if not settings.embedding_model_revision:
        raise ConfigurationError("EMBEDDING_MODEL_REVISION 不能为空。", "请修改 .env。")
    if not settings.openrouter_model:
        raise ConfigurationError("OPENROUTER_MODEL 不能为空。", "请修改 .env。")


def update_env_key(path: Path, key: str, value: str) -> None:
    """Atomically update the sole interactively writable secret in a .env file."""
    if key != "OPENROUTER_API_KEY":
        raise ConfigurationError("只允许更新 OPENROUTER_API_KEY。")
    try:
        content = path.read_bytes().decode("utf-8") if path.exists() else ""
        newline = "\r\n" if "\r\n" in content else "\n"
        lines = content.splitlines(keepends=True)
        pattern = re.compile(r"^(\s*OPENROUTER_API_KEY\s*=).*$", re.ASCII)
        replaced = False
        updated: list[str] = []
        for line in lines:
            if not replaced and (match := pattern.match(line.rstrip("\r\n"))):
                ending = line[len(line.rstrip("\r\n")) :]
                updated.append(f"{match.group(1)}{value}{ending}")
                replaced = True
            else:
                updated.append(line)
        if not replaced:
            if updated and not updated[-1].endswith(("\n", "\r")):
                updated.append(newline)
            updated.append(f"{key}={value}{newline}")

        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write("".join(updated))
            stream.flush()
        temporary.replace(path)
    except OSError as exc:
        raise ConfigurationError(
            f"无法更新 .env 中的 {key}。",
            "请检查 .env 所在目录的权限和磁盘空间。",
            cause=exc,
        ) from exc
