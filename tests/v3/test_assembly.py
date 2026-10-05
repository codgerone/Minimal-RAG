"""Static assembly and identity gates from the reviewed V3 matrix."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from rag.v3.adapters.config_store import ConfigurationStore
from rag.v3.application.assembly import (
    AssemblyError, build_fingerprint, build_projection, builtin_configuration,
    canonical_json_bytes, effective_builtin_configuration, validate_configuration,
)
from rag.v3.contracts.assembly import PluginBinding


def changed(config, slot_id: str, *, plugin_id: str | None = None,
            parameters: dict | None = None):
    bindings = tuple(replace(item,
                     plugin_id=plugin_id or item.plugin_id,
                     parameters=item.parameters if parameters is None else parameters)
                     if item.slot_id == slot_id else item for item in config.bindings)
    return replace(config, bindings=bindings)


def with_bindings(config, *added: PluginBinding):
    return replace(config, bindings=(*config.bindings, *added))


def assert_rule(config, rule_id: str):
    with pytest.raises(AssemblyError) as raised:
        validate_configuration(config)
    assert raised.value.evaluation is not None
    assert raised.value.evaluation.rule_id == rule_id


def test_both_installed_assemblies_are_complete_and_distinct():
    plain = builtin_configuration("plain_text")
    structured = builtin_configuration("structured")
    assert validate_configuration(plain)
    assert validate_configuration(structured)
    assert len({item.binding_id for item in structured.bindings
                if item.slot_id == "document_processor.table_extractor"}) == 4
    assert build_fingerprint(plain) != build_fingerprint(structured)


def test_builtin_environment_overrides_change_effective_identity_without_rewriting_saved_config():
    plain = builtin_configuration("plain_text")
    effective = effective_builtin_configuration(plain, {
        "CHUNK_SIZE": "401", "CHUNK_OVERLAP": "40",
        "EMBEDDING_MODEL_REVISION": "alternate-revision",
    })
    assert effective != plain
    assert build_fingerprint(effective) != build_fingerprint(plain)
    assert build_fingerprint(builtin_configuration("plain_text")) == build_fingerprint(plain)
    assert all(item.parameters.get("revision") == "alternate-revision"
               for item in effective.bindings if item.plugin_id == "embedder.e5_small")
    structured = builtin_configuration("structured")
    changed = effective_builtin_configuration(structured, {
        "V2_MAX_INPUT_TOKENS": "480", "V2_TEXT_OVERLAP_TOKENS": "20",
        "CHUNK_SIZE": "401",
    })
    assert build_fingerprint(changed) != build_fingerprint(structured)
    assert next(item for item in changed.bindings if item.slot_id == "indexer.chunker").parameters == {
        "maximum_input_tokens": 480, "text_overlap_tokens": 20}
    with pytest.raises(AssemblyError):
        effective_builtin_configuration(plain, {"CHUNK_SIZE": "10", "CHUNK_OVERLAP": "10"})
    answer_model = effective_builtin_configuration(plain, {
        "OPENROUTER_MODEL": "another/answer-model"})
    assert build_fingerprint(answer_model) == build_fingerprint(plain)
    assert next(item for item in answer_model.bindings
                if item.slot_id == "chatbot.language_model").parameters["model_name"] == (
                    "another/answer-model")
    custom = replace(plain, name="review_custom")
    assert effective_builtin_configuration(custom, {
        "CHUNK_SIZE": "401", "OPENROUTER_MODEL": "another/answer-model"}) == custom
    assert "retriever.embedder" not in {item.slot_id for item in build_projection(plain).bindings}


@pytest.mark.parametrize("name,wrong_chunker", [
    ("plain_text", "chunker.structured_tokens"),
    ("structured", "chunker.page_characters"),
])
def test_cross_parser_chunker_is_rejected(name, wrong_chunker):
    config = builtin_configuration(name)
    assert_rule(changed(config, "indexer.chunker", plugin_id=wrong_chunker), "A01")


def test_docling_requires_content_preparation():
    config = builtin_configuration("structured")
    config = changed(config, "document_processor.document_assembler",
                     parameters={"table_content_enabled": False})
    config = replace(config, bindings=tuple(item for item in config.bindings if item.slot_id not in {
        "document_assembler.table_content_preparation", "table_content_preparation.header_detector",
        "table_content_preparation.table_serializer",
    }))
    assert_rule(config, "A02")


def test_plain_parser_rejects_complete_extraction_branch():
    config = builtin_configuration("plain_text")
    config = with_bindings(config,
        PluginBinding("document_processor.table_extractor", "pymupdf", "extractor.pymupdf", {}, 0),
        PluginBinding("document_processor.table_selector", "default", "selector.table_v1", {}, 0),
        PluginBinding("table_selector.pdf_evidence_reader", "default", "pdf_evidence.pymupdf", {}, 0),
    )
    assert_rule(config, "A03")


def test_structured_single_page_prompt_is_rejected():
    config = builtin_configuration("structured")
    assert_rule(changed(config, "chatbot.prompt_builder",
                        parameters={"source_label_style": "single_page"}), "A07")


def test_fixed_table_business_threshold_cannot_be_overridden():
    config = builtin_configuration("structured")
    with pytest.raises(AssemblyError) as error:
        validate_configuration(changed(config, "document_processor.table_selector",
            parameters={"candidate_coverage_minimum": Decimal("0.60")}))
    assert error.value.code == "invalid_parameter"


def test_query_changes_do_not_rebuild_but_build_changes_do():
    plain = builtin_configuration("plain_text")
    query = changed(plain, "chatbot.prompt_builder",
                    parameters={"source_label_style": "page_set"})
    assert build_fingerprint(query) == build_fingerprint(plain)
    build = changed(plain, "indexer.chunker", parameters={"chunk_size_characters": 701})
    assert build_fingerprint(build) != build_fingerprint(plain)


def test_decimal_fingerprint_normalization_without_float_round_trip():
    structured = builtin_configuration("structured")
    a = changed(structured, "document_processor.table_selector",
                parameters={"candidate_coverage_minimum": Decimal("0.6500")})
    b = changed(structured, "document_processor.table_selector",
                parameters={"candidate_coverage_minimum": Decimal("0.65")})
    assert build_fingerprint(a) == build_fingerprint(b)
    assert canonical_json_bytes({"x": Decimal("-0.000")}) == b'{"x":"0"}'
    with pytest.raises(AssemblyError):
        canonical_json_bytes({"x": Decimal("NaN")})


def test_invalid_binding_fails_before_resources_are_created():
    plain = builtin_configuration("plain_text")
    with pytest.raises(AssemblyError) as raised:
        validate_configuration(changed(plain, "indexer.chunker",
                                       parameters={"chunk_size_characters": True}))
    assert raised.value.code == "invalid_parameter"


def test_install_and_save_are_readback_checked_and_do_not_overwrite(tmp_path):
    store = ConfigurationStore(tmp_path / "system-v3")
    store.install_builtins()
    assert store.list_names() == ("plain_text", "structured")
    assert store.get_default().name == "structured"
    store.install_builtins()
    custom = replace(builtin_configuration("plain_text"), name="trial")
    store.save_new(custom)
    assert store.load("TRIAL") == custom
    with pytest.raises(AssemblyError) as raised:
        store.save_new(replace(custom, name="TRIAL"))
    assert raised.value.code == "duplicate_name"
    assert store.load("trial") == custom


def test_config_store_rejects_duplicate_keys_and_malformed_pointer(tmp_path):
    store = ConfigurationStore(tmp_path / "system-v3")
    store.install_builtins()
    path = store.configs / "bad.json"
    path.write_text('{"schema_version":"assembly_config_v3","name":"bad","name":"bad"}', encoding="utf-8")
    with pytest.raises(AssemblyError) as raised:
        store.load("bad")
    assert raised.value.code == "invalid_configuration"
    (store.root / "default-config.json").write_text('{"schema_version":"default_config_v3","name":"missing"}', encoding="utf-8")
    with pytest.raises(AssemblyError) as raised:
        store.get_default()
    assert raised.value.code == "unknown_configuration"
