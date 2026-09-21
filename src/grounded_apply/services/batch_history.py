"""Pure record checks for explicit historical preparation-batch audits.

The caller owns normalized request/state policy, event chains, material custody
and the read snapshot. Decoding preserves recorded strings and accepts harmless
JSON formatting; these checks grant neither reuse nor conversion admission.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from grounded_apply.repositories import RepositoryError
from grounded_apply.services.workflow import canonical, digest, opaque, validate_workflow


_ERROR = "Batch record failed integrity checks"
_INVALID = (RepositoryError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError)
_BATCH_FIELDS = frozenset({"id", "manifest_json", "manifest_sha256", "created_at", "workflow_run_id"})
_ITEM_FIELDS = frozenset({"id", "batch_id", "position", "job_id", "spec_json", "spec_sha256"})
_EVENT_FIELDS = frozenset({"id", "item_id", "position", "at", "state_json", "previous_sha256", "event_sha256"})
_LEASE_FIELDS = frozenset({"batch_id", "owner", "expires_at", "epoch", "stop_reason"})
_WORKFLOW_FIELDS = frozenset({"id", "workflow_type", "status", "idempotency_key", "input_hash_sha256",
    "input_json", "current_step", "completed_steps_json", "generated_artifacts_json",
    "outstanding_need_info_json", "model_name", "prompt_version", "retry_policy_json",
    "failure_code", "failure_reason", "started_at", "finished_at", "created_at", "updated_at"})
_PAYLOAD_FIELDS = frozenset({"version", "manifest_sha256", "batch_schema_version", "idempotency_sha256"})
_STOP_REASONS = frozenset({"item_budget", "time_budget", "capacity_reached", "shared_failure"})


def _closed(value: object, fields: frozenset[str]) -> Mapping[str, Any]:
    if (not isinstance(value, Mapping) or any(type(key) is not str for key in value)
            or set(value) != fields):
        raise ValueError(_ERROR)
    return value


def _digest(value: object) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(_ERROR)
    return value


def _time(value: object, *, canonical_utc: bool = False) -> datetime:
    if type(value) is not str:
        raise ValueError(_ERROR)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(_ERROR)
    if canonical_utc and parsed.astimezone(UTC).isoformat(timespec="microseconds") != value:
        raise ValueError(_ERROR)
    return parsed


def _position(value: object) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(_ERROR)


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


def decode_batch_json(value: object) -> Any:
    """Decode JSON without duplicate keys, nonfinite numbers or raw errors."""
    try:
        if type(value) is not str:
            raise ValueError(_ERROR)
        return json.loads(value, object_pairs_hook=_object, parse_constant=_constant, parse_float=_float)
    except _INVALID:
        raise ValueError(_ERROR) from None


def _decoded_object(value: object) -> dict[str, Any]:
    result = decode_batch_json(value)
    if type(result) is not dict:
        raise ValueError(_ERROR)
    return result


def parse_batch_record(record: Mapping[str, Any], batch_id: str) -> dict[str, Any]:
    """Decode one closed origin row; normalized manifest policy is external."""
    try:
        row = _closed(record, _BATCH_FIELDS)
        if opaque(row["id"]) != opaque(batch_id):
            raise ValueError(_ERROR)
        opaque(row["workflow_run_id"])
        _digest(row["manifest_sha256"])
        _time(row["created_at"], canonical_utc=True)
        return {**{key: value for key, value in row.items() if key != "manifest_json"},
                "manifest": _decoded_object(row["manifest_json"])}
    except _INVALID:
        raise ValueError(_ERROR) from None


def parse_batch_item(record: Mapping[str, Any]) -> dict[str, Any]:
    """Decode item metadata; the caller binds its manifest position and spec."""
    try:
        row = _closed(record, _ITEM_FIELDS)
        for key in ("id", "batch_id", "job_id"):
            opaque(row[key])
        _position(row["position"])
        _digest(row["spec_sha256"])
        return {**{key: value for key, value in row.items() if key != "spec_json"},
                "spec": _decoded_object(row["spec_json"])}
    except _INVALID:
        raise ValueError(_ERROR) from None


def parse_batch_event(record: Mapping[str, Any]) -> dict[str, Any]:
    """Decode a checkpoint; transition, chain and state policy are external."""
    try:
        row = _closed(record, _EVENT_FIELDS)
        opaque(row["id"])
        opaque(row["item_id"])
        _position(row["position"])
        _time(row["at"], canonical_utc=True)
        _digest(row["event_sha256"])
        if row["previous_sha256"] is not None:
            _digest(row["previous_sha256"])
        return {**{key: value for key, value in row.items() if key != "state_json"},
                "state": _decoded_object(row["state_json"])}
    except _INVALID:
        raise ValueError(_ERROR) from None


def parse_batch_lease(record: Mapping[str, Any], batch_id: str) -> dict[str, Any]:
    """Validate lease shape without applying a current active-lease policy."""
    try:
        row = _closed(record, _LEASE_FIELDS)
        if opaque(row["batch_id"]) != opaque(batch_id):
            raise ValueError(_ERROR)
        _position(row["epoch"])
        if (row["owner"] is None) != (row["expires_at"] is None):
            raise ValueError(_ERROR)
        if row["owner"] is not None:
            opaque(row["owner"])
            _time(row["expires_at"], canonical_utc=True)
        if row["stop_reason"] is not None and (type(row["stop_reason"]) is not str
                or row["stop_reason"] not in _STOP_REASONS):
            raise ValueError(_ERROR)
        return dict(row)
    except _INVALID:
        raise ValueError(_ERROR) from None


def _workflow_payload(value: object) -> dict[str, Any]:
    payload = _closed(value, _PAYLOAD_FIELDS)
    if any(type(payload[key]) is not int or payload[key] != 1 for key in ("version", "batch_schema_version")):
        raise ValueError(_ERROR)
    _digest(payload["manifest_sha256"])
    _digest(payload["idempotency_sha256"])
    return dict(payload)


def validate_batch_workflow(
    workflow: Mapping[str, Any] | None, payload: Mapping[str, Any], *,
    batch_id: str, created_at: str, workflow_run_id: str,
) -> None:
    """Bind a completed batch creation workflow to already validated origin."""
    try:
        row = _closed(workflow, _WORKFLOW_FIELDS)
        opaque(batch_id)
        if opaque(row["id"]) != opaque(workflow_run_id):
            raise ValueError(_ERROR)
        created = _time(created_at, canonical_utc=True)
        expected = _workflow_payload(payload)
        actual = _workflow_payload(decode_batch_json(row["input_json"]))
        if canonical(actual) != canonical(expected):
            raise ValueError(_ERROR)
        _digest(row["idempotency_key"])
        _digest(row["input_hash_sha256"])
        validate_workflow(dict(row), "batch_create", expected)
        if (decode_batch_json(row["completed_steps_json"]) != ["validate", "persist"]
                or decode_batch_json(row["generated_artifacts_json"]) != [batch_id]
                or decode_batch_json(row["outstanding_need_info_json"]) != []
                or decode_batch_json(row["retry_policy_json"]) != {}
                or any(row[key] is not None for key in
                       ("model_name", "prompt_version", "failure_code", "failure_reason"))
                or any(row[key] != created_at for key in ("created_at", "started_at", "finished_at"))
                or _time(row["updated_at"]) < created
                or row["input_hash_sha256"] != digest(expected)):
            raise ValueError(_ERROR)
    except _INVALID:
        raise ValueError(_ERROR) from None
