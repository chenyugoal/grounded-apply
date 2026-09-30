"""Explicit, durable daily ticks over saved searches; no installed scheduler."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5
from zoneinfo import ZoneInfo

from grounded_apply.repositories import Record, RepositoryError, SQLiteRepository
from grounded_apply.services.backup import MAX_SNAPSHOT_BYTES
from grounded_apply.services.storage_limits import StorageCapacityError
from grounded_apply.services.batches import _at, _valid_at
from grounded_apply.services.discovery import Transport
from grounded_apply.services.material_models import ResumeRenderer
from grounded_apply.services.schedule_notifications import (
    NotificationCapacityError, NotificationUpdate, notification_capacity, notification_delta, notification_failure,
    notification_retry_limit, notification_update, validate_notification_summary,
)
from grounded_apply.services.schedule_policy import MAX_DATE, MIN_DATE, DailyOccurrence, due_instant, latest_due, next_due, validate_schedule_manifest
from grounded_apply.services.searches import RepositoryFactory, SearchLeaseActiveError, SearchService
from grounded_apply.services.workflow import (
    digest, existing_workflow, finish_workflow, hash_bytes, opaque,
    request_input, start_workflow, validate_workflow,
)


SCHEDULE_RESERVE_BYTES = 768 * 1024
_TERMINAL = {"completed", "waiting_for_input"}
_OCCURRENCE_STATES = {"queued", "running", "completed", "waiting_for_input", "budget_exhausted", "failed", "retry_limit"}
_STOPS = {None, "item_budget", "time_budget", "request_budget", "byte_budget", "capacity_reached", "shared_failure", "lease_active", "paused", "retry_limit"}


class ScheduleIntegrityError(RepositoryError):
    """The saved schedule, occurrence or notification audit is invalid."""


class ScheduleLeaseActiveError(ValueError):
    def __init__(self, schedule_id: str) -> None:
        super().__init__("Another invocation owns this schedule; its current occurrence remains durable")
        self.schedule_id = schedule_id


class ScheduleLeaseLostError(RepositoryError):
    """A paused, expired or replaced schedule owner cannot dispatch or commit."""


class ScheduleCapacityError(StorageCapacityError):
    """Schedule storage capacity is exhausted without permission to delete history."""


class ScheduleExecutionError(RepositoryError):
    def __init__(self, schedule_id: str, occurrence_id: str, run_id: str | None) -> None:
        super().__init__("Daily execution stopped; inspect its validated schedule and child checkpoints")
        self.schedule_id, self.occurrence_id, self.run_id = schedule_id, occurrence_id, run_id


def _initial_control() -> dict[str, Any]:
    return {"enabled": True, "current_occurrence_id": None, "last_local_date": None,
        "missed_dates": 0, "paused_dates": 0, "notification_summary": None, "notification_id": None}


def _initial_occurrence() -> dict[str, Any]:
    return {"status": "queued", "attempts": 0, "run_id": None, "stop_reason": None}


def _event(parent_id: str, position: int, at: str, action: str, state: object, previous: str | None,
           workflow_run_id: str | None = None) -> dict[str, Any]:
    return {"parent_id": parent_id, "position": position, "at": at, "action": action, "state": state,
        "previous_sha256": previous, "workflow_run_id": workflow_run_id}


def _due_view(occurrence: DailyOccurrence | None) -> dict[str, str] | None:
    return None if occurrence is None else {"local_date": occurrence.local_date.isoformat(), "due_at": _at(occurrence.due_at)}


class ScheduleService:
    def __init__(self, repository_factory: RepositoryFactory, transport: Transport, renderer: ResumeRenderer, *,
                 clock: Callable[[], datetime] | None = None, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._open, self._transport, self._renderer = repository_factory, transport, renderer
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic = monotonic

    def _search(self, **hooks: Any) -> SearchService:
        return SearchService(self._open, self._transport, self._renderer, clock=self._clock, monotonic=self._monotonic, **hooks)

    def _now(self) -> str:
        return _at(self._clock())

    @staticmethod
    def _capacity(repository: SQLiteRepository, *, reserve: bool = True) -> None:
        if repository.database_size_bytes() > MAX_SNAPSHOT_BYTES - (SCHEDULE_RESERVE_BYTES if reserve else 0):
            raise ScheduleCapacityError("Schedule storage capacity reached; no new work was added")

    @staticmethod
    def _policy(manifest: dict[str, Any]) -> dict[str, Any]:
        return {"start_date": date.fromisoformat(manifest["start_date"]), "zone": manifest["timezone"], "local_time": manifest["local_time"]}

    def configure(self, manifest: object, *, idempotency_key: str, dry_run: bool = False) -> dict[str, Any]:
        spec = validate_schedule_manifest(manifest)
        if type(dry_run) is not bool:
            raise ValueError("Dry run must be boolean")
        opaque(idempotency_key)
        now = self._clock()
        following = next_due(now, **self._policy(spec))
        if dry_run:
            if date.fromisoformat(spec["start_date"]) < now.astimezone(ZoneInfo(spec["timezone"])).date():
                raise ValueError("A new schedule cannot start before its local configuration date")
            return {"schema_version": 1, "schedule_id": None, "manifest": spec, "dry_run": True,
                "executed": False, "notify": False, "next_due": _due_view(following),
                "search_validated": False, "external_action_taken": False, "scheduler_installed": False}
        with self._open(False) as repository, repository.transaction():
            search, _ = self._search()._scope(repository, spec["search_id"])
            payload = request_input(idempotency_key, {"manifest_sha256": digest(spec), "search_manifest_sha256": search["manifest_sha256"]})
            schedule_id = str(uuid5(NAMESPACE_URL, "grounded-apply.schedule@1/" + payload["idempotency_sha256"]))
            existing = existing_workflow(repository, "schedule_configure", payload)
            if existing is None:
                if date.fromisoformat(spec["start_date"]) < now.astimezone(ZoneInfo(spec["timezone"])).date():
                    raise ValueError("A new schedule cannot start before its local configuration date")
                at = _at(now)
                workflow = start_workflow(repository, "schedule_configure", payload, at)
                repository.insert_daily_schedule(schedule_id=schedule_id, search_id=spec["search_id"], manifest=spec,
                    manifest_sha256=digest(spec), search_manifest_sha256=search["manifest_sha256"], created_at=at, workflow_run_id=workflow["id"])
                finish_workflow(repository, workflow["id"], [schedule_id], at)
                self._append_control(repository, schedule_id, spec, "created", _initial_control(), workflow_run_id=workflow["id"], at=at)
                self._capacity(repository)
            self._read(repository, schedule_id)
        result = self.get(schedule_id)
        result["replayed"] = existing is not None
        return result

    def _scope(self, repository: SQLiteRepository, schedule_id: str) -> tuple[Record, dict[str, Any]]:
        opaque(schedule_id)
        record = repository.get_daily_schedule(schedule_id)
        if record is None:
            raise ValueError("Daily schedule does not exist")
        try:
            manifest = json.loads(record["manifest_json"])
            if (validate_schedule_manifest(manifest) != manifest or manifest["search_id"] != record["search_id"]
                or digest(manifest) != record["manifest_sha256"] or not _valid_at(record["created_at"])):
                raise ValueError
            search, _ = self._search()._scope(repository, record["search_id"])
            if search["manifest_sha256"] != record["search_manifest_sha256"]:
                raise ValueError
            workflow = repository.get_workflow_run(record["workflow_run_id"])
            if workflow is None:
                raise ValueError
            payload = {"version": 1, "manifest_sha256": digest(manifest), "search_manifest_sha256": record["search_manifest_sha256"],
                "idempotency_sha256": workflow["idempotency_key"]}
            validate_workflow(workflow, "schedule_configure", payload)
            if (schedule_id != str(uuid5(NAMESPACE_URL, "grounded-apply.schedule@1/" + workflow["idempotency_key"]))
                or workflow["created_at"] != record["created_at"] or json.loads(workflow["generated_artifacts_json"]) != [schedule_id]
                or date.fromisoformat(manifest["start_date"]) < datetime.fromisoformat(record["created_at"]).astimezone(ZoneInfo(manifest["timezone"])).date()):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise ScheduleIntegrityError("Daily schedule failed integrity checks") from None
        return record, manifest

    def _control(self, repository: SQLiteRepository, schedule_id: str, manifest: dict[str, Any]) -> tuple[dict[str, Any], list[Record]]:
        events = repository.list_schedule_events(schedule_id)
        previous, previous_hash, previous_at = None, None, None
        try:
            if not events:
                raise ValueError
            for position, event in enumerate(events):
                state = json.loads(event["state_json"])
                if (type(state) is not dict or set(state) != set(_initial_control()) or type(state["enabled"]) is not bool
                    or any(type(state[key]) is not int or not 0 <= state[key] <= (MAX_DATE - MIN_DATE).days for key in ("missed_dates", "paused_dates"))
                    or event["schedule_id"] != schedule_id or event["position"] != position or not _valid_at(event["at"])
                    or event["id"] != str(uuid5(NAMESPACE_URL, f"{schedule_id}/event/{position}"))
                    or previous_at is not None and event["at"] < previous_at or event["previous_sha256"] != previous_hash
                    or event["event_sha256"] != digest(_event(schedule_id, position, event["at"], event["action"], state, previous_hash, event["workflow_run_id"]))):
                    raise ValueError
                if (state["current_occurrence_id"] is None) != (state["last_local_date"] is None):
                    raise ValueError
                if state["current_occurrence_id"] is not None:
                    opaque(state["current_occurrence_id"])
                    if date.fromisoformat(state["last_local_date"]).isoformat() != state["last_local_date"]:
                        raise ValueError
                if state["notification_summary"] is not None:
                    if (validate_notification_summary(state["notification_summary"]) != state["notification_summary"]
                        or state["notification_summary"]["search_id"] != manifest["search_id"]):
                        raise ValueError
                if state["notification_id"] is not None:
                    opaque(state["notification_id"])
                action = event["action"]
                if previous is None:
                    if action != "created" or state != _initial_control():
                        raise ValueError
                else:
                    allowed = {"enabled_changed": {"enabled"}, "occurrence_selected": {"current_occurrence_id", "last_local_date", "missed_dates", "paused_dates"},
                        "notification_updated": {"notification_summary", "notification_id"}}
                    if action not in allowed or {key for key in state if state[key] != previous[key]} - allowed[action]:
                        raise ValueError
                    if action == "occurrence_selected":
                        occurrence = repository.get_schedule_occurrence(state["current_occurrence_id"])
                        first_date = date.fromisoformat(manifest["start_date"]) if previous["last_local_date"] is None else date.fromisoformat(previous["last_local_date"]) + timedelta(days=1)
                        if (occurrence is None or occurrence["schedule_id"] != schedule_id or occurrence["local_date"] != state["last_local_date"]
                            or occurrence["created_at"] != event["at"]
                            or state["missed_dates"] + state["paused_dates"] - previous["missed_dates"] - previous["paused_dates"] > (date.fromisoformat(state["last_local_date"]) - first_date).days):
                            raise ValueError
                        missed, paused = self._gaps(manifest, previous, events[:position], date.fromisoformat(state["last_local_date"]))
                        if state["missed_dates"] != previous["missed_dates"] + missed or state["paused_dates"] != previous["paused_dates"] + paused:
                            raise ValueError
                        if (state["last_local_date"] is None or previous["last_local_date"] is not None and state["last_local_date"] <= previous["last_local_date"]
                            or state["missed_dates"] < previous["missed_dates"] or state["paused_dates"] < previous["paused_dates"]):
                            raise ValueError
                    if action == "notification_updated":
                        delta = notification_delta(previous["notification_summary"], state["notification_summary"])
                        changed = any(bool(value) for value in delta.values())
                        if changed:
                            notice = repository.get_schedule_notification(state["notification_id"])
                            if (notice is None or notice["schedule_id"] != schedule_id or notice["occurrence_id"] != state["current_occurrence_id"]
                                or json.loads(notice["delta_json"]) != delta or notice["created_at"] != event["at"]
                                or notice["summary_sha256"] != digest(state["notification_summary"])
                                or notice["previous_summary_sha256"] != digest(previous["notification_summary"])):
                                raise ValueError
                            self._notification(notice)
                        elif state["notification_id"] != previous["notification_id"]:
                            raise ValueError
                if action in {"created", "enabled_changed"}:
                    workflow = repository.get_workflow_run(event["workflow_run_id"])
                    if workflow is None:
                        raise ValueError
                    if action == "enabled_changed":
                        validate_workflow(workflow, "schedule_enable", {"version": 1, "schedule_id": schedule_id,
                            "enabled": state["enabled"], "idempotency_sha256": workflow["idempotency_key"]})
                        if workflow["created_at"] != event["at"] or json.loads(workflow["generated_artifacts_json"]) != [schedule_id]:
                            raise ValueError
                elif event["workflow_run_id"] is not None:
                    raise ValueError
                previous, previous_hash, previous_at = state, event["event_sha256"], event["at"]
        except (ValueError, KeyError, TypeError):
            raise ScheduleIntegrityError("Schedule control history failed integrity checks") from None
        assert previous is not None
        return previous, events

    def _append_control(self, repository: SQLiteRepository, schedule_id: str, manifest: dict[str, Any], action: str,
                        state: dict[str, Any], *, workflow_run_id: str | None = None, at: str | None = None) -> None:
        events = repository.list_schedule_events(schedule_id)
        moment, position = self._now() if at is None else at, len(events)
        previous = events[-1]["event_sha256"] if events else None
        repository.insert_schedule_event(event_id=str(uuid5(NAMESPACE_URL, f"{schedule_id}/event/{position}")),
            schedule_id=schedule_id, position=position, at=moment, action=action, state=state, workflow_run_id=workflow_run_id,
            previous_sha256=previous, event_sha256=digest(_event(schedule_id, position, moment, action, state, previous, workflow_run_id)))
        self._control(repository, schedule_id, manifest)
        self._capacity(repository, reserve=False)

    @staticmethod
    def _run_id(search_id: str, occurrence_id: str) -> str:
        key = "schedule-run." + occurrence_id
        payload_key = "search-run." + digest({"search_id": search_id, "key": key})
        return str(uuid5(NAMESPACE_URL, f"grounded-apply.search-run@1/{search_id}/{hash_bytes(payload_key.encode())}"))

    def _occurrence(self, repository: SQLiteRepository, occurrence_id: str, manifest: dict[str, Any]) -> tuple[Record, dict[str, Any]]:
        record = repository.get_schedule_occurrence(occurrence_id)
        try:
            if record is None or record["id"] != str(uuid5(NAMESPACE_URL, f"{record['schedule_id']}/day/{record['local_date']}")):
                raise ValueError
            if (date.fromisoformat(record["local_date"]).isoformat() != record["local_date"]
                or record["local_date"] < manifest["start_date"] or not _valid_at(record["due_at"]) or not _valid_at(record["created_at"])
                or record["due_at"] > record["created_at"]
                or record["record_sha256"] != digest({key: value for key, value in record.items() if key != "record_sha256"})):
                raise ValueError
            events = repository.list_occurrence_events(occurrence_id)
            previous, previous_hash, previous_at = None, None, None
            for position, event in enumerate(events):
                state = json.loads(event["state_json"])
                if (type(state) is not dict or set(state) != set(_initial_occurrence()) or state["status"] not in _OCCURRENCE_STATES
                    or type(state["attempts"]) is not int or not 0 <= state["attempts"] <= manifest["max_attempts"]
                    or state["stop_reason"] not in _STOPS or event["occurrence_id"] != occurrence_id or event["position"] != position
                    or event["id"] != str(uuid5(NAMESPACE_URL, f"{occurrence_id}/event/{position}")) or not _valid_at(event["at"])
                    or previous_at is not None and event["at"] < previous_at or event["previous_sha256"] != previous_hash
                    or event["event_sha256"] != digest(_event(occurrence_id, position, event["at"], event["action"], state, previous_hash))):
                    raise ValueError
                if state["run_id"] is not None and state["run_id"] != self._run_id(manifest["search_id"], occurrence_id):
                    raise ValueError
                if state["status"] in _TERMINAL and state["run_id"] is None:
                    raise ValueError
                action = event["action"]
                if previous is None:
                    if action != "created" or state != _initial_occurrence() or event["at"] != record["created_at"]:
                        raise ValueError
                else:
                    allowed = {"attempted": {"status", "attempts", "stop_reason"}, "child_bound": {"run_id"},
                        "finished": {"status", "stop_reason"}, "child_reconciled": {"status", "stop_reason"}, "lease_deferred": {"status", "attempts", "stop_reason"},
                        "retry_limit": {"status", "stop_reason"}}
                    if action not in allowed or {key for key in state if state[key] != previous[key]} - allowed[action] or previous["status"] in _TERMINAL:
                        raise ValueError
                    if previous["run_id"] is not None and state["run_id"] != previous["run_id"]:
                        raise ValueError
                    if action == "attempted":
                        if state["status"] != "running" or state["attempts"] != previous["attempts"] + 1 or state["stop_reason"] is not None:
                            raise ValueError
                    elif action == "child_bound":
                        if previous["status"] != "running" or previous["run_id"] is not None or state["run_id"] is None:
                            raise ValueError
                    elif action == "finished":
                        if previous["status"] != "running" or state["status"] not in (_TERMINAL | {"failed", "budget_exhausted"}):
                            raise ValueError
                    elif action == "child_reconciled":
                        if state["status"] not in _TERMINAL or state["run_id"] is None:
                            raise ValueError
                    elif action == "lease_deferred":
                        if previous["status"] != "running" or state != {**previous, "status": "queued", "attempts": previous["attempts"] - 1, "stop_reason": "lease_active"}:
                            raise ValueError
                    elif action == "retry_limit" and (previous["attempts"] != manifest["max_attempts"] or state["status"] != "retry_limit" or state["stop_reason"] != "retry_limit"):
                        raise ValueError
                previous, previous_hash, previous_at = state, event["event_sha256"], event["at"]
            if previous is None:
                raise ValueError
            link = repository.get_occurrence_search_link(occurrence_id)
            if (link is None) != (previous["run_id"] is None) or link is not None and link["run_id"] != previous["run_id"]:
                raise ValueError
            if link is not None:
                child = repository.get_search_run(link["run_id"])
                if child is None or child["search_id"] != manifest["search_id"]:
                    raise ValueError
                if previous["status"] in _TERMINAL:
                    _, _, child_state = self._search()._validated(repository, link["run_id"])
                    if child_state["phase"] != "complete" or previous["stop_reason"] != child_state["stop_reason"]:
                        raise ValueError
        except (ValueError, KeyError, TypeError):
            raise ScheduleIntegrityError("Daily occurrence failed integrity checks") from None
        return record, previous

    def _append_occurrence(self, repository: SQLiteRepository, occurrence_id: str, manifest: dict[str, Any], action: str,
                           state: dict[str, Any], *, at: str | None = None) -> None:
        events = repository.list_occurrence_events(occurrence_id)
        moment, position = self._now() if at is None else at, len(events)
        previous = events[-1]["event_sha256"] if events else None
        repository.insert_occurrence_event(event_id=str(uuid5(NAMESPACE_URL, f"{occurrence_id}/event/{position}")),
            occurrence_id=occurrence_id, position=position, at=moment, action=action, state=state,
            previous_sha256=previous, event_sha256=digest(_event(occurrence_id, position, moment, action, state, previous)))
        self._occurrence(repository, occurrence_id, manifest)
        self._capacity(repository, reserve=False)

    def _read(self, repository: SQLiteRepository, schedule_id: str) -> tuple[Record, dict[str, Any], dict[str, Any]]:
        record, manifest = self._scope(repository, schedule_id)
        control, events = self._control(repository, schedule_id, manifest)
        if events[0]["at"] != record["created_at"] or events[0]["workflow_run_id"] != record["workflow_run_id"]:
            raise ScheduleIntegrityError("Schedule creation checkpoint is invalid")
        occurrences = repository.list_schedule_occurrences(schedule_id)
        selected = [json.loads(event["state_json"])["current_occurrence_id"] for event in events if event["action"] == "occurrence_selected"]
        if [item["id"] for item in occurrences] != selected:
            raise ScheduleIntegrityError("Schedule occurrence selection is invalid")
        for item in occurrences:
            self._occurrence(repository, item["id"], manifest)
        expected_notices = []
        previous_notice = None
        for event in events:
            value = json.loads(event["state_json"])
            if value["notification_id"] != previous_notice:
                expected_notices.append(value["notification_id"])
            previous_notice = value["notification_id"]
        if set(expected_notices) != {notice["id"] for notice in repository.list_schedule_notifications(schedule_id)} or len(set(expected_notices)) != len(expected_notices):
            raise ScheduleIntegrityError("Schedule notification history is invalid")
        if occurrences and (control["current_occurrence_id"] != occurrences[-1]["id"] or control["last_local_date"] != occurrences[-1]["local_date"]):
            raise ScheduleIntegrityError("Schedule latest occurrence is invalid")
        return record, manifest, control

    def _lease(self, repository: SQLiteRepository, schedule_id: str) -> Record:
        lease = repository.get_schedule_lease(schedule_id)
        try:
            if (lease is None or lease["schedule_id"] != schedule_id or type(lease["epoch"]) is not int or lease["epoch"] < 0
                or (lease["owner"] is None) != (lease["expires_at"] is None)
                or (lease["owner"] is None) != (lease["occurrence_id"] is None)):
                raise ValueError
            if lease["owner"] is not None:
                opaque(lease["owner"])
                occurrence = repository.get_schedule_occurrence(lease["occurrence_id"])
                if not _valid_at(lease["expires_at"]) or occurrence is None or occurrence["schedule_id"] != schedule_id:
                    raise ValueError
        except (ValueError, TypeError, KeyError):
            raise ScheduleIntegrityError("Schedule lease is invalid") from None
        return lease

    def _assert_lease(self, repository: SQLiteRepository, schedule_id: str, occurrence_id: str, owner: str, epoch: int,
                      *, require_enabled: bool = True) -> None:
        _, _, control = self._read(repository, schedule_id)
        lease = self._lease(repository, schedule_id)
        now = self._now()
        latest = max(repository.list_schedule_events(schedule_id)[-1]["at"], repository.list_occurrence_events(occurrence_id)[-1]["at"])
        if (now < latest or lease["owner"] != owner or lease["epoch"] != epoch or lease["occurrence_id"] != occurrence_id
            or lease["expires_at"] <= now or require_enabled and not control["enabled"]):
            raise ScheduleLeaseLostError("Daily schedule was paused or its lease expired or changed")
        self._capacity(repository)

    @staticmethod
    def _notification(record: Record) -> None:
        payload = {key: value for key, value in record.items() if key not in {"id", "notification_sha256", "delta_json"}}
        payload["delta"] = json.loads(record["delta_json"])
        expected_id = str(uuid5(NAMESPACE_URL, "grounded-apply.notification@1/" + digest(payload)))
        if not _valid_at(record["created_at"]) or record["id"] != expected_id or record["notification_sha256"] != digest(payload):
            raise ScheduleIntegrityError("Schedule notification failed integrity checks")

    def _pending(self, repository: SQLiteRepository, schedule_id: str) -> list[dict[str, Any]]:
        pending = []
        notices = {notice["id"]: notice for notice in repository.list_schedule_notifications(schedule_id)}
        ordered_ids = []
        for event in repository.list_schedule_events(schedule_id):
            identifier = json.loads(event["state_json"])["notification_id"]
            if identifier is not None and identifier not in ordered_ids:
                ordered_ids.append(identifier)
        for identifier in ordered_ids:
            notice = notices[identifier]
            self._notification(notice)
            ack = repository.get_schedule_notification_ack(notice["id"])
            if ack is not None:
                workflow = repository.get_workflow_run(ack["workflow_run_id"])
                if workflow is None:
                    raise ScheduleIntegrityError("Notification acknowledgment audit is missing")
                validate_workflow(workflow, "schedule_ack", {"version": 1, "schedule_id": schedule_id,
                    "notification_id": notice["id"], "notification_sha256": notice["notification_sha256"],
                    "idempotency_sha256": workflow["idempotency_key"]})
                if (not _valid_at(ack["acknowledged_at"]) or ack["acknowledged_at"] != workflow["created_at"] or ack["acknowledged_at"] < notice["created_at"]
                    or json.loads(workflow["generated_artifacts_json"]) != [notice["id"]]):
                    raise ScheduleIntegrityError("Notification acknowledgment audit is invalid")
            else:
                link = repository.get_occurrence_search_link(notice["occurrence_id"])
                pending.append({"id": notice["id"], "notification_id": notice["id"], "occurrence_id": notice["occurrence_id"],
                    "run_id": None if link is None else link["run_id"],
                    "created_at": notice["created_at"], "delta": json.loads(notice["delta_json"])})
        return pending

    def _notice(self, repository: SQLiteRepository, schedule_id: str, manifest: dict[str, Any], occurrence_id: str,
                report: dict[str, Any] | None, *, failed: bool = False, retry_limit: bool = False,
                capacity: bool = False) -> None:
        control, _ = self._control(repository, schedule_id, manifest)
        try:
            update: NotificationUpdate
            if retry_limit:
                update = notification_retry_limit(manifest["search_id"], control["notification_summary"], report=report)
            elif capacity:
                update = notification_capacity(manifest["search_id"], control["notification_summary"], report=report)
            else:
                update = notification_failure(manifest["search_id"], control["notification_summary"]) if failed else notification_update(report, control["notification_summary"])
        except NotificationCapacityError:
            raise ScheduleCapacityError("Notification summary capacity reached; existing work remains durable") from None
        if update.summary == control["notification_summary"]:
            return
        at = self._now()
        if update.notify:
            payload = {"schedule_id": schedule_id, "occurrence_id": occurrence_id, "created_at": at,
                "delta": update.delta, "summary_sha256": digest(update.summary),
                "previous_summary_sha256": digest(control["notification_summary"])}
            identifier = str(uuid5(NAMESPACE_URL, "grounded-apply.notification@1/" + digest(payload)))
            repository.insert_schedule_notification(notification_id=identifier, **payload, notification_sha256=digest(payload))
            control["notification_id"] = identifier
        control["notification_summary"] = update.summary
        self._append_control(repository, schedule_id, manifest, "notification_updated", control, at=at)

    def _due(self, manifest: dict[str, Any], control: dict[str, Any], occurrence: tuple[Record, dict[str, Any]] | None) -> tuple[DailyOccurrence | None, str]:
        due = latest_due(self._clock(), **self._policy(manifest))
        current = None if occurrence is None else DailyOccurrence(date.fromisoformat(occurrence[0]["local_date"]), datetime.fromisoformat(occurrence[0]["due_at"]))
        if not control["enabled"]:
            return current or due, "paused"
        if occurrence is not None and occurrence[1]["status"] not in _TERMINAL | {"retry_limit"}:
            return current, "retry_limit" if occurrence[1]["attempts"] >= manifest["max_attempts"] else "resume"
        if due is None or control["last_local_date"] is not None and due.local_date.isoformat() < control["last_local_date"]:
            return current or due, "not_due"
        if occurrence is None or occurrence[0]["local_date"] < due.local_date.isoformat():
            return due, "start"
        return current, "retry_limit" if occurrence[1]["status"] == "retry_limit" else "terminal"

    def _gaps(self, manifest: dict[str, Any], control: dict[str, Any], events: list[Record], selected: date) -> tuple[int, int]:
        first = date.fromisoformat(manifest["start_date"]) if control["last_local_date"] is None else date.fromisoformat(control["last_local_date"]) + timedelta(days=1)
        toggles = [(datetime.fromisoformat(event["at"]), json.loads(event["state_json"])["enabled"])
            for event in events if event["action"] in {"created", "enabled_changed"}]
        missed = paused = position = 0
        enabled = True
        while first < selected:
            instant = due_instant(first, manifest["timezone"], manifest["local_time"])
            if instant is not None:
                while position < len(toggles) and toggles[position][0] <= instant:
                    enabled = toggles[position][1]
                    position += 1
                if enabled:
                    missed += 1
                else:
                    paused += 1
            first += timedelta(days=1)
        return missed, paused

    def _select_occurrence(self, repository: SQLiteRepository, schedule_id: str, manifest: dict[str, Any],
                           control: dict[str, Any], due: DailyOccurrence) -> tuple[Record, dict[str, Any]]:
        identifier = str(uuid5(NAMESPACE_URL, f"{schedule_id}/day/{due.local_date.isoformat()}"))
        if repository.get_schedule_occurrence(identifier) is None:
            at = self._now()
            record = {"id": identifier, "schedule_id": schedule_id, "local_date": due.local_date.isoformat(),
                "due_at": _at(due.due_at), "created_at": at}
            repository.insert_schedule_occurrence(occurrence_id=identifier, **{key: value for key, value in record.items() if key != "id"},
                record_sha256=digest(record))
            self._append_occurrence(repository, identifier, manifest, "created", _initial_occurrence(), at=at)
            _, events = self._control(repository, schedule_id, manifest)
            missed, paused = self._gaps(manifest, control, events, due.local_date)
            control = {**control, "current_occurrence_id": identifier, "last_local_date": due.local_date.isoformat(),
                "missed_dates": control["missed_dates"] + missed, "paused_dates": control["paused_dates"] + paused}
            self._append_control(repository, schedule_id, manifest, "occurrence_selected", control, at=at)
        return self._occurrence(repository, identifier, manifest)

    def _reconcile_completed(self, schedule_id: str, preview: dict[str, Any]) -> bool:
        """Recover committed descendants without dispatching or consuming another attempt."""
        if (preview["current_occurrence"] is None or preview["current_occurrence"]["status"] in _TERMINAL
            or preview["child"] is None or preview["child"]["remaining_count"] or not preview["enabled"]):
            return False
        with self._open(False) as repository, repository.transaction():
            _, manifest, control = self._read(repository, schedule_id)
            lease = self._lease(repository, schedule_id)
            if (not control["enabled"] or control["current_occurrence_id"] != preview["current_occurrence"]["id"]
                or lease["owner"] is not None and lease["expires_at"] > self._now()):
                return False
            occurrence_id = control["current_occurrence_id"]
            _, state = self._occurrence(repository, occurrence_id, manifest)
            if state["run_id"] != preview["run_id"] or state["status"] in _TERMINAL:
                return False
            search = self._search()
            child = search._view(repository, state["run_id"])
            if child["lease_active"] or child["remaining_count"] or child["phase"] == "discovering":
                return False
            _, child_manifest, child_state = search._validated(repository, state["run_id"])
            if child_state["phase"] != "complete":
                if child_state["batch_id"] is None:
                    return False
                retained_stop = child_state["stop_reason"] if child_state["stop_reason"] in {"request_budget", "byte_budget"} else None
                search._append(repository, state["run_id"], child_manifest, "child_progress",
                    {**child_state, "phase": "complete", "stop_reason": retained_stop}, reserve=False)
                child = search._view(repository, state["run_id"])
            status = "waiting_for_input" if child["blockers_count"] else "completed"
            self._append_occurrence(repository, occurrence_id, manifest, "child_reconciled",
                {**state, "status": status, "stop_reason": child["stop_reason"]})
            self._notice(repository, schedule_id, manifest, occurrence_id, child)
            self._capacity(repository, reserve=False)
            return True

    def tick(self, schedule_id: str, *, dry_run: bool = False) -> dict[str, Any]:
        if type(dry_run) is not bool:
            raise ValueError("Dry run must be boolean")
        preview = self.get(schedule_id)
        if not dry_run and self._reconcile_completed(schedule_id, preview):
            return self.get(schedule_id)
        if dry_run or preview["due"]["action"] in {"paused", "not_due", "terminal", "retry_limit"}:
            # Terminal ticks may refresh notification meaning (for example,
            # newly retired evidence), while never dispatching the child again.
            if not dry_run and preview["due"]["action"] == "retry_limit":
                with self._open(False) as repository, repository.transaction():
                    _, manifest, control = self._read(repository, schedule_id)
                    occurrence_id = control["current_occurrence_id"]
                    occurrence = self._occurrence(repository, occurrence_id, manifest)
                    _, action = self._due(manifest, control, occurrence)
                    lease = self._lease(repository, schedule_id)
                    state = occurrence[1]
                    if (occurrence_id == preview["current_occurrence"]["id"] and action == "retry_limit"
                        and state["status"] != "retry_limit" and (lease["owner"] is None or lease["expires_at"] <= self._now())):
                        self._append_occurrence(repository, occurrence_id, manifest, "retry_limit", {**state, "status": "retry_limit", "stop_reason": "retry_limit"})
                        self._notice(repository, schedule_id, manifest, occurrence_id, None, retry_limit=True)
                preview = self.get(schedule_id)
            if not dry_run and preview["due"]["action"] == "terminal" and preview["child"] is not None:
                with self._open(False) as repository, repository.transaction():
                    _, manifest, control = self._read(repository, schedule_id)
                    lease = self._lease(repository, schedule_id)
                    if (control["current_occurrence_id"] == preview["current_occurrence"]["id"]
                        and control["enabled"] and (lease["owner"] is None or lease["expires_at"] <= self._now())):
                        _, state = self._occurrence(repository, control["current_occurrence_id"], manifest)
                        if state["status"] in _TERMINAL and state["run_id"] == preview["run_id"]:
                            child = self._search()._view(repository, state["run_id"])
                            self._notice(repository, schedule_id, manifest, control["current_occurrence_id"], child)
                preview = self.get(schedule_id)
            return {**preview, "dry_run": dry_run, "executed": False}
        owner = str(uuid4())
        with self._open(False) as repository, repository.transaction():
            _, manifest, control = self._read(repository, schedule_id)
            existing = None if control["current_occurrence_id"] is None else self._occurrence(repository, control["current_occurrence_id"], manifest)
            due, action = self._due(manifest, control, existing)
            if due is None or action not in {"start", "resume"}:
                return {**preview, "executed": False}
            lease = self._lease(repository, schedule_id)
            if lease["owner"] is not None and lease["expires_at"] > self._now():
                raise ScheduleLeaseActiveError(schedule_id)
            occurrence, state = self._select_occurrence(repository, schedule_id, manifest, control, due)
            occurrence_id = occurrence["id"]
            now = self._now()
            epoch = repository.acquire_schedule_lease(schedule_id, occurrence_id=occurrence_id, owner=owner,
                expected_epoch=lease["epoch"], now=now,
                expires_at=_at(datetime.fromisoformat(now) + timedelta(seconds=manifest["max_seconds"] + 240)))
            if epoch is None:
                raise ScheduleLeaseActiveError(schedule_id)
            state = {**state, "status": "running", "attempts": state["attempts"] + 1, "stop_reason": None}
            self._append_occurrence(repository, occurrence_id, manifest, "attempted", state)
            self._assert_lease(repository, schedule_id, occurrence_id, owner, epoch)
        executed, lease_deferred, capacity, failure, report = True, False, False, None, None
        def guard(repository: SQLiteRepository) -> None:
            self._assert_lease(repository, schedule_id, occurrence_id, owner, epoch)
        def bind(repository: SQLiteRepository, run_id: str) -> None:
            guard(repository)
            _, current = self._occurrence(repository, occurrence_id, manifest)
            if current["run_id"] is None:
                if run_id != self._run_id(manifest["search_id"], occurrence_id):
                    raise ScheduleIntegrityError("Scheduled search child identity is invalid")
                repository.insert_scheduled_search_link(run_id, occurrence_id)
                self._append_occurrence(repository, occurrence_id, manifest, "child_bound", {**current, "run_id": run_id})
            elif current["run_id"] != run_id:
                raise ScheduleIntegrityError("Scheduled search child identity changed")
            guard(repository)
        service = self._search(parent_guard=guard, bind_run=bind)
        try:
            if state["run_id"] is None:
                report = service.run(manifest["search_id"], idempotency_key="schedule-run." + occurrence_id,
                    max_items=manifest["max_items"], max_seconds=manifest["max_seconds"])
            else:
                report = service.resume(state["run_id"], max_items=manifest["max_items"], max_seconds=manifest["max_seconds"])
        except SearchLeaseActiveError:
            executed, lease_deferred = False, True
        except StorageCapacityError:
            capacity = True
        except Exception as error:
            failure = error
        finally:
            with self._open(False) as repository, repository.transaction():
                lease = self._lease(repository, schedule_id)
                if lease["owner"] == owner and lease["epoch"] == epoch and lease["occurrence_id"] == occurrence_id:
                    latest = max(repository.list_schedule_events(schedule_id)[-1]["at"], repository.list_occurrence_events(occurrence_id)[-1]["at"])
                    if latest <= self._now() < lease["expires_at"] and (report is not None or failure is not None or lease_deferred or capacity):
                        _, current = self._occurrence(repository, occurrence_id, manifest)
                        if lease_deferred:
                            current = {**current, "status": "queued", "attempts": current["attempts"] - 1, "stop_reason": "lease_active"}
                            self._append_occurrence(repository, occurrence_id, manifest, "lease_deferred", current)
                        else:
                            if capacity and current["run_id"] is not None:
                                report = self._search()._view(repository, current["run_id"])
                            status = "failed" if failure is not None else "budget_exhausted" if capacity or report["remaining_count"] else "waiting_for_input" if report["blockers_count"] else "completed"
                            stop = "shared_failure" if failure is not None else "capacity_reached" if capacity else report["stop_reason"]
                            current = {**current, "status": status, "stop_reason": stop}
                            self._append_occurrence(repository, occurrence_id, manifest, "finished", current)
                            self._notice(repository, schedule_id, manifest, occurrence_id, report, failed=failure is not None,
                                retry_limit=current["attempts"] >= manifest["max_attempts"] and status not in _TERMINAL,
                                capacity=capacity)
                    repository.release_schedule_lease(schedule_id, occurrence_id=occurrence_id, owner=owner, epoch=epoch)
                    self._capacity(repository, reserve=False)
        if failure is not None:
            with self._open(True) as repository:
                _, current = self._occurrence(repository, occurrence_id, manifest)
            raise ScheduleExecutionError(schedule_id, occurrence_id, current["run_id"]) from failure
        result = self.get(schedule_id)
        result["executed"] = executed
        if lease_deferred:
            result["due"]["action"], result["stop_reason"] = "lease_active", "lease_active"
        return result

    def get(self, schedule_id: str) -> dict[str, Any]:
        with self._open(True) as repository, repository.read_transaction():
            record, manifest, control = self._read(repository, schedule_id)
            occurrence = None if control["current_occurrence_id"] is None else self._occurrence(repository, control["current_occurrence_id"], manifest)
            pending = self._pending(repository, schedule_id)
            lease = self._lease(repository, schedule_id)
            due, action = self._due(manifest, control, occurrence)
            state = _initial_occurrence() if occurrence is None else occurrence[1]
            active = lease["owner"] is not None and lease["expires_at"] > self._now()
            if active and control["enabled"]:
                action = "lease_active"
            run_id = state["run_id"]
        child = None if run_id is None else self._search().get(run_id)
        status = "paused" if not control["enabled"] else "running" if active else "not_due" if action == "not_due" else "retry_limit" if action == "retry_limit" else "scheduled" if action == "start" else state["status"]
        if status == "queued":
            status = "scheduled"
        return {"schema_version": 1, "schedule_id": schedule_id, "manifest": manifest, "created_at": record["created_at"],
            "enabled": control["enabled"], "status": status,
            "due": {"local_date": None if due is None else due.local_date.isoformat(), "due_at": None if due is None else _at(due.due_at), "action": action},
            "next_due": _due_view(next_due(self._clock(), **self._policy(manifest))),
            "current_occurrence": None if occurrence is None else {**occurrence[0], **state}, "run_id": run_id,
            "attempts": state["attempts"], "remaining_attempts": max(0, manifest["max_attempts"] - state["attempts"]),
            "child": child, "notification": pending[0] if pending else None, "notifications_pending": len(pending),
            "notify": bool(pending), "executed": False, "dry_run": False, "stop_reason": state["stop_reason"],
            "missed_dates": control["missed_dates"], "paused_dates": control["paused_dates"],
            "external_action_taken": False, "approvals_recorded": False, "application_ready": False, "scheduler_installed": False}

    def list(self) -> tuple[dict[str, Any], ...]:
        with self._open(True) as repository:
            ids = [record["id"] for record in repository.list_daily_schedules()]
        return tuple(self.get(identifier) for identifier in ids)

    def set_enabled(self, schedule_id: str, enabled: bool, *, idempotency_key: str) -> dict[str, Any]:
        if type(enabled) is not bool:
            raise ValueError("Schedule enabled flag must be boolean")
        payload = request_input(idempotency_key, {"schedule_id": opaque(schedule_id), "enabled": enabled})
        with self._open(False) as repository, repository.transaction():
            _, manifest, control = self._read(repository, schedule_id)
            previous = existing_workflow(repository, "schedule_enable", payload)
            if previous is None:
                at = self._now()
                workflow = start_workflow(repository, "schedule_enable", payload, at)
                finish_workflow(repository, workflow["id"], [schedule_id], at)
                self._append_control(repository, schedule_id, manifest, "enabled_changed", {**control, "enabled": enabled},
                    workflow_run_id=workflow["id"], at=at)
                if not enabled:
                    repository.revoke_schedule_lease(schedule_id)
        result = self.get(schedule_id)
        result["replayed"] = previous is not None
        return result

    def ack(self, schedule_id: str, notification_id: str, *, idempotency_key: str) -> dict[str, Any]:
        opaque(notification_id)
        with self._open(False) as repository, repository.transaction():
            self._read(repository, schedule_id)
            self._pending(repository, schedule_id)
            notice = repository.get_schedule_notification(notification_id)
            if notice is None or notice["schedule_id"] != schedule_id:
                raise ValueError("Notification does not belong to this schedule")
            payload = request_input(idempotency_key, {"schedule_id": schedule_id, "notification_id": notification_id,
                "notification_sha256": notice["notification_sha256"]})
            previous = existing_workflow(repository, "schedule_ack", payload)
            if previous is None:
                if repository.get_schedule_notification_ack(notification_id) is not None:
                    raise ValueError("Notification was already acknowledged; replay its original acknowledgment key")
                at = self._now()
                workflow = start_workflow(repository, "schedule_ack", payload, at)
                repository.insert_schedule_notification_ack(notification_id, at, workflow["id"])
                finish_workflow(repository, workflow["id"], [notification_id], at)
                self._capacity(repository, reserve=False)
            self._pending(repository, schedule_id)
        result = self.get(schedule_id)
        result["replayed"] = previous is not None
        return result
