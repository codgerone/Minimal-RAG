"""Plugin registry: implementation names usable in configs/*.toml, and assembly validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from rag.config import AssemblyConfig, Component, ConfigError
from rag.index import embedder as embedders
from rag.index.analyzer import Analyzer
from rag.index.embedder import Embedder
from rag.ingest.chunkers import Chunker
from rag.ingest.parsers import Parser
from rag.ingest.pipeline import TableSteps
from rag.ingest.tables.extractors import TableExtractor
from rag.ingest.tables.formatters import (
    LabeledRowsFormatter, MarkdownRowsFormatter, TableFormatter,
)
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
    from rag.ingest.chunkers.tokens import E5TokenCounter
    return StructuredTokenChunker(E5TokenCounter(), **params)


PARSERS: dict[str, Callable[..., Parser]] = {
    "pymupdf_pages": _pymupdf_parser,
    "docling_layout": _docling_parser,
}
TABLE_EXTRACTORS: dict[str, Callable[..., TableExtractor]] = {
    name: _extractor(name) for name in ("pymupdf", "camelot", "docling", "unstructured")
}
TABLE_STRUCTURES = ("header_rules", "llm_annotation")
TABLE_FORMATTERS: dict[str, Callable[..., TableFormatter]] = {
    "markdown_rows": MarkdownRowsFormatter,
    "labeled_rows": LabeledRowsFormatter,
}
TABLE_INDEX_TEXTS = ("rows", "rows_with_lead", "summary")
TABLE_LLM_PARAMS = {"model", "temperature"}   # llm_annotation and summary
CHUNKERS: dict[str, Callable[..., Chunker]] = {
    "characters": _characters,
    "structured_tokens": _structured,
}
EMBEDDERS: dict[str, Callable[..., Embedder]] = {
    "e5_small": embedders.e5_small,
    "e5_large_instruct": embedders.e5_large_instruct,
    "bge_m3": embedders.bge_m3,
    "qwen3_embedding_0_6b": embedders.qwen3_embedding_0_6b,
}
RETRIEVERS = ("semantic", "bm25", "hybrid")
NEEDS_LEXICAL = ("bm25", "hybrid")
LLMS: dict[str, Callable[..., LLM]] = {"openrouter": OpenRouterLLM}


def _rapidocr() -> Any:
    from rag.ingest.ocr.rapidocr_engine import RapidOcrEngine
    return RapidOcrEngine()


OCR_ENGINES: dict[str, Callable[..., Any]] = {"rapidocr": _rapidocr}

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
    table_steps: TableSteps | None      # set when the parser produces tables
    embedder: Embedder
    chunker: Chunker
    lexical: Analyzer | None = None     # set when the config has [lexical]: ingest builds a BM25 index
    ocr: Any = None                     # OcrStep, set when the config has [ocr]
    _llm: LLM | None = field(default=None, repr=False)

    @property
    def name(self) -> str:
        return self.config.name

    def build_settings(self) -> dict[str, Any]:
        """Every build step of the config (rag/index/build_steps.py); each document's own record
        holds only the steps it passes through."""
        from rag.index.build_steps import step_settings
        return step_settings(self)

    def fingerprint(self) -> str:
        """Identifies the config's build settings for display; staleness is judged per document."""
        from rag.index.build_steps import fingerprint
        return fingerprint(self.build_settings())

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
        raise ConfigError(f"[retriever] {config.retriever.use} 只接受 top_k、document_filter 和 table_return_unit 参数")
    if config.retriever.use in NEEDS_LEXICAL and config.lexical is None:
        raise ConfigError(f"检索器 {config.retriever.use} 需要 BM25 索引，请在配置中加入 [lexical]")
    if config.llm.use not in LLMS:
        raise ConfigError(f"[llm] 没有名为 {config.llm.use!r} 的实现，可选：{', '.join(LLMS)}")

    parser = _make(PARSERS, "parser", config.parser)
    extractors = tuple(_make(TABLE_EXTRACTORS, "table_extractors", item)
                       for item in config.table_extractors)
    if extractors and not parser.provides_table_slots:
        raise ConfigError(f"解析器 {parser_name} 不提供表格位置，不能配置表格提取器")
    embedder = _make(EMBEDDERS, "embedder", config.embedder)
    chunker = _make(CHUNKERS, "chunker", config.chunker, embedder)
    lexical = Analyzer(**config.lexical) if config.lexical is not None else None
    table_steps = _table_steps(config, parser, chunker)
    return Assembly(config, parser, extractors, table_steps, embedder, chunker, lexical, ocr_step(config))


def ocr_step(config: AssemblyConfig) -> Any:
    """The OcrStep of a config with [ocr], else None (scanned pages stay unread)."""
    if config.ocr is None:
        return None
    from rag.ingest.ocr.step import OcrStep
    if config.ocr.use not in OCR_ENGINES:
        raise ConfigError(f"[ocr] 没有名为 {config.ocr.use!r} 的实现，可选：{', '.join(OCR_ENGINES)}")
    return OcrStep(config.ocr.use, OCR_ENGINES[config.ocr.use](), config.ocr.params["dpi"],
                   _workspace_root(config) / ".rag" / "ocr")


def _workspace_root(config: AssemblyConfig):
    return next(p for p in config.path.parents if p.name == "configs").parent


def _table_steps(config: AssemblyConfig, parser: Parser, chunker: Chunker) -> TableSteps | None:
    sections = {"table_structure": config.table_structure, "table_formatter": config.table_formatter,
                "table_index_text": config.table_index_text}
    if not parser.provides_table_slots:
        given = [name for name, value in sections.items() if value is not None]
        if given:
            raise ConfigError(f"解析器 {config.parser.use} 不产出表格，{'、'.join(f'[{n}]' for n in given)} 不会生效，请删除")
        return None
    missing = [name for name, value in sections.items() if value is None]
    if missing:
        raise ConfigError(f"解析器 {config.parser.use} 会产出表格，需要配置 {'、'.join(f'[{n}]' for n in missing)}")
    structure, formatter_config, index_text = (config.table_structure, config.table_formatter,
                                               config.table_index_text)
    assert structure is not None and formatter_config is not None and index_text is not None
    if structure.use not in TABLE_STRUCTURES:
        raise ConfigError(f"[table_structure] 没有名为 {structure.use!r} 的实现，可选：{', '.join(TABLE_STRUCTURES)}")
    if index_text.use not in TABLE_INDEX_TEXTS:
        raise ConfigError(f"[table_index_text] 没有名为 {index_text.use!r} 的实现，可选：{', '.join(TABLE_INDEX_TEXTS)}")
    formatter = _make(TABLE_FORMATTERS, "table_formatter", formatter_config)
    for section, component, llm_use in (("table_structure", structure, "llm_annotation"),
                                        ("table_index_text", index_text, "summary")):
        allowed = TABLE_LLM_PARAMS if component.use == llm_use else set()
        if set(component.params) - allowed or (component.use == llm_use and "model" not in component.params):
            raise ConfigError(f"[{section}] {component.use} " + (f"需要 model，只接受 {sorted(allowed)}" if allowed
                                                                else "不接受参数"))
    if index_text.use == "rows_with_lead" and structure.use != "llm_annotation":
        raise ConfigError("[table_index_text] rows_with_lead 需要 [table_structure] llm_annotation（引导句与结构标注同一次调用）")
    if index_text.use == "summary" and structure.use != "header_rules":
        raise ConfigError("[table_index_text] summary 只能搭配 [table_structure] header_rules")
    if index_text.use == "summary" and config.table_return_unit == "chunk":
        raise ConfigError("[table_index_text] summary 时返回单元不能是 chunk（摘要不对应单元格）")
    uses_llm = structure.use == "llm_annotation" or index_text.use == "summary"
    if uses_llm and not hasattr(chunker, "max_tokens"):
        raise ConfigError("LLM 表格处理需要按 token 分块的分块器（structured_tokens）")
    from rag.ingest.tables.structure import HeaderRulesStructure
    structurer: Any = HeaderRulesStructure()
    summarizer = None
    if uses_llm:
        from rag.ingest.chunkers.tokens import E5TokenCounter
        from rag.ingest.tables.llm_tables import LLMAnnotationStructure, LLMCache, LLMSummary
        cache = LLMCache(_workspace_root(config) / ".rag" / "llm-cache")
        llm_config = structure if structure.use == "llm_annotation" else index_text
        llm = OpenRouterLLM(model=llm_config.params["model"], temperature=llm_config.params.get("temperature", 0),
                            env_override=False)
        step = (LLMAnnotationStructure if structure.use == "llm_annotation" else LLMSummary)(
            llm, E5TokenCounter(), chunker.max_tokens, cache)  # type: ignore[attr-defined]
        if structure.use == "llm_annotation":
            structurer = step
        else:
            summarizer = step
    return TableSteps(structurer, formatter, index_text.use, summarizer)  # type: ignore[arg-type]
