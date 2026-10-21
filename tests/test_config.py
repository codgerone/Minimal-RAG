from pathlib import Path

import pytest

from rag.config import ConfigError, list_configs, load_config, resolve_config
from rag.registry import assemble

ROOT = Path(__file__).resolve().parents[1]

BASE = """name = "{name}"
[parser]
use = "{parser}"
{extra}
[chunker]
use = "{chunker}"
[embedder]
use = "e5_small"
[retriever]
use = "semantic"
top_k = 4
[llm]
use = "openrouter"
"""


TABLES = """[table_structure]
use = "header_rules"
[table_formatter]
use = "markdown_rows"
[table_index_text]
use = "rows"
"""


def write(tmp_path: Path, name="x", parser="pymupdf_pages", chunker="characters", extra="") -> Path:
    path = tmp_path / f"{name}.toml"
    path.write_text(BASE.format(name=name, parser=parser, chunker=chunker, extra=extra), encoding="utf-8")
    return path


def test_builtin_configs_are_valid_and_differ():
    names = [c.name for c in list_configs(ROOT, extra=True)]
    assert names[:7] == ["structured", "structured_labeled", "structured_llm", "structured_llm_labeled",
                         "structured_llm_labeled_lead", "structured_llm_lead", "structured_summary"]
    assert names[7:] == ["plain_text"]
    assemblies = [assemble(resolve_config(ROOT, name)) for name in names]   # extra/ resolved by name
    assert len({a.fingerprint() for a in assemblies}) == len(names)
    plain = assemblies[names.index("plain_text")]
    assert len(assemblies[0].extractors) == 4 and not plain.extractors and plain.table_steps is None


def test_query_settings_do_not_change_fingerprint(tmp_path):
    first = assemble(load_config(write(tmp_path)))
    path = write(tmp_path)
    path.write_text(path.read_text(encoding="utf-8").replace("top_k = 4", "top_k = 9"), encoding="utf-8")
    assert assemble(load_config(path)).fingerprint() == first.fingerprint()


def test_build_settings_change_fingerprint(tmp_path):
    first = assemble(load_config(write(tmp_path)))
    second = assemble(load_config(write(tmp_path, extra="").with_name("x.toml")))
    path = tmp_path / "x.toml"
    path.write_text(path.read_text(encoding="utf-8").replace('use = "characters"',
                    'use = "characters"\nchunk_size = 500'), encoding="utf-8")
    assert assemble(load_config(path)).fingerprint() != first.fingerprint() == second.fingerprint()


@pytest.mark.parametrize("kwargs, message", [
    ({"parser": "nope"}, "没有名为 'nope'"),
    ({"extra": '[[table_extractors]]\nuse = "camelot"'}, "不提供表格位置"),
    ({"parser": "docling_layout", "chunker": "characters"}, "只能搭配 pymupdf_pages"),
    ({"parser": "docling_layout", "chunker": "structured_tokens"}, "需要配置 [table_structure]、[table_formatter]"),
    ({"parser": "docling_layout", "chunker": "structured_tokens",
      "extra": TABLES + '[[table_extractors]]\nuse = "camelot"\n[[table_extractors]]\nuse = "camelot"'},
     "重复"),
    ({"parser": "docling_layout", "chunker": "structured_tokens",
      "extra": TABLES.replace('use = "rows"', 'use = "rows_with_lead"')}, "rows_with_lead 需要"),
    ({"parser": "docling_layout", "chunker": "structured_tokens",
      "extra": TABLES.replace('use = "header_rules"', 'use = "llm_annotation"\nmodel = "m"')
               .replace('use = "rows"', 'use = "summary"\nmodel = "m"')}, "summary 只能搭配"),
    ({"parser": "docling_layout", "chunker": "structured_tokens",
      "extra": TABLES.replace('use = "header_rules"', 'use = "llm_annotation"')}, "需要 model"),
    ({"parser": "pymupdf_pages", "chunker": "characters", "extra": TABLES}, "不产出表格"),
])
def test_invalid_assemblies_are_rejected_before_any_work(tmp_path, kwargs, message):
    with pytest.raises(ConfigError, match=message.replace("[", r"\[").replace("]", r"\]")):
        assemble(load_config(write(tmp_path, **kwargs)))


def test_unknown_parameter_is_reported(tmp_path):
    path = write(tmp_path)
    path.write_text(path.read_text(encoding="utf-8").replace('use = "characters"',
                    'use = "characters"\nchunk_sise = 10'), encoding="utf-8")
    with pytest.raises(ConfigError, match="参数无效"):
        assemble(load_config(path))


def test_name_must_match_file(tmp_path):
    path = write(tmp_path, name="other")
    with pytest.raises(ConfigError, match="文件名一致"):
        load_config(path.rename(tmp_path / "x.toml"))


def test_document_filter_is_a_query_setting(tmp_path):
    path = write(tmp_path)
    first = assemble(load_config(path))
    assert first.config.document_filter is True
    path.write_text(path.read_text(encoding="utf-8").replace("top_k = 4", "top_k = 4\ndocument_filter = false"),
                    encoding="utf-8")
    second = assemble(load_config(path))
    assert second.config.document_filter is False
    assert second.fingerprint() == first.fingerprint()
    path.write_text(path.read_text(encoding="utf-8").replace("= false", '= "no"'), encoding="utf-8")
    with pytest.raises(ConfigError, match="document_filter"):
        load_config(path)


def test_structured_chunk_budget_is_counted_with_e5_tokenizer():
    from rag.ingest.chunkers.tokens import E5TokenCounter
    structured = assemble(resolve_config(ROOT, "structured"))
    assert isinstance(structured.chunker.counter, E5TokenCounter)
