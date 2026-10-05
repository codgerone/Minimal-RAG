"""Plugin registry: implementation names usable in configs/*.toml, and assembly validation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable

from rag.config import AssemblyConfig, Component, ConfigError
from rag.index.embedder import E5Embedder, Embedder
from rag.ingest.chunkers import Chunker
from rag.ingest.parsers import Parser
from rag.ingest.tables.extractors import TableExtractor
from rag.ingest.tables.formatters import RowTextFormatter, TableFormatter
from rag.query.llm import LLM, OpenRouterLLM


def _docling_parser(**params: Any) -> Parser:
    from rag.ingest.parsers.docling_layout import DoclingLayoutParser
    return DoclingLayoutParser(**params)


def _pymupdf_parser(**params: Any) -> Parser:
    from rag.ingest.parsers.pymupdf_pages import PyMuPDFPagesParser
    return PyMuPDFPagesParser(**params)


def _extractor(name: str) -> Callable[..., TableExtractor]:
    def build() -> TableExtractor:
        from rag.ingest.tables import extractors
        return getattr(extractors, f"{name}_extractor")()
    return build


def _characters(embedder: Embedder, **params: Any) -> Chunker:
    from rag.ingest.chunkers.characters import CharacterChunker
    return CharacterChunker(**params)


def _structured(embedder: Embedder, **params: Any) -> Chunker:
    from rag.ingest.chunkers.structured import StructuredTokenChunker
    if not hasattr(embedder, "count_passage"):
        raise ConfigError("structured_tokens 分块器需要能统计 token 的编码器")
    return StructuredTokenChunker(embedder, **params)  # type: ignore[arg-type]


PARSERS: dict[str, Callable[..., Parser]] = {
    "pymupdf_pages": _pymupdf_parser,
    "docling_layout": _docling_parser,
}
TABLE_EXTRACTORS: dict[str, Callable[..., TableExtractor]] = {
    name: _extractor(name) for name in ("pymupdf", "camelot", "docling", "unstructured")
}
TABLE_FORMATTERS: dict[str, Callable[..., TableFormatter]] = {"row_text_v1": RowTextFormatter}
CHUNKERS: dict[str, Callable[..., Chunker]] = {
    "characters": _characters,
    "structured_tokens": _structured,
}
EMBEDDERS: dict[str, Callable[..., Embedder]] = {"e5_small": E5Embedder}
RETRIEVERS = ("semantic",)
LLMS: dict[str, Callable[..., LLM]] = {"openrouter": OpenRouterLLM}

# Steps whose implementation only makes sense with a specific parser.
REQUIRES_PARSER = {
    ("table_extractors", "docling"): "docling_layout",
    ("chunker", "characters"): "pymupdf_pages",
}


def _make(registry: dict[str, Callable[..., Any]], section: str, component: Component,
          *args: Any) -> Any:
    factory = registry.get(component.use)
    if factory is None:
        raise ConfigError(f"[{section}] 没有名为 {component.use!r} 的实现，可选：{', '.join(registry)}")
    try:
        return factory(*args, **component.params)
    except TypeError as exc:
        raise ConfigError(f"[{section}] {component.use} 的参数无效：{exc}") from exc
    except ValueError as exc:
        raise ConfigError(f"[{section}] {component.use}：{exc}") from exc


@dataclass
class Assembly:
    """A validated config with its implementations instantiated (models load lazily)."""
    config: AssemblyConfig
    parser: Parser
    extractors: tuple[TableExtractor, ...]
    formatter: TableFormatter | None
    embedder: Embedder
    chunker: Chunker
    _llm: LLM | None = field(default=None, repr=False)

    @property
    def name(self) -> str:
        return self.config.name

    def build_settings(self) -> dict[str, Any]:
        return {**self.config.build_settings(), "embedder_identity": self.embedder.identity()}

    def fingerprint(self) -> str:
        raw = json.dumps(self.build_settings(), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def llm(self) -> LLM:
        if self._llm is None:
            self._llm = _make(LLMS, "llm", self.config.llm)
        return self._llm


def assemble(config: AssemblyConfig) -> Assembly:
    parser_name = config.parser.use
    if parser_name not in PARSERS:
        raise ConfigError(f"[parser] 没有名为 {parser_name!r} 的实现，可选：{', '.join(PARSERS)}")
    for item in config.table_extractors:
        needed = REQUIRES_PARSER.get(("table_extractors", item.use))
        if needed and needed != parser_name:
            raise ConfigError(f"表格提取器 {item.use} 只能搭配 {needed} 解析器")
    needed = REQUIRES_PARSER.get(("chunker", config.chunker.use))
    if needed and needed != parser_name:
        raise ConfigError(f"分块器 {config.chunker.use} 只能搭配 {needed} 解析器")
    uses = [item.use for item in config.table_extractors]
    if len(uses) != len(set(uses)):
        raise ConfigError("table_extractors 中有重复的提取器")
    if config.retriever.use not in RETRIEVERS:
        raise ConfigError(f"[retriever] 没有名为 {config.retriever.use!r} 的实现，可选：{', '.join(RETRIEVERS)}")
    if config.retriever.params:
        raise ConfigError(f"[retriever] semantic 只接受 top_k 参数")
    if config.llm.use not in LLMS:
        raise ConfigError(f"[llm] 没有名为 {config.llm.use!r} 的实现，可选：{', '.join(LLMS)}")

    parser = _make(PARSERS, "parser", config.parser)
    extractors = tuple(_make(TABLE_EXTRACTORS, "table_extractors", item)
                       for item in config.table_extractors)
    if extractors and not parser.provides_table_slots:
        raise ConfigError(f"解析器 {parser_name} 不提供表格位置，不能配置表格提取器")
    formatter = None
    if parser.provides_table_slots:
        if config.table_formatter is None:
            raise ConfigError(f"解析器 {parser_name} 会产出表格，需要配置 [table_formatter]")
        formatter = _make(TABLE_FORMATTERS, "table_formatter", config.table_formatter)
    elif config.table_formatter is not None:
        raise ConfigError(f"解析器 {parser_name} 不产出表格，[table_formatter] 不会生效，请删除")
    embedder = _make(EMBEDDERS, "embedder", config.embedder)
    chunker = _make(CHUNKERS, "chunker", config.chunker, embedder)
    return Assembly(config, parser, extractors, formatter, embedder, chunker)
