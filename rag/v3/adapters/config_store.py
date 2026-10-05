"""Atomic, strict saved-configuration storage for the V3 namespace."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from rag.v3.application.assembly import (
    AssemblyError, builtin_configuration, validate_configuration,
)
from rag.v3.contracts.assembly import (
    DefaultConfigurationPointer, PluginBinding, SavedConfiguration,
)
from rag.v3.plugins.catalog import PARAMETERS


def _encoded(value: object) -> str:
    if value is None:
        return "null"
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise AssemblyError("invalid_parameter", "nonfinite decimal")
        if value == 0:
            return "0"
        return format(value.normalize(), "f")
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (tuple, list)):
        return "[" + ",".join(_encoded(item) for item in value) + "]"
    if isinstance(value, dict):
        if any(type(key) is not str for key in value):
            raise AssemblyError("invalid_configuration", "JSON keys must be strings")
        return "{" + ",".join(_encoded(key) + ":" + _encoded(value[key])
                              for key in sorted(value)) + "}"
    raise AssemblyError("invalid_configuration", f"unsupported JSON value: {type(value).__name__}")


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise AssemblyError("invalid_configuration", f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _parse_integer(value: str) -> int:
    if value == "-0":
        raise AssemblyError("invalid_configuration", "negative zero integer")
    return int(value)


def _reject_constant(value: str) -> object:
    raise AssemblyError("invalid_configuration", f"nonfinite JSON constant: {value}")


def _load_json(data: bytes) -> dict[str, object]:
    try:
        value = json.loads(data.decode("utf-8-sig"), object_pairs_hook=_unique_pairs,
                           parse_float=Decimal, parse_int=_parse_integer,
                           parse_constant=_reject_constant)
    except (UnicodeError, ValueError) as exc:
        raise AssemblyError("invalid_configuration", "configuration JSON unreadable") from exc
    if not isinstance(value, dict):
        raise AssemblyError("invalid_configuration", "configuration root must be object")
    return value


def _configuration_from_json(data: bytes) -> SavedConfiguration:
    raw = _load_json(data)
    if set(raw) != {"schema_version", "name", "bindings", "created_at"} or type(raw["bindings"]) is not list:
        raise AssemblyError("invalid_configuration", "unexpected configuration fields")
    bindings = []
    for record in raw["bindings"]:
        if type(record) is not dict or set(record) != {"slot_id", "binding_id", "plugin_id", "parameters", "order"}:
            raise AssemblyError("invalid_configuration", "unexpected binding fields")
        if type(record["parameters"]) is not dict:
            raise AssemblyError("invalid_configuration", "parameters must be object")
        for key, value in record["parameters"].items():
            spec = PARAMETERS.get(record["plugin_id"], {}).get(key)
            if spec is not None and spec.kind is tuple and type(value) is list:
                record["parameters"][key] = tuple(value)
        bindings.append(PluginBinding(**record))
    result = SavedConfiguration(raw["schema_version"], raw["name"], tuple(bindings), raw["created_at"])
    validate_configuration(result)
    return result


def _write_staged(parent: Path, data: bytes) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=parent)
    staging = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if staging.read_bytes() != data:
            raise AssemblyError("configuration_write", "staged configuration failed readback")
        return staging
    except BaseException:
        staging.unlink(missing_ok=True)
        raise


class ConfigurationStore:
    def __init__(self, system_root: Path):
        self.root = system_root.resolve()
        self.configs = self.root / "configs"

    def _path(self, name: str) -> Path:
        from rag.v3.application.assembly import NAME_PATTERN
        if type(name) is not str or not NAME_PATTERN.fullmatch(name):
            raise AssemblyError("invalid_configuration", "unsafe configuration name")
        return self.configs / (name.casefold() + ".json")

    def save_new(self, config: SavedConfiguration, *, installing_builtin: bool = False) -> Path:
        validate_configuration(config)
        if config.name.casefold() in {"plain_text", "structured"} and not installing_builtin:
            raise AssemblyError("reserved_name", "built-in names cannot be replaced")
        target = self._path(config.name)
        data = _encoded(asdict(config)).encode("utf-8")
        if _configuration_from_json(data) != config:
            raise AssemblyError("configuration_write", "serialized configuration changed on readback")
        staging = _write_staged(self.configs, data)
        try:
            os.link(staging, target)  # atomic create-if-absent; no overwrite on Windows
        except FileExistsError as exc:
            raise AssemblyError("duplicate_name", config.name) from exc
        finally:
            staging.unlink(missing_ok=True)
        if self.load(config.name) != config:
            raise AssemblyError("configuration_write", "published configuration failed readback")
        return target

    def load(self, name: str) -> SavedConfiguration:
        path = self._path(name)
        try:
            config = _configuration_from_json(path.read_bytes())
        except FileNotFoundError as exc:
            raise AssemblyError("unknown_configuration", name) from exc
        if config.name.casefold() != name.casefold() or path.name != config.name.casefold() + ".json":
            raise AssemblyError("invalid_configuration", "name and canonical file path disagree")
        return config

    def list_names(self) -> tuple[str, ...]:
        if not self.configs.exists():
            return ()
        names = []
        for path in sorted(self.configs.glob("*.json"), key=lambda p: p.name.casefold()):
            names.append(self.load(path.stem).name)
        return tuple(names)

    def set_default(self, name: str) -> DefaultConfigurationPointer:
        selected = self.load(name)
        pointer = DefaultConfigurationPointer("default_config_v3", selected.name)
        data = _encoded(asdict(pointer)).encode("utf-8")
        staging = _write_staged(self.root, data)
        target = self.root / "default-config.json"
        try:
            os.replace(staging, target)
        finally:
            staging.unlink(missing_ok=True)
        if self.get_default() != pointer:
            raise AssemblyError("configuration_write", "default pointer failed readback")
        return pointer

    def get_default(self) -> DefaultConfigurationPointer:
        try:
            raw = _load_json((self.root / "default-config.json").read_bytes())
        except FileNotFoundError as exc:
            raise AssemblyError("missing_default", "default pointer is absent") from exc
        if set(raw) != {"schema_version", "name"} or raw["schema_version"] != "default_config_v3":
            raise AssemblyError("invalid_configuration", "invalid default pointer")
        config = self.load(raw["name"])
        return DefaultConfigurationPointer("default_config_v3", config.name)

    def install_builtins(self) -> None:
        for name in ("plain_text", "structured"):
            expected = builtin_configuration(name)
            try:
                existing = self.load(name)
            except AssemblyError as exc:
                if exc.code != "unknown_configuration":
                    raise
                self.save_new(expected, installing_builtin=True)
            else:
                if existing != expected:
                    raise AssemblyError("builtin_modified", f"installed {name} differs from packaged binding")
        try:
            self.get_default()
        except AssemblyError as exc:
            if exc.code != "missing_default":
                raise
            self.set_default("structured")
