"""Strict linked material/build records for historical factual audits.

The caller supplies a bundle already checked by its PDF renderer and owns the
read snapshot. This helper preserves stored encodings and identities; factual
authority and questionnaire policy remain with the historical fact validator.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from grounded_apply.repositories import RepositoryError
from grounded_apply.services.material_models import PRESENTATION_TRANSFORMATIONS, TRANSFORMATIONS, validate_layout
from grounded_apply.services.workflow import canonical, digest, hash_bytes, opaque, validate_workflow


_ERROR = "Material build record failed integrity checks"
_INVALID = (RepositoryError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError)
_MATERIAL_FIELDS = frozenset({"id", "job_id", "structure_json", "manifest_json", "validation_json",
    "pdf_bytes", "latex_text", "extracted_text", "bundle_sha256", "created_at", "workflow_run_id",
    "structure", "manifest", "validation"})
_STRUCTURE_FIELDS = frozenset({"job_id", "units", "schema_version", "transformation"})
_MANIFEST_FIELDS = frozenset({"schema_version", "renderer", "transformation", "job_id", "job_source_sha256",
    "created_at", "pdf_sha256", "latex_sha256", "text_sha256", "structure_sha256", "model", "prompt_version",
    "question_specs", "answers", "requirement_links"})
_VALIDATION_FIELDS = frozenset({"schema_version", "valid", "factual_units", "page_count",
    "critical_fields_present", "unsupported_factual_units", "human_approval_required"})
_WORKFLOW_FIELDS = frozenset({"id", "workflow_type", "status", "idempotency_key", "input_hash_sha256",
    "input_json", "current_step", "completed_steps_json", "generated_artifacts_json",
    "outstanding_need_info_json", "model_name", "prompt_version", "retry_policy_json",
    "failure_code", "failure_reason", "started_at", "finished_at", "created_at", "updated_at"})
_PAYLOAD_FIELDS = frozenset({"version", "job_id", "selected_claim_ids", "structure_sha256", "transformation",
    "question_specs_sha256", "answers_sha256", "idempotency_sha256"})


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


def _material_values(material: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    values = []
    for field, fields in (("structure", _STRUCTURE_FIELDS), ("manifest", _MANIFEST_FIELDS),
                          ("validation", _VALIDATION_FIELDS)):
        decoded = _json(material[field + "_json"])
        _closed(decoded, fields)
        # asdict retains tuple collections; JSON represents both as arrays.
        # Canonical values still distinguish booleans, integers and floats.
        if canonical(decoded) != canonical(material[field]):
            raise ValueError(_ERROR)
        if type(decoded["schema_version"]) is not int or decoded["schema_version"] != 1:
            raise ValueError(_ERROR)
        values.append(decoded)
    structure, manifest, validation = values
    if (type(structure["transformation"]) is not str or structure["transformation"] not in TRANSFORMATIONS
            or type(structure["units"]) is not list
            or opaque(structure["job_id"]) != material["job_id"]
            or opaque(manifest["job_id"]) != material["job_id"]
            or manifest["transformation"] != structure["transformation"]
            or type(manifest["renderer"]) is not str or not manifest["renderer"]
            or manifest["created_at"] != material["created_at"]
            or manifest["model"] is not None or manifest["prompt_version"] is not None
            or manifest["requirement_links"] != "inferred_shared_terms_for_review"
            or type(manifest["question_specs"]) is not list or type(manifest["answers"]) is not list
            or type(validation["factual_units"]) is not int or validation["factual_units"] != len(structure["units"])
            or type(validation["page_count"]) is not int or validation["page_count"] not in {1, 2}
            or type(validation["unsupported_factual_units"]) is not int or validation["unsupported_factual_units"] != 0
            or any(validation[key] is not True for key in ("valid", "critical_fields_present", "human_approval_required"))):
        raise ValueError(_ERROR)
    _digest(manifest["job_source_sha256"])
    if (_digest(manifest["structure_sha256"]) != digest(structure)
            or _digest(manifest["pdf_sha256"]) != hash_bytes(material["pdf_bytes"])
            or _digest(manifest["latex_sha256"]) != hash_bytes(material["latex_text"].encode())
            or _digest(manifest["text_sha256"]) != hash_bytes(material["extracted_text"].encode())
            or _digest(material["bundle_sha256"]) != digest({"structure": structure, "manifest": manifest, "validation": validation})):
        raise ValueError(_ERROR)
    return structure, manifest, validation


def _payload(value: object, transformation: str) -> dict[str, Any]:
    fields = _PAYLOAD_FIELDS | {"presentations"} if transformation in PRESENTATION_TRANSFORMATIONS else _PAYLOAD_FIELDS
    payload = _closed(value, fields)
    if (type(payload["version"]) is not int or payload["version"] != 1
            or payload["transformation"] != transformation
            or type(payload["selected_claim_ids"]) is not list
            or not 1 <= len(payload["selected_claim_ids"]) <= 80):
        raise ValueError(_ERROR)
    opaque(payload["job_id"])
    selected = tuple(opaque(identifier) for identifier in payload["selected_claim_ids"])
    if len(set(selected)) != len(selected):
        raise ValueError(_ERROR)
    for key in ("structure_sha256", "question_specs_sha256", "answers_sha256", "idempotency_sha256"):
        _digest(payload[key])
    if transformation in PRESENTATION_TRANSFORMATIONS:
        validate_layout({"schema_version": 1, "presentations": payload["presentations"]}, selected)
    return dict(payload)


def validate_material_build_record(
    material: Mapping[str, Any], workflow: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Validate linked records and return an independently decoded build input.

    The loaded bundle may contain tuple collections from asdict. Its original
    JSON remains bound without normalization of bytes or clocks. This checks no
    present-day readiness and performs no repository, renderer or profile call.
    """
    try:
        row = _closed(material, _MATERIAL_FIELDS)
        build = _closed(workflow, _WORKFLOW_FIELDS)
        material_id = opaque(row["id"])
        opaque(row["job_id"])
        if opaque(row["workflow_run_id"]) != opaque(build["id"]):
            raise ValueError(_ERROR)
        if (type(row["pdf_bytes"]) is not bytes or type(row["latex_text"]) is not str
                or type(row["extracted_text"]) is not str):
            raise ValueError(_ERROR)
        created = _time(row["created_at"])
        structure, manifest, _ = _material_values(row)
        payload = _payload(_json(build["input_json"]), structure["transformation"])
        expected = {"version": 1, "idempotency_sha256": _digest(build["idempotency_key"]),
            "job_id": row["job_id"], "selected_claim_ids": payload["selected_claim_ids"],
            "structure_sha256": digest(structure), "transformation": structure["transformation"],
            "question_specs_sha256": digest(manifest["question_specs"]), "answers_sha256": digest(manifest["answers"])}
        if structure["transformation"] in PRESENTATION_TRANSFORMATIONS:
            expected["presentations"] = validate_layout(
                {"schema_version": 1, "presentations": payload["presentations"]}, tuple(payload["selected_claim_ids"]))
        if canonical(payload) != canonical(expected):
            raise ValueError(_ERROR)
        _digest(build["input_hash_sha256"])
        validate_workflow(dict(build), "material_build", expected)
        if (_json(build["completed_steps_json"]) != ["validate", "persist"]
                or _json(build["generated_artifacts_json"]) != [material_id]
                or _json(build["outstanding_need_info_json"]) != []
                or _json(build["retry_policy_json"]) != {}
                or any(build[key] is not None for key in ("model_name", "prompt_version", "failure_code", "failure_reason"))
                or any(build[key] != row["created_at"] for key in ("created_at", "started_at", "finished_at"))
                or _time(build["updated_at"]) < created):
            raise ValueError(_ERROR)
        return payload
    except _INVALID:
        raise ValueError(_ERROR) from None
