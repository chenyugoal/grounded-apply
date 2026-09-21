"""Strict saved-search origins without runtime access or execution policy.

The caller supplies the registered manifest normalizer and owns the read
snapshot. These bindings retain stored bytes and do not authorize execution.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from grounded_apply.repositories import RepositoryError
from grounded_apply.services.workflow import canonical, digest, opaque, validate_workflow


_ERROR = "Saved search failed integrity checks"
_INVALID = (RepositoryError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError)
_SEARCH_FIELDS = frozenset({"id", "manifest_json", "manifest_sha256", "created_at", "workflow_run_id"})
_WORKFLOW_FIELDS = frozenset({"id", "workflow_type", "status", "idempotency_key", "input_hash_sha256",
    "input_json", "current_step", "completed_steps_json", "generated_artifacts_json",
    "outstanding_need_info_json", "model_name", "prompt_version", "retry_policy_json",
    "failure_code", "failure_reason", "started_at", "finished_at", "created_at", "updated_at"})
_PAYLOAD_FIELDS = frozenset({"version", "manifest_sha256", "idempotency_sha256"})


def _closed(value: object, fields: frozenset[str]) -> Mapping[str, Any]:
    if (not isinstance(value, Mapping) or any(type(key) is not str for key in value)
            or set(value) != fields):
        raise ValueError(_ERROR)
    return value


def _digest(value: object) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(_ERROR)
    return value


def _time(value: object) -> datetime:
    if type(value) is not str:
        raise ValueError(_ERROR)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(_ERROR)
    return parsed


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(_ERROR)
        result[key] = value
    return result


def _constant(value: str) -> Any:
    raise ValueError(_ERROR)


def _float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(_ERROR)
    return result


def _json(value: object) -> Any:
    if type(value) is not str:
        raise ValueError(_ERROR)
    return json.loads(value, object_pairs_hook=_object, parse_constant=_constant, parse_float=_float)


def validate_search_scope_record(
    record: Mapping[str, Any], workflow: Mapping[str, Any] | None, search_id: str, *,
    normalize_manifest: Callable[[object], dict[str, Any]],
) -> dict[str, Any]:
    """Return the unchanged normalized manifest after strict origin validation."""
    try:
        row = _closed(record, _SEARCH_FIELDS)
        run = _closed(workflow, _WORKFLOW_FIELDS)
        if (opaque(row["id"]) != opaque(search_id)
                or opaque(row["workflow_run_id"]) != opaque(run["id"])):
            raise ValueError(_ERROR)
        created = _time(row["created_at"])
        if created.astimezone(UTC).isoformat(timespec="microseconds") != row["created_at"]:
            raise ValueError(_ERROR)
        manifest = _json(row["manifest_json"])
        normalized = normalize_manifest(manifest)
        if (type(manifest) is not dict or type(normalized) is not dict
                or canonical(manifest) != canonical(normalized)
                or _digest(row["manifest_sha256"]) != digest(normalized)):
            raise ValueError(_ERROR)
        key = _digest(run["idempotency_key"])
        expected = {"version": 1, "manifest_sha256": row["manifest_sha256"], "idempotency_sha256": key}
        actual = _closed(_json(run["input_json"]), _PAYLOAD_FIELDS)
        if (type(actual["version"]) is not int or actual["version"] != 1
                or canonical(dict(actual)) != canonical(expected)
                or _digest(run["input_hash_sha256"]) != digest(expected)
                or search_id != str(uuid5(NAMESPACE_URL, "grounded-apply.search@1/" + key))):
            raise ValueError(_ERROR)
        validate_workflow(dict(run), "search_configure", expected)
        for field, value in (("completed_steps_json", ["validate", "persist"]),
                             ("generated_artifacts_json", [search_id]),
                             ("outstanding_need_info_json", []), ("retry_policy_json", {})):
            if canonical(_json(run[field])) != canonical(value):
                raise ValueError(_ERROR)
        if (any(run[field] is not None for field in ("model_name", "prompt_version", "failure_code", "failure_reason"))
                or any(run[field] != row["created_at"] for field in ("created_at", "started_at", "finished_at"))
                or _time(run["updated_at"]) < created):
            raise ValueError(_ERROR)
        return normalized
    except _INVALID:
        raise ValueError(_ERROR) from None
