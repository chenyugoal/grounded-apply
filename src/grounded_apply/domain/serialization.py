"""Small JSON-safe serialization helpers for domain records."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any

from .models import JsonValue


def to_jsonable(value: Any) -> JsonValue:
    """Recursively convert a domain value to standard JSON-compatible types."""

    if isinstance(value, Enum):
        return to_jsonable(value.value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("cannot serialize a naive datetime")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: to_jsonable(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings")
            result[key] = to_jsonable(item)
        return result
    if isinstance(value, (tuple, list, set, frozenset)):
        return [to_jsonable(item) for item in value]
    raise TypeError(f"cannot serialize {type(value).__name__} to JSON")


def to_json_dict(value: Any) -> dict[str, JsonValue]:
    """Serialize a domain record and require an object at the root."""

    result = to_jsonable(value)
    if not isinstance(result, dict):
        raise TypeError("serialized root must be a JSON object")
    return result
