"""JSON helpers: dataclass-aware encoding and atomic writes."""

from __future__ import annotations

import dataclasses
import functools
import json
import os
import types
import typing
from pathlib import Path
from typing import Any


def to_jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)
                if not f.name.startswith("_")}
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    return value


def dumps(value: Any) -> str:
    return json.dumps(to_jsonable(value), ensure_ascii=False, indent=2)


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def write_json(path: Path, value: Any) -> None:
    write_atomic(path, dumps(value) + "\n")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def from_jsonable(kind: Any, value: Any) -> Any:
    """Rebuild a value written by `to_jsonable` as `kind` (a type hint): dataclasses, tuples,
    lists, dicts, sets, literals, optionals and unions of dataclasses (told apart by field names)."""
    origin, args = typing.get_origin(kind), typing.get_args(kind)
    if kind is Any or kind is object:
        return value
    if origin is typing.Literal:
        if value not in args:
            raise ValueError(f"{value!r} is not one of {args}")
        return value
    if origin in (typing.Union, types.UnionType):
        if value is None and type(None) in args:
            return None
        options = [arg for arg in args if arg is not type(None)]
        if len(options) == 1:
            return from_jsonable(options[0], value)
        classes = [arg for arg in options if dataclasses.is_dataclass(arg)]
        if classes and isinstance(value, dict):
            fitting = [cls for cls in classes if _fits(cls, value)]
            if len(fitting) != 1:
                raise ValueError(f"cannot tell which of {classes} the value is")
            return from_jsonable(fitting[0], value)
        for option in options:   # unions of literals or scalars
            try:
                return from_jsonable(option, value)
            except (TypeError, ValueError):
                continue
        raise ValueError(f"{value!r} matches none of {options}")
    if origin is tuple:
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(from_jsonable(args[0], item) for item in value)
        if len(args) != len(value):
            raise ValueError(f"expected {len(args)} items, got {len(value)}")
        return tuple(from_jsonable(arg, item) for arg, item in zip(args, value))
    if origin in (list, set, frozenset):
        items = [from_jsonable(args[0] if args else Any, item) for item in value]
        return origin(items)
    if origin is dict:
        key, item = args or (Any, Any)
        return {int(k) if key is int else k: from_jsonable(item, v) for k, v in value.items()}
    if dataclasses.is_dataclass(kind):
        hints = _hints(kind)
        names = {f.name for f in dataclasses.fields(kind) if f.init}
        unknown = set(value) - names
        if unknown:
            raise ValueError(f"{kind.__name__} has no fields {sorted(unknown)}")
        return kind(**{name: from_jsonable(hints[name], item) for name, item in value.items()})
    if kind is float and isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    if isinstance(kind, type) and not isinstance(value, kind):
        raise TypeError(f"{value!r} is not {kind.__name__}")
    return value


@functools.cache
def _hints(cls: Any) -> dict[str, Any]:
    """Field types; names a model imports only for type checking are looked up in the model modules."""
    try:
        return typing.get_type_hints(cls)
    except NameError:
        import rag.ingest.tables.models
        import rag.models
        names = {**vars(rag.models), **vars(rag.ingest.tables.models)}
        return typing.get_type_hints(cls, localns=names)


def _fits(cls: Any, value: dict) -> bool:
    fields = {f.name: f for f in dataclasses.fields(cls) if f.init}
    required = {name for name, f in fields.items()
                if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING}
    return set(value) <= set(fields) and required <= set(value)
