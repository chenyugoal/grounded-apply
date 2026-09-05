"""Small shared workflow identity checks for the local application pilot."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from grounded_apply.repositories import Record, RepositoryError, SQLiteRepository


def opaque(value: str) -> str:
    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", value):
        raise ValueError("An opaque identifier is required")
    return value


def hash_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def digest(value: object) -> str:
    return hash_bytes(canonical(value).encode())


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


def request_input(key: str, fields: dict[str, Any]) -> dict[str, Any]:
    return {"version": 1, **fields, "idempotency_sha256": hash_bytes(opaque(key).encode())}


def existing_workflow(repository: SQLiteRepository, kind: str, payload: dict[str, Any]) -> Record | None:
    found = repository.get_workflow_run_by_idempotency_key(kind, payload["idempotency_sha256"])
    if found is not None:
        validate_workflow(found, kind, payload)
    return found


def validate_workflow(record: Record | None, kind: str, payload: dict[str, Any]) -> None:
    if record is None:
        raise RepositoryError("Workflow audit is missing")
    try:
        valid = (record["workflow_type"] == kind and record["status"] == "succeeded"
            and record["input_hash_sha256"] == digest(payload)
            and json.loads(record["input_json"]) == payload
            and record["idempotency_key"] == payload["idempotency_sha256"]
            and record["current_step"] == "complete"
            and json.loads(record["completed_steps_json"]) == ["validate", "persist"]
            and record["finished_at"] == record["created_at"])
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise RepositoryError("Workflow audit failed integrity checks")


def start_workflow(repository: SQLiteRepository, kind: str, payload: dict[str, Any], at: str) -> Record:
    return repository.add_workflow_run(workflow_type=kind, status="running",
        idempotency_key=payload["idempotency_sha256"], input_hash_sha256=digest(payload),
        input_data=payload, current_step="validate", created_at=at)


def finish_workflow(repository: SQLiteRepository, workflow_id: str, result_ids: list[str], at: str) -> None:
    repository.update_workflow_run(workflow_id, status="succeeded", current_step="complete",
        completed_steps=("validate", "persist"), generated_artifacts=result_ids, finished_at=at)
