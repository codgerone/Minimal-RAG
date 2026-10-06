"""Assembly configs: configs/<name>.toml chooses one implementation per replaceable step."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Bump when a built-in algorithm changes its output, so existing indexes are flagged stale.
RULES_VERSION = "v3.1"
DEFAULT_CONFIG = "structured"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Component:
    use: str
    params: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"use": self.use, **self.params}


@dataclass(frozen=True)
class AssemblyConfig:
    name: str
    description: str
    path: Path
    parser: Component
    table_extractors: tuple[Component, ...]
    table_formatter: Component | None
    chunker: Component
    embedder: Component
    retriever: Component
    top_k: int          # search / ask / chat
    llm: Component
    eval_top_k: int     # formal evaluation; kept at the baseline K for comparability

    def build_settings(self) -> dict[str, Any]:
        """Everything that changes index content. Retrieval, LLM and display settings are excluded."""
        return {
            "rules_version": RULES_VERSION,
            "parser": self.parser.as_dict(),
            "table_extractors": [item.as_dict() for item in self.table_extractors],
            "table_formatter": self.table_formatter.as_dict() if self.table_formatter else None,
            "chunker": self.chunker.as_dict(),
            "embedder": self.embedder.as_dict(),
        }


def _component(data: Any, section: str, file: Path) -> Component:
    if not isinstance(data, dict) or not isinstance(data.get("use"), str):
        raise ConfigError(f"{file.name}：[{section}] 必须包含 use = \"实现名\"")
    params = {key: value for key, value in data.items() if key != "use"}
    return Component(data["use"], params)


def load_config(path: Path) -> AssemblyConfig:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"无法读取配置 {path.name}：{exc}") from exc
    known = {"name", "description", "parser", "table_extractors", "table_formatter",
             "chunker", "embedder", "retriever", "llm", "eval"}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"{path.name}：未知配置项 {sorted(unknown)}")
    name = data.get("name")
    if name != path.stem:
        raise ConfigError(f"{path.name}：name 必须与文件名一致（应为 {path.stem!r}）")
    for section in ("parser", "chunker", "embedder", "retriever", "llm"):
        if section not in data:
            raise ConfigError(f"{path.name}：缺少 [{section}]")
    extractors = data.get("table_extractors", [])
    if not isinstance(extractors, list):
        raise ConfigError(f"{path.name}：table_extractors 必须写成 [[table_extractors]] 列表")
    retriever = _component(data["retriever"], "retriever", path)
    top_k = retriever.params.pop("top_k", 4)
    if type(top_k) is not int or top_k <= 0:
        raise ConfigError(f"{path.name}：retriever.top_k 必须为正整数")
    evaluation = data.get("eval", {})
    if not isinstance(evaluation, dict) or set(evaluation) - {"top_k"}:
        raise ConfigError(f"{path.name}：[eval] 只接受 top_k")
    eval_top_k = evaluation.get("top_k", 3)
    if type(eval_top_k) is not int or eval_top_k <= 0:
        raise ConfigError(f"{path.name}：eval.top_k 必须为正整数")
    formatter = data.get("table_formatter")
    return AssemblyConfig(
        name, str(data.get("description", "")), path,
        _component(data["parser"], "parser", path),
        tuple(_component(item, "table_extractors", path) for item in extractors),
        _component(formatter, "table_formatter", path) if formatter is not None else None,
        _component(data["chunker"], "chunker", path),
        _component(data["embedder"], "embedder", path),
        retriever, top_k,
        _component(data["llm"], "llm", path),
        eval_top_k,
    )


def config_dir(workspace: Path) -> Path:
    return workspace / "configs"


def list_configs(workspace: Path) -> list[AssemblyConfig]:
    return [load_config(path) for path in sorted(config_dir(workspace).glob("*.toml"))]


def resolve_config(workspace: Path, name: str | None) -> AssemblyConfig:
    chosen = name or os.environ.get("RAG_CONFIG") or DEFAULT_CONFIG
    path = config_dir(workspace) / f"{chosen}.toml"
    if not path.is_file():
        available = ", ".join(p.stem for p in sorted(config_dir(workspace).glob("*.toml")))
        raise ConfigError(f"找不到配置 {chosen!r}。可用配置：{available or '无'}")
    return load_config(path)
