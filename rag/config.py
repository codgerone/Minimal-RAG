"""Assembly configs: configs/<name>.toml chooses one implementation per replaceable step."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Bump when a built-in algorithm changes its output, so existing indexes are flagged stale.
RULES_VERSION = "v3.13"
DEFAULT_CONFIG = "structured"
TABLE_RETURN_UNITS = ("chunk", "table")
QUERY_MODES = ("retrieval", "multi_round")   # [query] mode: the question retrieved once, or multi-round retrieval


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Component:
    use: str
    params: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"use": self.use, **self.params}


@dataclass(frozen=True)
class MultiRoundConfig:
    """[multi_round]: query settings of multi-round retrieval (docs/rules/multi-round.md); not fingerprinted."""
    max_llm_calls: int = 3                                   # the answering call included
    fallback_schedule: tuple[tuple[str, ...], ...] = ()      # fallback groups by attempt; () = arrangement A (one group of every mode)
    units_per_attempt: int = 3                               # K per question, mode and retrieval
    model: str = "google/gemini-3.8-flash"


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
    document_filter: bool = True   # limit retrieval to documents whose catalog code is in the question
    lexical: dict[str, Any] | None = None   # [lexical]: BM25 index analyzer settings; None = no BM25 index
    table_return_unit: str = "chunk"   # a hit on a table chunk returns that chunk, or the whole table
    table_structure: Component | None = None    # [table_structure]: header_rules / llm_annotation
    table_index_text: Component | None = None   # [table_index_text]: rows / rows_with_lead / summary
    bm25_weight: float = 1.0   # hybrid: weight α of the BM25 ranking in the fusion (docs/rules/lexical.md)
    multi_round: MultiRoundConfig = MultiRoundConfig()
    query_mode: str = "retrieval"   # [query] mode: retrieval (the question retrieved once) | multi_round

    def build_settings(self) -> dict[str, Any]:
        """Everything that changes index content. Retrieval, LLM and display settings are excluded."""
        return {
            "rules_version": RULES_VERSION,
            "parser": self.parser.as_dict(),
            "table_extractors": [item.as_dict() for item in self.table_extractors],
            "table_structure": _with_rules(self.table_structure, "llm_annotation", _annotation_rules),
            "table_formatter": self.table_formatter.as_dict() if self.table_formatter else None,
            "table_index_text": _with_rules(self.table_index_text, "summary", _summary_rules),
            "chunker": self.chunker.as_dict(),
            "embedder": self.embedder.as_dict(),
            # Only present when configured, so configs without a BM25 index keep their fingerprint.
            **({"lexical": self.lexical} if self.lexical is not None else {}),
        }


def _with_rules(component: Component | None, llm_use: str, rules: Any) -> dict[str, Any] | None:
    if component is None:
        return None
    return {**component.as_dict(), **(rules() if component.use == llm_use else {})}


def _llm_rules() -> dict[str, Any]:
    """Prompt, reasoning and context rules of LLM table processing (they change its output)."""
    from rag.ingest.tables import llm_tables as rules
    return {"prompt_version": rules.PROMPT_VERSION, "reasoning_effort": rules.REASONING_EFFORT,
            "context_nodes": rules.CONTEXT_NODES, "context_node_tokens": rules.CONTEXT_NODE_TOKENS,
            "context_skipped_kinds": sorted(rules.CONTEXT_SKIPPED_KINDS)}


def _annotation_rules() -> dict[str, Any]:
    from rag.ingest.tables import llm_tables as rules
    return {**_llm_rules(), "lead_words": rules.LEAD_WORDS, "lead_chars": rules.LEAD_CHARS,
            "lead_max_tokens": rules.LEAD_MAX_TOKENS}


def _summary_rules() -> dict[str, Any]:
    from rag.ingest.tables import llm_tables as rules
    return {**_llm_rules(), "summary_words_per_token": rules.SUMMARY_WORDS_PER_TOKEN,
            "summary_chars_per_token": rules.SUMMARY_CHARS_PER_TOKEN}


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
    known = {"name", "description", "parser", "table_extractors", "table_structure", "table_formatter",
             "table_index_text", "chunker", "embedder", "lexical", "retriever", "llm", "eval", "multi_round", "query"}
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
    document_filter = retriever.params.pop("document_filter", True)
    if type(document_filter) is not bool:
        raise ConfigError(f"{path.name}：retriever.document_filter 必须为 true 或 false")
    table_return_unit = retriever.params.pop("table_return_unit", "chunk")
    if table_return_unit not in TABLE_RETURN_UNITS:
        raise ConfigError(f"{path.name}：retriever.table_return_unit 只能是 \"chunk\" 或 \"table\"")
    bm25_weight = retriever.params.pop("bm25_weight", 1.0)
    if type(bm25_weight) not in (int, float) or bm25_weight < 0:
        raise ConfigError(f"{path.name}：retriever.bm25_weight 必须为非负数")
    evaluation = data.get("eval", {})
    if not isinstance(evaluation, dict) or set(evaluation) - {"top_k"}:
        raise ConfigError(f"{path.name}：[eval] 只接受 top_k")
    eval_top_k = evaluation.get("top_k", 3)
    if type(eval_top_k) is not int or eval_top_k <= 0:
        raise ConfigError(f"{path.name}：eval.top_k 必须为正整数")
    lexical = data.get("lexical")
    if lexical is not None:
        if not isinstance(lexical, dict) or set(lexical) - {"stopwords", "stemming"}:
            raise ConfigError(f"{path.name}：[lexical] 只接受 stopwords 和 stemming")
        lexical = {"stopwords": lexical.get("stopwords", True), "stemming": lexical.get("stemming", True)}
        if not all(type(v) is bool for v in lexical.values()):
            raise ConfigError(f"{path.name}：lexical.stopwords、lexical.stemming 必须为 true 或 false")
    query = data.get("query", {})
    if not isinstance(query, dict) or set(query) - {"mode"}:
        raise ConfigError(f"{path.name}：[query] 只接受 mode")
    query_mode = query.get("mode", "retrieval")
    if query_mode not in QUERY_MODES:
        raise ConfigError(f'{path.name}：query.mode 只能是 "retrieval" 或 "multi_round"')
    table_sections = {name: _component(data[name], name, path) if name in data else None
                      for name in ("table_structure", "table_formatter", "table_index_text")}
    return AssemblyConfig(
        name, str(data.get("description", "")), path,
        _component(data["parser"], "parser", path),
        tuple(_component(item, "table_extractors", path) for item in extractors),
        table_sections["table_formatter"],
        _component(data["chunker"], "chunker", path),
        _component(data["embedder"], "embedder", path),
        retriever, top_k,
        _component(data["llm"], "llm", path),
        eval_top_k, document_filter, lexical, table_return_unit,
        table_sections["table_structure"], table_sections["table_index_text"], float(bm25_weight),
        _multi_round(data.get("multi_round", {}), path), query_mode,
    )


def _multi_round(data: Any, path: Path) -> MultiRoundConfig:
    if not isinstance(data, dict) or set(data) - {"max_llm_calls", "fallback_schedule", "units_per_attempt", "model"}:
        raise ConfigError(f"{path.name}：[multi_round] 只接受 max_llm_calls、fallback_schedule、units_per_attempt、model")
    default = MultiRoundConfig()
    calls = data.get("max_llm_calls", default.max_llm_calls)
    k = data.get("units_per_attempt", default.units_per_attempt)
    if type(calls) is not int or calls < 2:
        raise ConfigError(f"{path.name}：multi_round.max_llm_calls 必须为不小于 2 的整数")
    if type(k) is not int or k <= 0:
        raise ConfigError(f"{path.name}：multi_round.units_per_attempt 必须为正整数")
    schedule = data.get("fallback_schedule", "A")
    if schedule == "A":
        groups: tuple[tuple[str, ...], ...] = ()
    elif isinstance(schedule, list) and all(isinstance(g, list) and all(isinstance(m, str) for m in g) for g in schedule):
        groups = tuple(tuple(g) for g in schedule)
        from rag.query.multi_round.state import Schedule
        try:
            Schedule(groups)
        except ValueError as exc:
            raise ConfigError(f"{path.name}：multi_round.fallback_schedule：{exc}") from exc
    else:
        raise ConfigError(f'{path.name}：multi_round.fallback_schedule 只能是 "A" 或兜底方式分组的列表')
    model = data.get("model", default.model)
    if not isinstance(model, str) or not model:
        raise ConfigError(f"{path.name}：multi_round.model 必须为非空字符串")
    return MultiRoundConfig(calls, groups, k, model)


def config_dir(workspace: Path) -> Path:
    return workspace / "configs"


def _config_paths(workspace: Path, extra: bool) -> list[Path]:
    """configs/*.toml are the standard set; configs/extra/*.toml can be assembled by name too."""
    paths = sorted(config_dir(workspace).glob("*.toml"))
    return paths + (sorted((config_dir(workspace) / "extra").glob("*.toml")) if extra else [])


def list_configs(workspace: Path, extra: bool = False) -> list[AssemblyConfig]:
    return [load_config(path) for path in _config_paths(workspace, extra)]


def resolve_config(workspace: Path, name: str | None) -> AssemblyConfig:
    chosen = name or os.environ.get("RAG_CONFIG") or DEFAULT_CONFIG
    found = [path for path in _config_paths(workspace, True) if path.stem == chosen]
    if len(found) > 1:
        raise ConfigError(f"配置名 {chosen!r} 在 configs/ 与 configs/extra/ 中重复")
    if not found:
        available = ", ".join(p.stem for p in _config_paths(workspace, True))
        raise ConfigError(f"找不到配置 {chosen!r}。可用配置：{available or '无'}")
    return load_config(found[0])
