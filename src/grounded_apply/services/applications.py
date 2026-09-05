"""Append-only manual application history and immutable submission evidence."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from grounded_apply.domain import to_jsonable
from grounded_apply.domain.application_states import ApplicationState, require_transition
from grounded_apply.repositories import RepositoryError, SQLiteRepository
from grounded_apply.services.jobs import JobService
from grounded_apply.services.materials import MaterialBlocked, MaterialService, MaterialValidationError
from grounded_apply.services.workflow import (
    digest, existing_workflow, finish_workflow, opaque, request_input, start_workflow,
    timestamp, validate_workflow,
)


def _event_hash(record: dict[str, Any]) -> str:
    return digest({k: v for k, v in record.items() if k != "event_sha256"})


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
                ids = json.loads(existing["generated_artifacts_json"])
                result = self.get(ids[0])
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
        opaque(application_id)
        application = self._repository.get_application(application_id)
        if application is None:
            raise ValueError("Application does not exist")
        job = JobService(self._repository).get(application["job_id"])
        records = self._repository.list_application_events(application_id)
        events = []
        previous = None
        try:
            if not records:
                raise ValueError
            for index, record in enumerate(records):
                event = {k: v for k, v in record.items() if k != "payload_json"}
                event["payload"] = json.loads(record["payload_json"])
                if (event["application_id"] != application_id or event["position"] != index
                    or event["previous_sha256"] != (None if previous is None else previous["event_sha256"])
                    or event["event_sha256"] != _event_hash(event)):
                    raise ValueError
                workflow = self._repository.get_workflow_run(event["workflow_run_id"])
                if workflow is None:
                    raise ValueError
                if index == 0:
                    payload = {"version": 1, "job_id": job.id, "actor_id": event["actor_id"],
                        "idempotency_sha256": workflow["idempotency_key"]}
                    validate_workflow(workflow, "application_create", payload)
                    if (event["state"] != "discovered" or event["payload"] != {}
                        or event["workflow_run_id"] != application["workflow_run_id"]
                        or event["at"] != application["created_at"]
                        or json.loads(workflow["generated_artifacts_json"]) != [application_id, event["id"]]):
                        raise ValueError
                else:
                    require_transition(ApplicationState(previous["state"]), ApplicationState(event["state"]))
                    if set(event["payload"]) != {"material_id", "bundle_sha256", "human_confirmed_submission"}:
                        raise ValueError
                    if event["state"] in {"ready_for_review", "applied"}:
                        material = self._materials.get(event["payload"]["material_id"], require_current=False)
                        if (material["job_id"] != job.id or material["bundle_sha256"] != event["payload"]["bundle_sha256"]
                            or not self._materials.is_approved(material["id"], require_current=False)
                            or event["payload"]["human_confirmed_submission"] is not (event["state"] == "applied")):
                            raise ValueError
                        if event["state"] == "applied" and previous["payload"]["material_id"] != material["id"]:
                            raise ValueError
                    elif event["payload"] != {"material_id": None, "bundle_sha256": None, "human_confirmed_submission": False}:
                        raise ValueError
                    payload = {"version": 1, "application_id": application_id, "previous_sha256": previous["event_sha256"],
                        "target_state": event["state"], "actor_id": event["actor_id"], **event["payload"],
                        "idempotency_sha256": workflow["idempotency_key"]}
                    validate_workflow(workflow, "application_transition", payload)
                    if json.loads(workflow["generated_artifacts_json"]) != [application_id, event["id"]] or event["at"] < previous["at"]:
                        raise ValueError
                if workflow["created_at"] != event["at"]:
                    raise ValueError
                events.append(event)
                previous = event
            snapshot = self._repository.get_submission_snapshot(application_id)
            applied = [e for e in events if e["state"] == "applied"]
            if bool(snapshot) != bool(applied) or len(applied) > 1:
                raise ValueError
            if snapshot is not None:
                expected = self._submission(job.id, applied[0], require_current=False)
                if (snapshot["event_id"] != applied[0]["id"] or snapshot["material_id"] != applied[0]["payload"]["material_id"]
                    or snapshot["submitted_at"] != applied[0]["at"]
                    or json.loads(snapshot["snapshot_json"]) != expected or snapshot["snapshot_sha256"] != digest(expected)):
                    raise ValueError
        except (KeyError, TypeError, ValueError):
            raise RepositoryError("Application history failed integrity checks") from None
        ready = False
        if events[-1]["state"] == "ready_for_review":
            try:
                ready = self._materials.is_approved(events[-1]["payload"]["material_id"])
            except MaterialBlocked:
                pass
        return {"application_id": application_id, "job_id": job.id, "source_url": job.source_url,
            "state": events[-1]["state"], "events": events, "state_token": events[-1]["event_sha256"],
            "currently_ready": ready,
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
                payload = json.loads(existing["input_json"])
                if (confirm and preview_token == digest(payload) and payload["application_id"] == application_id
                    and payload["actor_id"] == actor_id and payload["target_state"] == target.value
                    and payload["material_id"] == material_id and payload["human_confirmed_submission"] == confirm_submitted):
                    validate_workflow(existing, "application_transition", payload)
                    ids = json.loads(existing["generated_artifacts_json"])
                    if len(ids) != 2 or ids[0] != application_id or not any(e["id"] == ids[1] for e in application["events"]):
                        raise RepositoryError("Transition replay result is invalid")
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
            or approval["approved_at"] > event["at"] or not self._materials.is_approved(material_id, require_current=require_current)
            or event["payload"]["bundle_sha256"] != material["bundle_sha256"] or event["payload"]["human_confirmed_submission"] is not True):
            raise MaterialValidationError("Submission requires the exact approved material")
        return {"schema_version": 1, "job": to_jsonable(JobService(self._repository).get(job_id)),
            "material_id": material_id, "bundle_sha256": material["bundle_sha256"],
            "pdf_sha256": material["manifest"]["pdf_sha256"], "structure": to_jsonable(material["structure"]),
            "answers": material["manifest"]["answers"], "approval": approval,
            "recorded_at": event["at"], "human_confirmed_submission": True,
            "external_action_taken": False, "material_bytes_retained_in": "immutable_material_version"}
