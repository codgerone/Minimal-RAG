"""Immutable assembly records from the V3 assembly contract."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, TypeAlias

from rag.v3.contracts.storage import IndexIdentity


ParameterValue: TypeAlias = (
    "None | bool | str | int | Decimal | tuple[ParameterValue, ...] | dict[str, ParameterValue]"
)
ConditionId: TypeAlias = Literal[
    "table_extractor_present_v1", "assembler_consumes_table_v1"
]


@dataclass(frozen=True)
class InterfacePort:
    port_id: str
    model_id: str
    active_when_all: tuple[ConditionId, ...] = ()


@dataclass(frozen=True)
class InterfaceDefinition:
    interface_id: str
    contract_version: str
    input_ports: tuple[InterfacePort, ...]
    output_ports: tuple[InterfacePort, ...]
    required_capabilities: tuple[str, ...] = ()
    allowed_plugin_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SlotDefinition:
    slot_id: str
    owner_interface_id: str | None
    interface_id: str
    access: Literal["required", "optional", "conditional"]
    cardinality: Literal["one", "multi"]
    condition_id: ConditionId | None = None
    required_capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class DependencyEdge:
    source_slot_id: str
    source_output_port_id: str
    target_slot_id: str
    target_input_port_id: str
    delivery: Literal["direct", "ordered_collect"] = "direct"
    active_when_all: tuple[ConditionId, ...] = ()


@dataclass(frozen=True)
class BoundaryMapping:
    owner_interface_id: str
    owner_port_id: str
    child_slot_id: str
    child_port_id: str
    kind: Literal["ingress", "egress"]
    field_path: str | None = None
    active_when_all: tuple[ConditionId, ...] = ()


@dataclass(frozen=True)
class OwnerConstruction:
    owner_interface_id: str
    target_slot_id: str
    target_input_port_id: str
    construction_contract_id: str
    active_when_all: tuple[ConditionId, ...] = ()


@dataclass(frozen=True)
class PluginDefinition:
    plugin_id: str
    implementation_version: str
    interface_id: str
    supported_contract_versions: tuple[str, ...]
    capabilities: tuple[str, ...]
    parameter_schema_id: str
    factory_id: str
    resource_requirements: tuple[str, ...]
    build_effect: bool


@dataclass(frozen=True)
class PluginBinding:
    slot_id: str
    binding_id: str
    plugin_id: str
    parameters: dict[str, ParameterValue]
    order: int


@dataclass(frozen=True)
class SavedConfiguration:
    schema_version: Literal["assembly_config_v3"]
    name: str
    bindings: tuple[PluginBinding, ...]
    created_at: str


@dataclass(frozen=True)
class DefaultConfigurationPointer:
    schema_version: Literal["default_config_v3"]
    name: str


@dataclass(frozen=True)
class BuildProjectionBinding:
    slot_id: str
    binding_id: str
    order: int
    plugin_id: str
    implementation_version: str
    contract_version: str
    parameter_schema_id: str
    parameters: dict[str, ParameterValue]
    rule_versions: dict[str, str]


@dataclass(frozen=True)
class BuildProjection:
    projection_schema: Literal["build_projection_v3"]
    interface_contract_major: Literal[1]
    document_schema_version: str
    chunk_schema_version: str
    artifact_schema_version: str
    vector_metadata_schema_version: str
    manifest_schema_version: str
    bindings: tuple[BuildProjectionBinding, ...]


@dataclass(frozen=True)
class CompatibilityEvaluation:
    rule_id: Literal["A01", "A02", "A03", "A04", "A05", "A06", "A07", "A08", "A09"]
    status: Literal["passed", "failed", "deferred"]
    failure_reason: str | None
    participating_slot_ids: tuple[str, ...]
    selected_plugin_ids: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedPlugin:
    slot_id: str
    binding_id: str
    plugin_id: str
    interface_id: str
    contract_version: str
    instance_handle: object


@dataclass(frozen=True)
class RuntimeBinding:
    configuration_name: str
    index_identity: IndexIdentity
    resolved_plugins: tuple[ResolvedPlugin, ...]
    resource_scope_id: str
