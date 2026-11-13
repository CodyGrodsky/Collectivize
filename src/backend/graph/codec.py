"""Convert graph records to and from JSON-safe dicts for persistence.

Encoding is driven by each dataclass's type hints, so enums, datetimes,
timedeltas, tuples and nested dataclasses round-trip exactly. Values typed
`Any` (e.g. EventChange old/new values) use tagged encodings.

Decoding rebuilds objects through their constructors, so every
`__post_init__` validation runs again on data read from the database.
"""

from __future__ import annotations

import types
from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta
from enum import Enum
from functools import cache
from typing import Any, Union, get_args, get_origin, get_type_hints

from backend import objects
from backend.objects import GraphRecord

_DATETIME = "$datetime"
_TIMEDELTA = "$timedelta"


@cache
def record_types() -> dict[str, type[GraphRecord]]:
    """Every node/edge class, keyed by the type name stored in the DB."""
    return {
        cls.__name__: cls
        for cls in vars(objects).values()
        if isinstance(cls, type) and is_dataclass(cls) and issubclass(cls, GraphRecord)
    }


def type_names_for(cls: type) -> list[str]:
    """Stored type names that are `cls` or one of its subclasses."""
    return [name for name, record in record_types().items() if issubclass(record, cls)]


def encode_record(record: GraphRecord) -> dict[str, Any]:
    return _encode_dataclass(record)


def decode_record(type_name: str, data: dict[str, Any]) -> GraphRecord:
    try:
        cls = record_types()[type_name]
    except KeyError:
        raise ValueError(f"unknown record type {type_name!r}") from None
    return _decode_dataclass(cls, data)


# --- internals ---------------------------------------------------------------


@cache
def _hints(cls: type) -> dict[str, Any]:
    return get_type_hints(cls)


def _unwrap_optional(hint: Any) -> Any:
    if get_origin(hint) in (Union, types.UnionType):
        args = [a for a in get_args(hint) if a is not type(None)]
        return args[0] if len(args) == 1 else Any
    return hint


def _encode_dataclass(obj: Any) -> dict[str, Any]:
    hints = _hints(type(obj))
    return {f.name: _encode(getattr(obj, f.name), hints[f.name]) for f in fields(obj)}


def _decode_dataclass(cls: type, data: dict[str, Any]) -> Any:
    hints = _hints(cls)
    names = {f.name for f in fields(cls)}
    unknown = set(data) - names
    if unknown:
        raise ValueError(f"unexpected fields for {cls.__name__}: {sorted(unknown)}")
    return cls(**{name: _decode(value, hints[name]) for name, value in data.items()})


def _encode(value: Any, hint: Any) -> Any:
    if value is None:
        return None
    hint = _unwrap_optional(hint)
    if hint is Any:
        return _encode_tagged(value)
    if get_origin(hint) is tuple:
        item_hint = get_args(hint)[0]
        return [_encode(v, item_hint) for v in value]
    if isinstance(hint, type) and issubclass(hint, Enum):
        return value.value
    if hint is datetime:
        return value.isoformat()
    if hint is timedelta:
        return value.total_seconds()
    if is_dataclass(hint):
        return _encode_dataclass(value)
    return value


def _decode(raw: Any, hint: Any) -> Any:
    if raw is None:
        return None
    hint = _unwrap_optional(hint)
    if hint is Any:
        return _decode_tagged(raw)
    if get_origin(hint) is tuple:
        item_hint = get_args(hint)[0]
        return tuple(_decode(v, item_hint) for v in raw)
    if isinstance(hint, type) and issubclass(hint, Enum):
        return hint(raw)
    if hint is datetime:
        return datetime.fromisoformat(raw)
    if hint is timedelta:
        return timedelta(seconds=raw)
    if is_dataclass(hint):
        return _decode_dataclass(hint, raw)
    return raw


def _encode_tagged(value: Any) -> Any:
    if isinstance(value, datetime):
        return {_DATETIME: value.isoformat()}
    if isinstance(value, timedelta):
        return {_TIMEDELTA: value.total_seconds()}
    if isinstance(value, Enum):
        return value.value
    return value


def _decode_tagged(raw: Any) -> Any:
    if isinstance(raw, dict) and _DATETIME in raw:
        return datetime.fromisoformat(raw[_DATETIME])
    if isinstance(raw, dict) and _TIMEDELTA in raw:
        return timedelta(seconds=raw[_TIMEDELTA])
    return raw
