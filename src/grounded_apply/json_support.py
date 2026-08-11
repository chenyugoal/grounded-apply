"""Small JSON helpers shared by the dependency-free CLI and repositories."""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import json
from pathlib import Path
from typing import Any


def to_jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: to_jsonable(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_jsonable(item) for item in value]
    return value


def dumps(value: Any, *, pretty: bool = True) -> str:
    return json.dumps(
        to_jsonable(value),
        ensure_ascii=False,
        indent=2 if pretty else None,
        sort_keys=True,
    )
