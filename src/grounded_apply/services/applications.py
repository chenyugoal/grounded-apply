"""Append-only manual application history and immutable submission evidence."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any
from uuid import uuid4

from grounded_apply.domain import to_jsonable
from grounded_apply.domain.application_states import ApplicationState, require_transition
from grounded_apply.repositories import RepositoryError, SQLiteRepository
from grounded_apply.services.application_history import (
    parse_application_event, validate_application_record, validate_application_workflow,
    validate_submission_record,
)
from grounded_apply.services.jobs import JobService, JobSnapshot
from grounded_apply.services.materials import MaterialBlocked, MaterialService, MaterialValidationError
from grounded_apply.services.workflow import (
    digest, existing_workflow, finish_workflow, opaque, request_input, start_workflow,
    timestamp,
)


def _event_hash(record: dict[str, Any]) -> str:
    return digest({k: v for k, v in record.items() if k != "event_sha256"})


def _aware_time(value: object) -> datetime:
    """Compare instants without rewriting the timestamp used by record hashes."""
    try:
        if type(value) is not str:
            raise ValueError
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed
    except ValueError:
        raise ValueError("Application timestamp is invalid") from None


def _submission_value(job: JobSnapshot, event: dict[str, Any], material: dict[str, Any],
                      approval: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct the immutable snapshot from already checked records."""
    return {"schema_version": 1, "job": to_jsonable(job),
        "material_id": material["id"], "bundle_sha256": material["bundle_sha256"],
        "pdf_sha256": material["manifest"]["pdf_sha256"], "structure": to_jsonable(material["structure"]),
        "answers": material["manifest"]["answers"], "approval": approval,
        "recorded_at": event["at"], "human_confirmed_submission": True,
        "external_action_taken": False, "material_bytes_retained_in": "immutable_material_version"}


class ApplicationService:
    def __init__(self, repository: SQLiteRepository, materials: MaterialService) -> None:
        self._repository = repository
        self._materials = materials

    def add(self, job_id: str, *, actor_id: str, idempotency_key: str, dry_run: bool = False) -> dict[str, Any]:
        opaque(actor_id)
        if type(dry_run) is not bool:
            raise ValueError("Dry run must be boolean")
        with (self._repository.read_transaction() if dry_run else self._repository.transaction()):
            JobService(self._repository).get(job_id)
            payload = request_input(idempotency_key, {"job_id": job_id, "actor_id": actor_id})
            if dry_run:
                return {"dry_run": True, "state": "discovered", "external_action_taken": False}
            existing = existing_workflow(self._repository, "application_create", payload)
            if existing is not None:
                try:
                    ids = json.loads(existing["generated_artifacts_json"])
                    if type(ids) is not list or len(ids) != 2:
                        raise ValueError
                    for identifier in ids:
                        opaque(identifier)
                except (KeyError, TypeError, ValueError, RecursionError):
                    raise RepositoryError("Application replay failed integrity checks") from None
                result = self.get(ids[0])
                first = result["events"][0]
                try:
                    validate_application_workflow(existing, "application_create", payload,
                        application_id=result["application_id"], event_id=first["id"], event_at=first["at"])
                    if existing["id"] != first["workflow_run_id"]:
                        raise ValueError
                except ValueError:
                    raise RepositoryError("Application replay failed integrity checks") from None
                return {"application_id": result["application_id"], "state": result["state"], "replayed": True}
            application_id, at = str(uuid4()), timestamp()
            workflow = start_workflow(self._repository, "application_create", payload, at)
            self._repository.insert_application(application_id=application_id, job_id=job_id, created_at=at, workflow_run_id=workflow["id"])
            event = self._append(application_id, 0, "discovered", actor_id, at, None, {}, workflow["id"])
            finish_workflow(self._repository, workflow["id"], [application_id, event["id"]], at)
            self.get(application_id)
            return {"application_id": application_id, "state": "discovered", "replayed": False, "external_action_taken": False}

    def _append(self, application_id: str, position: int, state: str, actor: str, at: str,
                previous: str | None, payload: dict[str, Any], workflow: str) -> dict[str, Any]:
        event = {"id": str(uuid4()), "application_id": application_id, "position": position,
            "state": state, "actor_id": actor, "at": at, "previous_sha256": previous,
            "payload": payload, "workflow_run_id": workflow}
        event["event_sha256"] = _event_hash(event)
        self._repository.insert_application_event(event_id=event["id"], application_id=application_id,
            position=position, state=state, actor_id=actor, at=at, previous_sha256=previous,
            event_sha256=event["event_sha256"], payload=payload, workflow_run_id=workflow)
        return event

    def get(self, application_id: str) -> dict[str, Any]:
        history = self._validated_history(application_id)
        if history["state"] == "ready_for_review":
            try:
                history["currently_ready"] = self._materials.is_approved(history["events"][-1]["payload"]["material_id"])
            except MaterialBlocked:
                pass
        return history

    def validate_historical_material_use(self, application_id: str) -> None:
        """Audit facts at each saved material-use event without current readiness.

        This checks reconstructible facts and recorded required answers in one
        snapshot; it does not authenticate actors or admit profile conversion.
        """
        try:
            with self._repository.read_transaction():
                self._validated_history(application_id, historical_use=True)
        except (RepositoryError, sqlite3.Error, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
            raise RepositoryError("Application historical material use failed integrity checks") from None

    def validate_historical_inventory(self) -> None:
        """Audit every application and account for its complete component inventory.

        Unvisited application workflows, events and submissions fail closed.
        This grants neither current readiness nor whole-profile conversion
        admission; other workflow kinds require their own custody checks.
        """
        columns = {
            "applications": ("id",),
            "events": ("id", "application_id", "workflow_run_id"),
            "submissions": ("application_id", "event_id", "material_id"),
            "workflows": ("id", "workflow_type"),
        }
        try:
            with self._repository.read_transaction():
                inventory = self._repository.application_history_inventory()
                if type(inventory) is not dict or set(inventory) != set(columns):
                    raise ValueError
                actual: dict[str, tuple[tuple[str, ...], ...]] = {}
                for kind, fields in columns.items():
                    rows = inventory[kind]
                    if type(rows) is not tuple:
                        raise ValueError
                    values = []
                    for row in rows:
                        if type(row) is not dict or set(row) != set(fields):
                            raise ValueError
                        values.append(tuple(opaque(row[field]) for field in fields))
                    if len({row[0] for row in values}) != len(values):
                        raise ValueError
                    actual[kind] = tuple(sorted(values))

                expected: dict[str, list[tuple[str, ...]]] = {kind: [] for kind in columns}
                for (application_id,) in actual["applications"]:
                    history = self._validated_history(application_id, historical_use=True)
                    expected["applications"].append((history["application_id"],))
                    for event in history["events"]:
                        expected["events"].append((event["id"], application_id, event["workflow_run_id"]))
                        workflow_kind = "application_create" if event["position"] == 0 else "application_transition"
                        expected["workflows"].append((event["workflow_run_id"], workflow_kind))
                        if event["state"] == "applied":
                            expected["submissions"].append((application_id, event["id"], event["payload"]["material_id"]))
                if any(actual[kind] != tuple(sorted(expected[kind])) for kind in columns):
                    raise ValueError
        except (RepositoryError, sqlite3.Error, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
            raise RepositoryError("Application historical inventory failed integrity checks") from None

    def _validated_history(self, application_id: str, *, historical_use: bool = False) -> dict[str, Any]:
        """Validate record bindings; the explicit use audit owns its snapshot."""
        opaque(application_id)
        try:
            application = self._repository.get_application(application_id)
        except sqlite3.Error:
            raise RepositoryError("Application history failed integrity checks") from None
        if application is None:
            raise ValueError("Application does not exist")
        events = []
        previous = None
        previous_at = None
        applied_material = None
        try:
            validate_application_record(application, application_id)
            job = JobService(self._repository).get(application["job_id"])
            records = self._repository.list_application_events(application_id)
            if not records:
                raise ValueError
            for index, record in enumerate(records):
                event = parse_application_event(record)
                if (event["application_id"] != application_id or event["position"] != index
                    or event["previous_sha256"] != (None if previous is None else previous["event_sha256"])
                    or event["event_sha256"] != _event_hash(event)):
                    raise ValueError
                event_at = _aware_time(event["at"])
                workflow = self._repository.get_workflow_run(event["workflow_run_id"])
                if workflow is None or workflow["id"] != event["workflow_run_id"]:
                    raise ValueError
                if index == 0:
                    payload = {"version": 1, "job_id": job.id, "actor_id": event["actor_id"],
                        "idempotency_sha256": workflow["idempotency_key"]}
                    validate_application_workflow(workflow, "application_create", payload,
                        application_id=application_id, event_id=event["id"], event_at=event["at"])
                    if (event["state"] != "discovered" or event["payload"] != {}
                        or event["workflow_run_id"] != application["workflow_run_id"]
                        or event["at"] != application["created_at"]):
                        raise ValueError
                else:
                    require_transition(ApplicationState(previous["state"]), ApplicationState(event["state"]))
                    if set(event["payload"]) != {"material_id", "bundle_sha256", "human_confirmed_submission"}:
                        raise ValueError
                    if event["state"] in {"ready_for_review", "applied"}:
                        material = self._materials.get(event["payload"]["material_id"], require_current=False)
                        if (material["job_id"] != job.id or material["bundle_sha256"] != event["payload"]["bundle_sha256"]
                            or (not historical_use and not self._materials.is_approved(material["id"], require_current=False))
                            or event["payload"]["human_confirmed_submission"] is not (event["state"] == "applied")):
                            raise ValueError
                        approval = self._repository.get_material_approval(material["id"])
                        if approval is None or _aware_time(approval["approved_at"]) > event_at:
                            raise ValueError
                        if historical_use:
                            if material["id"] != event["payload"]["material_id"]:
                                raise ValueError
                            self._materials._validate_historical_approved_snapshot(
                                material, approval, require_complete=True, used_at=event["at"])
                        if event["state"] == "applied" and previous["payload"]["material_id"] != material["id"]:
                            raise ValueError
                        if historical_use and event["state"] == "applied":
                            applied_material = (material, approval)
                    elif event["payload"] != {"material_id": None, "bundle_sha256": None, "human_confirmed_submission": False}:
                        raise ValueError
                    payload = {"version": 1, "application_id": application_id, "previous_sha256": previous["event_sha256"],
                        "target_state": event["state"], "actor_id": event["actor_id"], **event["payload"],
                        "idempotency_sha256": workflow["idempotency_key"]}
                    validate_application_workflow(workflow, "application_transition", payload,
                        application_id=application_id, event_id=event["id"], event_at=event["at"])
                    if previous_at is None or event_at < previous_at:
                        raise ValueError
                events.append(event)
                previous = event
                previous_at = event_at
            snapshot = self._repository.get_submission_snapshot(application_id)
            applied = [e for e in events if e["state"] == "applied"]
            if bool(snapshot) != bool(applied) or len(applied) > 1:
                raise ValueError
            if snapshot is not None:
                if historical_use:
                    if applied_material is None:
                        raise ValueError
                    expected = _submission_value(job, applied[0], *applied_material)
                else:
                    expected = self._submission(job.id, applied[0], require_current=False)
                validate_submission_record(snapshot, expected, application_id=application_id,
                    event_id=applied[0]["id"], material_id=applied[0]["payload"]["material_id"],
                    submitted_at=applied[0]["at"])
        except (sqlite3.Error, KeyError, TypeError, ValueError, AttributeError, OverflowError, RecursionError):
            raise RepositoryError("Application history failed integrity checks") from None
        return {"application_id": application_id, "job_id": job.id, "source_url": job.source_url,
            "state": events[-1]["state"], "events": events, "state_token": events[-1]["event_sha256"],
            "currently_ready": False,
            "submission_sha256": None if snapshot is None else snapshot["snapshot_sha256"],
            "external_action_taken": False}

    def list(self) -> tuple[dict[str, Any], ...]:
        return tuple(self.get(r["id"]) for r in self._repository.list_applications())

    def transition(self, application_id: str, target_state: str, *, actor_id: str, idempotency_key: str,
                   material_id: str | None = None, confirm_submitted: bool = False,
                   confirm: bool = False, preview_token: str | None = None) -> dict[str, Any]:
        opaque(actor_id)
        target = ApplicationState(target_state)
        if type(confirm) is not bool or type(confirm_submitted) is not bool:
            raise ValueError("Confirmation must be boolean")
        if confirm and (type(preview_token) is not str or len(preview_token) != 64):
            raise ValueError("Confirmed transition requires a preview token")
        if target != ApplicationState.APPLIED and confirm_submitted:
            raise ValueError("Submission confirmation applies only when recording applied")
        with (self._repository.transaction() if confirm else self._repository.read_transaction()):
            application = self.get(application_id)
            key = request_input(idempotency_key, {})["idempotency_sha256"]
            existing = self._repository.get_workflow_run_by_idempotency_key("application_transition", key)
            if existing is not None:
                try:
                    ids = json.loads(existing["generated_artifacts_json"])
                    if type(ids) is not list or len(ids) != 2:
                        raise ValueError
                    for identifier in ids:
                        opaque(identifier)
                    recorded = application if ids[0] == application_id else self._validated_history(ids[0])
                    event = next((e for e in recorded["events"] if e["id"] == ids[1]), None)
                    if event is None or event["workflow_run_id"] != existing["id"]:
                        raise ValueError
                    payload = {"version": 1, "application_id": recorded["application_id"],
                        "previous_sha256": event["previous_sha256"], "target_state": event["state"],
                        "actor_id": event["actor_id"], **event["payload"], "idempotency_sha256": key}
                    validate_application_workflow(existing, "application_transition", payload,
                        application_id=recorded["application_id"], event_id=event["id"], event_at=event["at"])
                except (KeyError, TypeError, ValueError, AttributeError, OverflowError, RecursionError):
                    raise RepositoryError("Transition replay result is invalid") from None
                if (confirm and preview_token == digest(payload) and payload["application_id"] == application_id
                    and payload["actor_id"] == actor_id and payload["target_state"] == target.value
                    and payload["material_id"] == material_id and payload["human_confirmed_submission"] is confirm_submitted):
                    return {"application_id": application_id, "recorded_state": target.value, "state": application["state"], "replayed": True, "external_action_taken": False}
                raise ValueError("Transition key already used; replay the exact confirmed request")
            require_transition(ApplicationState(application["state"]), target)
            bundle = None
            if target in {ApplicationState.READY_FOR_REVIEW, ApplicationState.APPLIED}:
                if material_id is None:
                    raise MaterialValidationError("This transition requires an approved material version")
                material = self._materials.get(material_id)
                if material["job_id"] != application["job_id"] or not self._materials.is_approved(material_id):
                    raise MaterialValidationError("Material must be approved for this exact job")
                bundle = material["bundle_sha256"]
                if target == ApplicationState.APPLIED:
                    if not confirm_submitted:
                        raise ValueError("Recording applied requires explicit confirmation that you submitted manually")
                    if application["events"][-1]["payload"]["material_id"] != material_id:
                        raise ValueError("Submission must use the reviewed material version")
            elif material_id is not None or confirm_submitted:
                raise ValueError("Material/submission fields are not valid for this transition")
            payload = request_input(idempotency_key, {"application_id": application_id,
                "previous_sha256": application["state_token"], "target_state": target.value, "actor_id": actor_id,
                "material_id": material_id, "bundle_sha256": bundle, "human_confirmed_submission": confirm_submitted})
            token = digest(payload)
            if preview_token is not None and preview_token != token:
                raise ValueError("Application or material changed since preview")
            if not confirm:
                return {"application_id": application_id, "from_state": application["state"], "to_state": target.value,
                        "preview_token": token, "dry_run": True, "requires_confirmation": True, "external_action_taken": False}
            at = timestamp()
            workflow = start_workflow(self._repository, "application_transition", payload, at)
            event = self._append(application_id, len(application["events"]), target.value, actor_id, at, application["state_token"],
                {"material_id": material_id, "bundle_sha256": bundle, "human_confirmed_submission": confirm_submitted}, workflow["id"])
            if target == ApplicationState.APPLIED:
                snapshot = self._submission(application["job_id"], event, require_current=True)
                self._repository.insert_submission_snapshot(application_id=application_id, event_id=event["id"], material_id=material_id,
                    snapshot=snapshot, snapshot_sha256=digest(snapshot), submitted_at=at)
            finish_workflow(self._repository, workflow["id"], [application_id, event["id"]], at)
            self.get(application_id)
            return {"application_id": application_id, "state": target.value, "replayed": False, "external_action_taken": False}

    def _submission(self, job_id: str, event: dict[str, Any], *, require_current: bool) -> dict[str, Any]:
        material_id = event["payload"]["material_id"]
        material = self._materials.get(material_id, require_current=require_current)
        approval = self._repository.get_material_approval(material_id)
        if (approval is None or material["job_id"] != job_id or approval["bundle_sha256"] != material["bundle_sha256"]
            or _aware_time(approval["approved_at"]) > _aware_time(event["at"])
            or not self._materials.is_approved(material_id, require_current=require_current)
            or event["payload"]["bundle_sha256"] != material["bundle_sha256"] or event["payload"]["human_confirmed_submission"] is not True):
            raise MaterialValidationError("Submission requires the exact approved material")
        return _submission_value(JobService(self._repository).get(job_id), event, material, approval)
