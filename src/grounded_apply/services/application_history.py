"""Pure, strict record bindings for saved manual application history.

Callers own the read snapshot, event chain and factual material validation.
JSON values and recorded identities are checked without rewriting stored bytes.
These consistency checks do not authenticate an actor or grant current use.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from grounded_apply.domain.application_states import ApplicationState
from grounded_apply.repositories import RepositoryError
from grounded_apply.services.workflow import canonical, digest, opaque, validate_workflow


_ERROR = "Application record failed integrity checks"
_APPLICATION_FIELDS = frozenset({"id", "job_id", "created_at", "workflow_run_id"})
_EVENT_FIELDS = frozenset({"id", "application_id", "position", "state", "actor_id", "at",
    "previous_sha256", "event_sha256", "payload_json", "workflow_run_id"})
_EVENT_PAYLOAD_FIELDS = frozenset({"material_id", "bundle_sha256", "human_confirmed_submission"})
_WORKFLOW_FIELDS = frozenset({"id", "workflow_type", "status", "idempotency_key", "input_hash_sha256",
    "input_json", "current_step", "completed_steps_json", "generated_artifacts_json",
    "outstanding_need_info_json", "model_name", "prompt_version", "retry_policy_json",
    "failure_code", "failure_reason", "started_at", "finished_at", "created_at", "updated_at"})
_CREATE_FIELDS = frozenset({"version", "job_id", "actor_id", "idempotency_sha256"})
_TRANSITION_FIELDS = frozenset({"version", "application_id", "previous_sha256", "target_state", "actor_id",
    "material_id", "bundle_sha256", "human_confirmed_submission", "idempotency_sha256"})
_SUBMISSION_FIELDS = frozenset({"application_id", "event_id", "material_id", "snapshot_json",
    "snapshot_sha256", "submitted_at"})
_SUBMISSION_VALUE_FIELDS = frozenset({"schema_version", "job", "material_id", "bundle_sha256",
    "pdf_sha256", "structure", "answers", "approval", "recorded_at", "human_confirmed_submission",
    "external_action_taken", "material_bytes_retained_in"})


def _closed(value: object, fields: frozenset[str]) -> Mapping[str, Any]:
    if (not isinstance(value, Mapping) or any(type(key) is not str for key in value)
            or set(value) != fields):
        raise ValueError(_ERROR)
    return value


def _time(value: object) -> datetime:
    if type(value) is not str:
        raise ValueError(_ERROR)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(_ERROR)
    return parsed


def _digest(value: object) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(_ERROR)
    return value


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(_ERROR)
        result[key] = value
    return result


def _constant(value: str) -> Any:
    raise ValueError(_ERROR)


def _json(value: object) -> Any:
    if type(value) is not str:
        raise ValueError(_ERROR)
    return json.loads(value, object_pairs_hook=_object, parse_constant=_constant)


def _state(value: object) -> ApplicationState:
    if type(value) is not str:
        raise ValueError(_ERROR)
    return ApplicationState(value)


def _event_payload(value: object, state: ApplicationState) -> Mapping[str, Any]:
    if state is ApplicationState.DISCOVERED:
        return _closed(value, frozenset())
    payload = _closed(value, _EVENT_PAYLOAD_FIELDS)
    if state in {ApplicationState.READY_FOR_REVIEW, ApplicationState.APPLIED}:
        opaque(payload["material_id"])
        _digest(payload["bundle_sha256"])
        if payload["human_confirmed_submission"] is not (state is ApplicationState.APPLIED):
            raise ValueError(_ERROR)
    elif (payload["material_id"] is not None or payload["bundle_sha256"] is not None
            or payload["human_confirmed_submission"] is not False):
        raise ValueError(_ERROR)
    return payload


def _workflow_payload(value: object, kind: str, application_id: str) -> dict[str, Any]:
    if kind == "application_create":
        payload = _closed(value, _CREATE_FIELDS)
        opaque(payload["job_id"])
    elif kind == "application_transition":
        payload = _closed(value, _TRANSITION_FIELDS)
        if opaque(payload["application_id"]) != application_id:
            raise ValueError(_ERROR)
        _digest(payload["previous_sha256"])
        state = _state(payload["target_state"])
        if state is ApplicationState.DISCOVERED:
            raise ValueError(_ERROR)
        _event_payload({key: payload[key] for key in _EVENT_PAYLOAD_FIELDS}, state)
    else:
        raise ValueError(_ERROR)
    if type(payload["version"]) is not int or payload["version"] != 1:
        raise ValueError(_ERROR)
    opaque(payload["actor_id"])
    _digest(payload["idempotency_sha256"])
    return dict(payload)


def validate_application_record(application: Mapping[str, Any], application_id: str) -> None:
    """Validate one origin row; its first-event bindings belong to the caller."""
    try:
        record = _closed(application, _APPLICATION_FIELDS)
        if opaque(record["id"]) != opaque(application_id):
            raise ValueError(_ERROR)
        opaque(record["job_id"])
        opaque(record["workflow_run_id"])
        _time(record["created_at"])
    except (RepositoryError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        raise ValueError(_ERROR) from None


def parse_application_event(record: Mapping[str, Any]) -> dict[str, Any]:
    """Decode a closed event row; do not change timestamps or chain identities."""
    try:
        row = _closed(record, _EVENT_FIELDS)
        for key in ("id", "application_id", "actor_id", "workflow_run_id"):
            opaque(row[key])
        if type(row["position"]) is not int or row["position"] < 0:
            raise ValueError(_ERROR)
        state = _state(row["state"])
        _time(row["at"])
        _digest(row["event_sha256"])
        if row["previous_sha256"] is not None:
            _digest(row["previous_sha256"])
        payload = _event_payload(_json(row["payload_json"]), state)
        return {**{key: value for key, value in row.items() if key != "payload_json"}, "payload": dict(payload)}
    except (RepositoryError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        raise ValueError(_ERROR) from None


def validate_application_workflow(
    workflow: Mapping[str, Any] | None, kind: str, payload: Mapping[str, Any], *,
    application_id: str, event_id: str, event_at: str,
) -> None:
    """Bind a completed application workflow to its expected event values."""
    try:
        row = _closed(workflow, _WORKFLOW_FIELDS)
        opaque(application_id)
        opaque(event_id)
        opaque(row["id"])
        event_time = _time(event_at)
        if type(kind) is not str:
            raise ValueError(_ERROR)
        expected = _workflow_payload(payload, kind, application_id)
        actual = _workflow_payload(_json(row["input_json"]), kind, application_id)
        if canonical(actual) != canonical(expected):
            raise ValueError(_ERROR)
        _digest(row["idempotency_key"])
        _digest(row["input_hash_sha256"])
        validate_workflow(dict(row), kind, expected)
        if (_json(row["completed_steps_json"]) != ["validate", "persist"]
                or _json(row["generated_artifacts_json"]) != [application_id, event_id]
                or _json(row["outstanding_need_info_json"]) != []
                or _json(row["retry_policy_json"]) != {}
                or any(row[key] is not None for key in
                       ("model_name", "prompt_version", "failure_code", "failure_reason"))
                or any(row[key] != event_at for key in ("created_at", "started_at", "finished_at"))
                or _time(row["updated_at"]) < event_time):
            raise ValueError(_ERROR)
    except (RepositoryError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        raise ValueError(_ERROR) from None


def validate_submission_record(
    snapshot: Mapping[str, Any], expected: Mapping[str, Any], *,
    application_id: str, event_id: str, material_id: str, submitted_at: str,
) -> None:
    """Require exact typed snapshot values from already validated source records."""
    try:
        row = _closed(snapshot, _SUBMISSION_FIELDS)
        value = _closed(expected, _SUBMISSION_VALUE_FIELDS)
        for key, identifier in (("application_id", application_id), ("event_id", event_id), ("material_id", material_id)):
            if opaque(row[key]) != opaque(identifier):
                raise ValueError(_ERROR)
        _time(submitted_at)
        _time(row["submitted_at"])
        if (row["submitted_at"] != submitted_at or value["recorded_at"] != submitted_at
                or opaque(value["material_id"]) != material_id
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or value["human_confirmed_submission"] is not True or value["external_action_taken"] is not False
                or value["material_bytes_retained_in"] != "immutable_material_version"):
            raise ValueError(_ERROR)
        _digest(value["bundle_sha256"])
        _digest(value["pdf_sha256"])
        # Compare decoded values, preserving harmless JSON formatting changes
        # while distinguishing booleans, integers and floats at every depth.
        actual = _json(row["snapshot_json"])
        expected_value = dict(value)
        if (canonical(actual) != canonical(expected_value)
                or _digest(row["snapshot_sha256"]) != digest(expected_value)):
            raise ValueError(_ERROR)
    except (RepositoryError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        raise ValueError(_ERROR) from None
