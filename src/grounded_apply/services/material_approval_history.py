"""Strict bindings for a saved approval record, without granting use authority.

The caller supplies one snapshot and an already validated historical bundle.
Packet eligibility at approval time and present-day readiness remain separate.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from grounded_apply.services.workflow import opaque, validate_workflow

_APPROVAL_FIELDS = {"material_id", "bundle_sha256", "actor_id", "approved_at", "workflow_run_id"}
_PAYLOAD_FIELDS = {"version", "material_id", "bundle_sha256", "actor_id", "idempotency_sha256"}
_WORKFLOW_FIELDS = {
    "id", "workflow_type", "status", "idempotency_key", "input_hash_sha256", "input_json",
    "current_step", "completed_steps_json", "generated_artifacts_json", "outstanding_need_info_json",
    "model_name", "prompt_version", "retry_policy_json", "failure_code", "failure_reason",
    "started_at", "finished_at", "created_at", "updated_at",
}
_ERROR = "Historical material approval record is invalid"


def _time(value: object) -> datetime:
    if type(value) is not str:
        raise ValueError(_ERROR)
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(_ERROR)
    return result


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


def _json(value: object) -> Any:
    if type(value) is not str:
        raise ValueError(_ERROR)
    return json.loads(value, object_pairs_hook=_object)


def validate_approval_record(
    material: Mapping[str, Any], approval: Mapping[str, Any], workflow: Mapping[str, Any] | None,
) -> None:
    """Check the existing row and completed workflow; do not infer approval.

    A matching record alone does not establish that its actor had authority or
    that facts/questions were eligible at its timestamp. Even a partial or
    retired material may retain this record without becoming ready for use.
    """
    if set(approval) != _APPROVAL_FIELDS or workflow is None or set(workflow) != _WORKFLOW_FIELDS:
        raise ValueError(_ERROR)
    material_id = opaque(material["id"])
    actor_id = opaque(approval["actor_id"])
    workflow_id = opaque(approval["workflow_run_id"])
    if (opaque(approval["material_id"]) != material_id or opaque(workflow["id"]) != workflow_id
            or _digest(approval["bundle_sha256"]) != _digest(material["bundle_sha256"])):
        raise ValueError(_ERROR)
    expected = {"version": 1, "material_id": material_id, "bundle_sha256": material["bundle_sha256"],
                "actor_id": actor_id, "idempotency_sha256": _digest(workflow["idempotency_key"])}
    payload = _json(workflow["input_json"])
    if (type(payload) is not dict or set(payload) != _PAYLOAD_FIELDS
            or type(payload["version"]) is not int or payload != expected):
        raise ValueError(_ERROR)
    _digest(workflow["input_hash_sha256"])
    # Keep the original workflow identity contract after closing the JSON/type
    # aliases that ordinary structural equality would otherwise accept.
    validate_workflow(workflow, "material_approval", expected)
    if (_json(workflow["completed_steps_json"]) != ["validate", "persist"]
            or _json(workflow["generated_artifacts_json"]) != [material_id]
            or _json(workflow["outstanding_need_info_json"]) != []
            or _json(workflow["retry_policy_json"]) != {}
            or any(workflow[key] is not None for key in
                   ("model_name", "prompt_version", "failure_code", "failure_reason"))):
        raise ValueError(_ERROR)
    approved_at = _time(approval["approved_at"])
    if (approved_at < _time(material["created_at"])
            or any(workflow[key] != approval["approved_at"] for key in
                   ("created_at", "started_at", "finished_at"))
            or _time(workflow["updated_at"]) < approved_at):
        raise ValueError(_ERROR)
