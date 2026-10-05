"""Validate saved bindings once and derive deterministic build identity."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, replace
from datetime import datetime, timezone
from decimal import Decimal
from importlib.resources import files
from typing import Mapping

from rag.v3.contracts.assembly import (
    BuildProjection, BuildProjectionBinding, CompatibilityEvaluation,
    PluginBinding, SavedConfiguration,
)
from rag.v3.plugins.catalog import PARAMETERS, PLUGINS, SLOTS, ParameterField
from rag.v3.contracts.storage import IndexIdentity


NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]+\Z")
BUILD_SCHEMA_VERSIONS = {
    "document_schema_version": "parsed_document_v3",
    "chunk_schema_version": "chunk_batch_v3",
    "artifact_schema_version": "snapshot_manifest_v3",
    "vector_metadata_schema_version": "chunk_metadata_v3",
    "manifest_schema_version": "index_manifest_v3",
}
RULE_REASONS = (
    "semantic_pair_disallowed", "table_content_mismatch", "extractor_selector_mismatch",
    "audit_snapshot_unavailable", "passage_tokenizer_mismatch", "embedding_store_mismatch",
    "source_label_unsupported", "publication_scope_mismatch", "shared_conversion_mismatch",
)
PARSER_CHUNK_PAIRS = frozenset({
    ("parser.pymupdf_pages", "chunker.page_characters"),
    ("parser.docling_layout", "chunker.structured_tokens"),
})
BUILD_SLOT_IDS = frozenset({
    "system.artifact_builder", "indexer.chunker", "indexer.embedder",
    "document_processor.main_parser", "document_processor.table_extractor",
    "document_processor.table_selector", "document_processor.document_assembler",
    "document_assembler.table_content_preparation", "document_assembler.document_composer",
    "table_content_preparation.header_detector", "table_content_preparation.table_serializer",
    "publisher.vector_store", "publisher.manifest_store", "publisher.artifact_store",
})


class AssemblyError(ValueError):
    def __init__(self, code: str, detail: str, *, slot_id: str | None = None,
                 evaluation: CompatibilityEvaluation | None = None):
        self.code, self.slot_id, self.evaluation = code, slot_id, evaluation
        super().__init__(detail)


def _normalize_decimal(value: Decimal) -> str:
    if not value.is_finite():
        raise AssemblyError("invalid_parameter", "Decimal must be finite")
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _json_value(value: object) -> object:
    if isinstance(value, Decimal):
        return _normalize_decimal(value)
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    return value


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(_json_value(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def readable_json_bytes(value: object) -> bytes:
    """Serialize reviewable stored JSON; keep fingerprint JSON canonical and compact."""
    return (json.dumps(_json_value(value), ensure_ascii=False, sort_keys=True,
                       indent=2, allow_nan=False) + "\n").encode("utf-8")


def _validate_field(value: object, spec: ParameterField, name: str) -> object:
    if spec.kind is Decimal:
        if type(value) is not Decimal:
            raise AssemblyError("invalid_parameter", f"{name} must be a JSON decimal")
    elif type(value) is not spec.kind:
        raise AssemblyError("invalid_parameter", f"{name} requires {spec.kind.__name__}")
    if spec.kind is str and not value:
        raise AssemblyError("invalid_parameter", f"{name} cannot be empty")
    if spec.choices and value not in spec.choices:
        raise AssemblyError("invalid_parameter", f"{name} is outside the registered choices")
    if spec.minimum is not None and Decimal(value) < spec.minimum:
        raise AssemblyError("invalid_parameter", f"{name} is below the minimum")
    if spec.kind is Decimal and not value.is_finite():
        raise AssemblyError("invalid_parameter", f"{name} must be finite")
    if name == "metric_weights":
        if (len(value) != 4 or any(type(item) is not Decimal or not item.is_finite() or item < 0
                                   for item in value) or sum(value) != Decimal(1)):
            raise AssemblyError("invalid_parameter", "metric weights must be four finite nonnegative decimals summing to one")
    return value


def effective_parameters(binding: PluginBinding) -> dict[str, object]:
    specs = PARAMETERS.get(binding.plugin_id, {})
    unknown = binding.parameters.keys() - specs.keys()
    if unknown:
        raise AssemblyError("invalid_parameter", f"unknown parameters: {sorted(unknown)}",
                            slot_id=binding.slot_id)
    result = {}
    for name, spec in specs.items():
        value = binding.parameters.get(name, spec.default)
        result[name] = _validate_field(value, spec, name)
    if binding.plugin_id == "chunker.page_characters" and (
        result["chunk_overlap_characters"] >= result["chunk_size_characters"]
    ):
        raise AssemblyError("invalid_parameter", "character overlap must be below size",
                            slot_id=binding.slot_id)
    if binding.plugin_id == "chunker.structured_tokens" and (
        result["text_overlap_tokens"] >= result["maximum_input_tokens"]
    ):
        raise AssemblyError("invalid_parameter", "token overlap must be below maximum",
                            slot_id=binding.slot_id)
    return result


def _binding(bindings: Mapping[str, tuple[PluginBinding, ...]], slot: str) -> PluginBinding | None:
    selected = bindings.get(slot, ())
    return selected[0] if selected else None


def _compat(rule: int, passed: bool, bindings: Mapping[str, tuple[PluginBinding, ...]],
            participating: tuple[str, ...]) -> CompatibilityEvaluation:
    return CompatibilityEvaluation(
        f"A{rule:02d}", "passed" if passed else "failed",
        None if passed else RULE_REASONS[rule - 1], participating,
        tuple(item.plugin_id for slot in participating for item in bindings.get(slot, ())),
    )


def compatibility_matrix(bindings: Mapping[str, tuple[PluginBinding, ...]],
                         params: Mapping[str, dict[str, object]]) -> tuple[CompatibilityEvaluation, ...]:
    parser = _binding(bindings, "document_processor.main_parser")
    chunker = _binding(bindings, "indexer.chunker")
    assembler = _binding(bindings, "document_processor.document_assembler")
    extractor = bindings.get("document_processor.table_extractor", ())
    selector = _binding(bindings, "document_processor.table_selector")
    evidence = _binding(bindings, "table_selector.pdf_evidence_reader")
    content = _binding(bindings, "document_assembler.table_content_preparation")
    header = _binding(bindings, "table_content_preparation.header_detector")
    serializer = _binding(bindings, "table_content_preparation.table_serializer")
    enabled = bool(params[assembler.slot_id]["table_content_enabled"])
    pid, cid = parser.plugin_id, chunker.plugin_id
    results = [
        _compat(1, (pid, cid) in PARSER_CHUNK_PAIRS, bindings,
                (parser.slot_id, assembler.slot_id, chunker.slot_id)),
        _compat(2, (pid == "parser.docling_layout") == enabled and
                ((content is not None and header is not None and serializer is not None) == enabled),
                bindings, (parser.slot_id, assembler.slot_id,
                           "document_assembler.table_content_preparation",
                           "table_content_preparation.header_detector",
                           "table_content_preparation.table_serializer")),
        _compat(3, (not extractor and selector is None and evidence is None) or
                (bool(extractor) and pid == "parser.docling_layout" and
                 selector is not None and evidence is not None), bindings,
                (parser.slot_id, "document_processor.table_extractor",
                 "document_processor.table_selector", "table_selector.pdf_evidence_reader")),
        _compat(4, pid in {"parser.pymupdf_pages", "parser.docling_layout"} and
                _binding(bindings, "system.artifact_builder") is not None and
                _binding(bindings, "publisher.artifact_store") is not None,
                bindings, (parser.slot_id, "system.artifact_builder", "publisher.artifact_store")),
    ]
    passages = _binding(bindings, "indexer.embedder")
    queries = _binding(bindings, "retriever.embedder")
    ep = params[passages.slot_id]
    eq = params[queries.slot_id]
    results.append(_compat(5, cid != "chunker.structured_tokens" or
                           (params[chunker.slot_id]["maximum_input_tokens"] <= 512 and
                            ep["passage_prefix"] == "passage: " and ep["add_special_tokens"] is True),
                           bindings, (chunker.slot_id, passages.slot_id)))
    results.append(_compat(6, ep == eq, bindings,
                           (passages.slot_id, queries.slot_id,
                            "publisher.vector_store", "retriever.vector_reader")))
    prompt = _binding(bindings, "chatbot.prompt_builder")
    style = params[prompt.slot_id]["source_label_style"]
    results.append(_compat(7, style == "page_set" or cid == "chunker.page_characters",
                           bindings, (chunker.slot_id, prompt.slot_id)))
    storage_slots = ("indexer.publisher", "publisher.vector_store", "publisher.manifest_store",
                     "publisher.recovery_store", "publisher.artifact_store", "index_health.vector_reader",
                     "index_health.manifest_store", "index_health.recovery_store", "index_health.artifact_store",
                     "recovery.vector_store", "recovery.manifest_store", "recovery.recovery_store",
                     "recovery.artifact_store")
    results.append(_compat(8, all(_binding(bindings, s) is not None for s in storage_slots),
                           bindings, storage_slots))
    results.append(_compat(9, pid != "parser.docling_layout" or
                           not any(item.plugin_id == "extractor.docling" for item in extractor) or
                           params[parser.slot_id]["do_ocr"] is False, bindings,
                           (parser.slot_id, "document_processor.table_extractor")))
    return tuple(results)


def validate_configuration(config: SavedConfiguration) -> tuple[PluginBinding, ...]:
    if config.schema_version != "assembly_config_v3" or not NAME_PATTERN.fullmatch(config.name):
        raise AssemblyError("invalid_configuration", "invalid V3 configuration name or schema")
    if not config.created_at:
        raise AssemblyError("invalid_configuration", "created_at is required")
    try:
        created = datetime.fromisoformat(config.created_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AssemblyError("invalid_configuration", "created_at must be RFC3339") from exc
    if created.tzinfo is None:
        raise AssemblyError("invalid_configuration", "created_at must include timezone")
    grouped: dict[str, list[PluginBinding]] = {}
    params: dict[str, dict[str, object]] = {}
    for item in config.bindings:
        slot, plugin = SLOTS.get(item.slot_id), PLUGINS.get(item.plugin_id)
        if slot is None or plugin is None or plugin.interface_id != slot.interface_id:
            raise AssemblyError("invalid_reference", "slot or plugin interface mismatch", slot_id=item.slot_id)
        if "1.0" not in plugin.supported_contract_versions or type(item.order) is not int or item.order < 0:
            raise AssemblyError("invalid_reference", "plugin contract or binding order invalid", slot_id=item.slot_id)
        effective_parameters(item)
        grouped.setdefault(item.slot_id, []).append(item)
    for slot_id, items in grouped.items():
        slot = SLOTS[slot_id]
        if len({item.binding_id for item in items}) != len(items) or len({item.order for item in items}) != len(items):
            raise AssemblyError("invalid_cardinality", "duplicate binding identity or order", slot_id=slot_id)
        if slot.cardinality == "one" and (len(items) != 1 or items[0].order != 0):
            raise AssemblyError("invalid_cardinality", "one slot requires one order-zero binding", slot_id=slot_id)
        if slot.cardinality == "multi" and tuple(sorted(item.order for item in items)) != tuple(range(len(items))):
            raise AssemblyError("invalid_cardinality", "multi orders must be contiguous", slot_id=slot_id)
        if len({item.plugin_id for item in items}) != len(items):
            raise AssemblyError("invalid_cardinality", "duplicate plugin in multi slot", slot_id=slot_id)
    selected = {slot: tuple(sorted(items, key=lambda x: x.order)) for slot, items in grouped.items()}
    assembler = _binding(selected, "document_processor.document_assembler")
    c1 = bool(selected.get("document_processor.table_extractor"))
    c2 = bool(assembler and effective_parameters(assembler).get("table_content_enabled"))
    for slot_id, slot in SLOTS.items():
        present = bool(selected.get(slot_id))
        expected = slot.access == "required" or slot.condition_id == "table_extractor_present_v1" and c1 or slot.condition_id == "assembler_consumes_table_v1" and c2
        if slot.access == "conditional" and present != expected:
            raise AssemblyError("invalid_condition", "conditional slot presence disagrees with C1/C2", slot_id=slot_id)
        if slot.access == "required" and not present:
            raise AssemblyError("missing_required", "required slot is absent", slot_id=slot_id)
    for slot_id, items in selected.items():
        params[slot_id] = effective_parameters(items[0])
    from rag.v3.plugins.graph import validate_dependency_graph
    validate_dependency_graph()
    for evaluation in compatibility_matrix(selected, params):
        if evaluation.status == "failed":
            raise AssemblyError("invalid_assembly", evaluation.failure_reason or evaluation.rule_id,
                                evaluation=evaluation)
    return tuple(sorted(config.bindings, key=lambda x: (x.slot_id, x.order, x.binding_id)))


def build_projection(config: SavedConfiguration) -> BuildProjection:
    bindings = validate_configuration(config)
    selected: list[BuildProjectionBinding] = []
    for item in bindings:
        plugin = PLUGINS[item.plugin_id]
        if not plugin.build_effect or item.slot_id not in BUILD_SLOT_IDS:
            continue
        fields = PARAMETERS.get(item.plugin_id, {})
        effective = effective_parameters(item)
        build_params = {key: value for key, value in effective.items() if fields[key].build_effect}
        rules = {key: value for key, value in build_params.items()
                 if key.endswith("rule_version") and isinstance(value, str)}
        if item.plugin_id == "artifacts.stage_snapshot_v3":
            rules["snapshot_rule_version"] = "stage_snapshot_v3"
        selected.append(BuildProjectionBinding(item.slot_id, item.binding_id, item.order,
                        item.plugin_id, plugin.implementation_version, "1.0",
                        plugin.parameter_schema_id, build_params, rules))
    return BuildProjection("build_projection_v3", 1, **BUILD_SCHEMA_VERSIONS,
                           bindings=tuple(selected))


def build_fingerprint(config: SavedConfiguration) -> str:
    return hashlib.sha256(canonical_json_bytes(asdict(build_projection(config)))).hexdigest()


def index_identity(config: SavedConfiguration) -> IndexIdentity:
    fingerprint = build_fingerprint(config)
    return IndexIdentity(fingerprint, "index_store_v3", "rag_v3_" + fingerprint[:32],
                         ".rag/system-v3/indexes/" + fingerprint)


def make_configuration(name: str, bindings: tuple[PluginBinding, ...]) -> SavedConfiguration:
    return SavedConfiguration("assembly_config_v3", name, bindings,
                              datetime.now(timezone.utc).isoformat())


def builtin_configuration(name: str) -> SavedConfiguration:
    if name not in {"plain_text", "structured"}:
        raise AssemblyError("unknown_configuration", name)
    raw = json.loads(files("rag.v3.plugins").joinpath("builtin_configs", name + ".json").read_text(encoding="utf-8"),
                     parse_float=Decimal)
    if set(raw) != {"schema_version", "name", "bindings", "created_at"} or raw["name"] != name:
        raise AssemblyError("invalid_configuration", "invalid installed configuration")
    bindings = tuple(PluginBinding(**record) for record in raw["bindings"])
    result = SavedConfiguration(raw["schema_version"], name, bindings, raw["created_at"])
    validate_configuration(result)
    return result


def effective_builtin_configuration(config: SavedConfiguration,
                                    environment: Mapping[str, str]) -> SavedConfiguration:
    """Apply explicit legacy environment overrides without changing saved bindings."""
    if config.name not in {"plain_text", "structured"}:
        return config
    chunk_variables = ({"CHUNK_SIZE": "chunk_size_characters",
                        "CHUNK_OVERLAP": "chunk_overlap_characters"}
                       if config.name == "plain_text" else
                       {"V2_MAX_INPUT_TOKENS": "maximum_input_tokens",
                        "V2_TEXT_OVERLAP_TOKENS": "text_overlap_tokens"})
    chunk_overrides = {}
    for variable, parameter in chunk_variables.items():
        if variable not in environment:
            continue
        try:
            chunk_overrides[parameter] = int(environment[variable])
        except (TypeError, ValueError) as exc:
            raise AssemblyError("invalid_parameter", f"{variable} must be an integer") from exc
    embedding_overrides = {
        parameter: environment[variable]
        for variable, parameter in (("EMBEDDING_MODEL", "model_name"),
                                    ("EMBEDDING_MODEL_REVISION", "revision"))
        if variable in environment
    }
    llm_overrides = ({"model_name": environment["OPENROUTER_MODEL"]}
                     if "OPENROUTER_MODEL" in environment else {})
    if not chunk_overrides and not embedding_overrides and not llm_overrides:
        return config
    bindings = []
    for binding in config.bindings:
        changes = (chunk_overrides if binding.slot_id == "indexer.chunker" else
                   embedding_overrides if binding.plugin_id == "embedder.e5_small" else
                   llm_overrides if binding.slot_id == "chatbot.language_model" else {})
        bindings.append(replace(binding, parameters={**binding.parameters, **changes})
                        if changes else binding)
    effective = replace(config, bindings=tuple(bindings))
    validate_configuration(effective)
    return effective
