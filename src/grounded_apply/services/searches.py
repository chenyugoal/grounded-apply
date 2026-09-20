"""Durable on-demand public discovery into an evidence-backed draft queue.

The runtime factory is closed around every network call. Search events retain
identifiers, counters and source coverage; job descriptions have one owner in
immutable job snapshots. Scheduling and external application actions are absent.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from grounded_apply.repositories import Record, RepositoryError, SQLiteRepository
from grounded_apply.services.backup import MAX_SNAPSHOT_BYTES
from grounded_apply.services.storage_limits import StorageCapacityError
from grounded_apply.services.batches import BatchService, _at, _hash, _valid_at, validate_batch_manifest
from grounded_apply.services.discovery import (
    DiscoveryErrorCode, DiscoveryService, MAX_RESPONSE_BYTES, SourceSpec, Transport,
    validate_source_manifest,
)
from grounded_apply.services.jobs import DiscoveryCapacityError, DiscoveryRecordError, JobService
from grounded_apply.services.material_models import ResumeRenderer
from grounded_apply.services.materials import MaterialBlocked, MaterialService
from grounded_apply.services.review_exports import ReviewMaterial, SearchReviewSnapshot
from grounded_apply.services.search_policy import SearchEligibilityPolicy, validate_excluded_identities
from grounded_apply.services.search_filters import PreparationFilters, validate_preparation_filters
from grounded_apply.services.source_rotation import (
    SourceRotation, source_indices, validate_rotation_ledger, validate_source_rotation,
)
from grounded_apply.services.workflow import (
    canonical, digest, existing_workflow, finish_workflow, hash_bytes, opaque,
    request_input, start_workflow, validate_workflow,
)


RepositoryFactory = Callable[[bool], AbstractContextManager[SQLiteRepository]]
SEARCH_RESERVE_BYTES = 512 * 1024
_STOPS = {None, "item_budget", "time_budget", "request_budget", "byte_budget", "capacity_reached", "shared_failure"}
_ACTIONS = {"created", "resumed", "source_order_reserved", "source_started", "request_reserved", "request_settled", "source_completed",
    "source_deferred", "selection_frozen", "child_created", "child_progress", "stopped"}
_SKIPS = {"explicitly_excluded", "already_applied", "unchanged_draft", "invalid_record", "deferred"}
_FILTER_SKIPS = {"title_excluded", "location_excluded", "location_not_matched", "location_unknown_excluded"}
_STATE_FIELDS = {"phase", "sources", "selected_job_ids", "selection_skipped", "batch_id", "requests_used",
    "bytes_charged", "inflight_bytes", "stop_reason"}


class SearchIntegrityError(RepositoryError):
    """A saved scope, checkpoint or child association failed validation."""


class SearchLeaseActiveError(ValueError):
    """Another invocation owns the saved scope."""
    def __init__(self, run_id: str) -> None:
        super().__init__("Another invocation owns this saved search; resume after its lease expires")
        self.run_id = run_id


class SearchLeaseLostError(RepositoryError):
    """A replaced or expired parent cannot change this search or its child."""


class SearchCapacityError(StorageCapacityError):
    """The bounded search cannot add another durable checkpoint."""


class SearchExecutionError(RepositoryError):
    def __init__(self, run_id: str) -> None:
        super().__init__("Saved search execution stopped; inspect its validated checkpoints before resuming")
        self.run_id = run_id
        self.reason = "shared_failure"


class _Pause(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason


class _NetworkAbort(BaseException):
    """Escape source adapters' ordinary transport-error handling."""
    def __init__(self, cause: Exception) -> None:
        self.cause = cause


class _NetworkBudget(Exception):
    code = "source_limit_reached"


class _NetworkResponseTooLarge(Exception):
    code = "response_too_large"


def validate_search_manifest(manifest: object) -> dict[str, Any]:
    fields = {"schema_version", "sources", "title_contains", "claim_ids", "layout", "questions",
        "questionnaire_coverage", "excluded_identities", "max_jobs", "max_requests", "max_bytes"}
    if type(manifest) is dict and manifest.get("schema_version") == 2:
        fields.add("preparation_filters")
    if (type(manifest) is not dict or not {"schema_version", "sources", "claim_ids"} <= set(manifest)
        or set(manifest) - fields or type(manifest["schema_version"]) is not int or manifest["schema_version"] not in {1, 2}):
        raise ValueError("Search requires schema_version 1 or 2, explicit sources and shared claim_ids")
    sources = validate_source_manifest({"schema_version": 1, "sources": manifest["sources"]})
    filters = manifest.get("title_contains", [])
    if (type(filters) is not list or len(filters) > 20
        or any(type(term) is not str or not term.strip() or term != term.strip() or len(term) > 128
               or any(ord(char) < 32 or ord(char) == 127 for char in term) for term in filters)):
        raise ValueError("Search title filters require at most twenty bounded nonblank terms")
    prep = validate_batch_manifest({"schema_version": 1, "jobs": [{"job_id": "search-preview"}],
        **{key: manifest[key] for key in ("claim_ids", "layout", "questions", "questionnaire_coverage") if key in manifest}})
    exclusions = validate_excluded_identities(manifest.get("excluded_identities", []))
    limits = {"max_jobs": (10, 50), "max_requests": (64, 320), "max_bytes": (64 * 1024 * 1024, 256 * 1024 * 1024)}
    selected = {}
    for key, (default, maximum) in limits.items():
        value = manifest.get(key, default)
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError("Search limits must be positive integers within supported bounds")
        selected[key] = value
    result = {"schema_version": manifest["schema_version"],
        "sources": [{key: value for key, value in asdict(source).items() if value is not None} for source in sources],
        "title_contains": list(filters), **{key: prep[key] for key in ("claim_ids", "layout", "questions", "questionnaire_coverage")},
        "excluded_identities": [identity.to_dict() for identity in exclusions], **selected}
    if manifest["schema_version"] == 2:
        result["preparation_filters"] = validate_preparation_filters(manifest.get("preparation_filters", {})).to_dict()
    if len(canonical(result).encode("utf-8")) > 1024 * 1024:
        raise ValueError("Search specification exceeds its bounded size")
    return result


def _skip_reasons(manifest: dict[str, Any]) -> set[str]:
    return _SKIPS | _FILTER_SKIPS if manifest["schema_version"] == 2 else _SKIPS


def _preparation_filters(manifest: dict[str, Any]) -> PreparationFilters | None:
    return validate_preparation_filters(manifest["preparation_filters"]) if manifest["schema_version"] == 2 else None


def _require_preparation_match(manifest: dict[str, Any], job: Any) -> None:
    if job.discovery is None:
        raise SearchIntegrityError("Captured posting has no validated discovery metadata")
    title = job.discovery["title"]
    if manifest["title_contains"] and not any(term.casefold() in title.casefold() for term in manifest["title_contains"]):
        raise SearchIntegrityError("Captured posting does not satisfy its immutable title retrieval filters")
    filters = _preparation_filters(manifest)
    if filters is not None and filters.assess(title=title, location=job.discovery["location"]) is not None:
        raise SearchIntegrityError("Captured posting does not satisfy its immutable preparation filters")


def _initial(manifest: dict[str, Any]) -> dict[str, Any]:
    return {"phase": "discovering", "sources": [{"source_id": source["id"], "stage": "pending", "attempts": 0,
        "report": None, "job_ids": [], "skipped": {reason: 0 for reason in sorted(_skip_reasons(manifest))}} for source in manifest["sources"]],
        "selected_job_ids": None, "selection_skipped": [], "batch_id": None, "requests_used": 0,
        "bytes_charged": 0, "inflight_bytes": 0, "stop_reason": None}


def _window(value: object, source: dict[str, Any]) -> dict[str, Any]:
    """Validate the optional v1 cursor envelope without rewriting legacy events."""
    from grounded_apply.services.source_windows import validate_netflix_cursor, validate_netflix_window_progress
    fields = {"schema_version", "policy", "generation", "reserved_epoch", "base_run_id", "base_generation",
        "base_progress_sha256", "after", "completed_epoch", "progress"}
    if (type(value) is not dict or set(value) != fields or type(value["schema_version"]) is not int
        or value["schema_version"] != 1 or value["policy"] != "netflix_head1_tail6@1"
        or any(type(value[key]) is not int or not 1 <= value[key] <= 1000000000 for key in ("generation", "reserved_epoch"))):
        raise ValueError("Netflix window checkpoint is invalid")
    cursor = validate_netflix_cursor(value["after"])
    if value["after"] != (None if cursor is None else cursor.to_dict()):
        raise ValueError("Netflix window input is not canonical")
    if value["base_run_id"] is None:
        if value["base_generation"] is not None or value["base_progress_sha256"] is not None or value["after"] is not None:
            raise ValueError("Netflix initial window has an invalid base")
    else:
        opaque(value["base_run_id"])
        _hash(value["base_progress_sha256"])
        if type(value["base_generation"]) is not int or not 1 <= value["base_generation"] < value["generation"]:
            raise ValueError("Netflix window base generation is invalid")
    if value["progress"] is None:
        if value["completed_epoch"] is not None or source["stage"] == "complete":
            raise ValueError("Netflix completed window has no progress")
    else:
        progress = validate_netflix_window_progress(value["progress"])
        if (progress.to_dict() != value["progress"] or value["progress"]["after"] != value["after"]
            or value["progress"]["policy"] != value["policy"] or source["stage"] != "complete"
            or type(value["completed_epoch"]) is not int or not value["reserved_epoch"] <= value["completed_epoch"] <= 1000000000):
            raise ValueError("Netflix window completion is invalid")
    return value


def _event(run_id: str, position: int, at: str, action: str, state: object, previous: str | None) -> dict[str, Any]:
    return {"run_id": run_id, "position": position, "at": at, "action": action, "state": state, "previous_sha256": previous}


def _candidate_limit(manifest: dict[str, Any]) -> int:
    return min(200, 4 * manifest["max_jobs"])


def _round_robin(sources: list[dict[str, Any]]) -> list[str]:
    return [source["job_ids"][index] for index in range(max((len(source["job_ids"]) for source in sources), default=0))
        for source in sources if index < len(source["job_ids"])]


def _source_indices(state: dict[str, Any], manifest: dict[str, Any]) -> tuple[int, ...]:
    reservation = None if "source_order" not in state else validate_source_rotation(state["source_order"])
    return source_indices(tuple(source["provider"] for source in manifest["sources"]), reservation)


def _pristine(state: dict[str, Any], manifest: dict[str, Any]) -> bool:
    return state == _initial(manifest)


class SearchService:
    def __init__(self, repository_factory: RepositoryFactory, transport: Transport, renderer: ResumeRenderer, *,
                 clock: Callable[[], datetime] | None = None, monotonic: Callable[[], float] = time.monotonic,
                 parent_guard: Callable[[SQLiteRepository], None] | None = None,
                 bind_run: Callable[[SQLiteRepository, str], None] | None = None) -> None:
        self._open = repository_factory
        self._transport = transport
        self._renderer = renderer
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic = monotonic
        self._parent_guard = parent_guard
        self._bind_run = bind_run

    def _now(self) -> str:
        return _at(self._clock())

    @staticmethod
    def _capacity(repository: SQLiteRepository, *, reserve: bool = True) -> None:
        if repository.database_size_bytes() > MAX_SNAPSHOT_BYTES - (SEARCH_RESERVE_BYTES if reserve else 0):
            raise SearchCapacityError("Search storage capacity reached; no new checkpoint was added")

    def configure(self, manifest: object, *, idempotency_key: str, dry_run: bool = False) -> dict[str, Any]:
        spec = validate_search_manifest(manifest)
        if type(dry_run) is not bool:
            raise ValueError("Dry run must be boolean")
        payload = request_input(idempotency_key, {"manifest_sha256": digest(spec)})
        if dry_run:
            return {"schema_version": 1, "dry_run": True, "search_id": None, "manifest": spec,
                "profile_validated": False, "network_accessed": False, "external_action_taken": False}
        search_id = str(uuid5(NAMESPACE_URL, "grounded-apply.search@1/" + payload["idempotency_sha256"]))
        with self._open(False) as repository, repository.transaction():
            existing = existing_workflow(repository, "search_configure", payload)
            if existing is None:
                at = self._now()
                workflow = start_workflow(repository, "search_configure", payload, at)
                repository.insert_saved_search(search_id=search_id, manifest=spec, manifest_sha256=digest(spec),
                    created_at=at, workflow_run_id=workflow["id"])
                finish_workflow(repository, workflow["id"], [search_id], at)
                self._capacity(repository)
            scope, normalized = self._scope(repository, search_id)
            return {"schema_version": 1, "dry_run": False, "search_id": search_id, "manifest": normalized,
                "created_at": scope["created_at"], "replayed": existing is not None,
                "profile_validated": False, "network_accessed": False, "external_action_taken": False}

    def _scope(self, repository: SQLiteRepository, search_id: str) -> tuple[Record, dict[str, Any]]:
        opaque(search_id)
        record = repository.get_saved_search(search_id)
        if record is None:
            raise ValueError("Saved search does not exist")
        try:
            manifest = json.loads(record["manifest_json"])
            if validate_search_manifest(manifest) != manifest or record["manifest_sha256"] != digest(manifest) or not _valid_at(record["created_at"]):
                raise ValueError
            workflow = repository.get_workflow_run(record["workflow_run_id"])
            if workflow is None:
                raise ValueError
            validate_workflow(workflow, "search_configure", {"version": 1, "manifest_sha256": digest(manifest),
                "idempotency_sha256": workflow["idempotency_key"]})
            if (search_id != str(uuid5(NAMESPACE_URL, "grounded-apply.search@1/" + workflow["idempotency_key"]))
                or workflow["created_at"] != record["created_at"] or json.loads(workflow["generated_artifacts_json"]) != [search_id]):
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise SearchIntegrityError("Saved search failed integrity checks") from None
        return record, manifest

    def _lease(self, repository: SQLiteRepository, search_id: str) -> Record:
        lease = repository.get_search_lease(search_id)
        try:
            if (lease is None or lease["search_id"] != search_id or type(lease["epoch"]) is not int or lease["epoch"] < 0
                or (lease["owner"] is None) != (lease["expires_at"] is None)
                or (lease["owner"] is None) != (lease["run_id"] is None)):
                raise ValueError
            if lease["owner"] is not None:
                opaque(lease["owner"])
                opaque(lease["run_id"])
                if not _valid_at(lease["expires_at"]):
                    raise ValueError
                run = repository.get_search_run(lease["run_id"])
                if run is None or run["search_id"] != search_id:
                    raise ValueError
        except (ValueError, TypeError, KeyError):
            raise SearchIntegrityError("Saved search lease failed integrity checks") from None
        return lease

    def _assert_lease(self, repository: SQLiteRepository, search_id: str, run_id: str, owner: str, epoch: int) -> None:
        if self._parent_guard is not None:
            self._parent_guard(repository)
        lease = self._lease(repository, search_id)
        if (lease["run_id"] != run_id or lease["owner"] != owner or lease["epoch"] != epoch or lease["expires_at"] <= self._now()):
            raise SearchLeaseLostError("Saved search lease expired or was replaced; resume its durable run")

    def _state(self, value: object, manifest: dict[str, Any]) -> dict[str, Any]:
        fields = _STATE_FIELDS | {"source_order"} if type(value) is dict and "source_order" in value else _STATE_FIELDS
        if (type(value) is not dict or set(value) != fields or value["phase"] not in {"discovering", "preparing", "complete"}
            or value["stop_reason"] not in _STOPS or type(value["sources"]) is not list
            or len(value["sources"]) != len(manifest["sources"])):
            raise ValueError
        if "source_order" in value:
            validate_source_rotation(value["source_order"])
        for key, maximum in (("requests_used", manifest["max_requests"]), ("bytes_charged", manifest["max_bytes"]), ("inflight_bytes", MAX_RESPONSE_BYTES)):
            if type(value[key]) is not int or not 0 <= value[key] <= maximum:
                raise ValueError
        if value["inflight_bytes"] > value["bytes_charged"]:
            raise ValueError
        jobs = []
        for source, spec in zip(value["sources"], manifest["sources"], strict=True):
            source_fields = {"source_id", "stage", "attempts", "report", "job_ids", "skipped"}
            if type(source) is dict and "window" in source:
                source_fields.add("window")
            if (type(source) is not dict or set(source) != source_fields
                or source["source_id"] != spec["id"] or source["stage"] not in {"pending", "fetching", "complete", "deferred"}
                or type(source["attempts"]) is not int or not 0 <= source["attempts"] <= 3
                or type(source["job_ids"]) is not list or len(source["job_ids"]) > manifest["max_jobs"]
                or type(source["skipped"]) is not dict or set(source["skipped"]) != _skip_reasons(manifest)
                or any(type(count) is not int or not 0 <= count <= 10000 for count in source["skipped"].values())):
                raise ValueError
            if "window" in source:
                if spec["provider"] != "netflix" or source["stage"] == "pending":
                    raise ValueError
                _window(source["window"], source)
                if "source_order" in value and source["window"]["reserved_epoch"] < value["source_order"]["reserved_epoch"]:
                    raise ValueError
            for identifier in source["job_ids"]:
                opaque(identifier)
            jobs.extend(source["job_ids"])
            report = source["report"]
            if source["stage"] == "complete":
                if (type(report) is not dict or set(report) != {"source_id", "provider", "board", "careers_url", "status", "count", "filtered_count",
                    "observed_count", "error", "fetched_at", "errors", "indexed_count", "remaining_count"}
                    or any(report[key] != spec.get(key) for key in ("provider", "board", "careers_url"))
                    or report["source_id"] != spec["id"] or report["status"] not in {"successful", "partial", "failed", "manual_required"}
                    or type(report["errors"]) is not list or len(report["errors"]) > len(DiscoveryErrorCode)
                    or len(report["errors"]) != len(set(report["errors"]))
                    or report["error"] != (report["errors"][0] if report["errors"] else None)
                    or any(error not in {code.value for code in DiscoveryErrorCode} for error in report["errors"])
                    or len(source["job_ids"]) > report["count"]):
                    raise ValueError
                if (spec["provider"] == "manual") is not (report["status"] == "manual_required"):
                    raise ValueError
                if (report["status"] in {"failed", "partial"}) is not bool(report["errors"]):
                    raise ValueError
                if report["status"] in {"failed", "manual_required"} and (report["count"] or source["job_ids"]):
                    raise ValueError
                for key in ("count", "filtered_count", "observed_count", "indexed_count", "remaining_count"):
                    number = report[key]
                    if number is None and key in {"indexed_count", "remaining_count"}:
                        continue
                    if type(number) is not int or not 0 <= number <= 1000000:
                        raise ValueError
                if (len(source["job_ids"]) + sum(source["skipped"].values()) != report["count"]
                    or report["count"] + report["filtered_count"] > report["observed_count"]):
                    raise ValueError
                if datetime.fromisoformat(report["fetched_at"]).utcoffset() is None:
                    raise ValueError
                if "window" in source and source["window"]["progress"]["indexed_count"] != report["indexed_count"]:
                    raise ValueError
            elif report is not None or source["job_ids"]:
                raise ValueError
        if len(jobs) != len(set(jobs)) or len(jobs) > _candidate_limit(manifest):
            raise ValueError
        if sum(source["stage"] == "fetching" for source in value["sources"]) > 1:
            raise ValueError
        selected = value["selected_job_ids"]
        if selected is not None and (type(selected) is not list or len(selected) > manifest["max_jobs"]
            or len(selected) != len(set(selected)) or any(job not in jobs for job in selected)):
            raise ValueError
        if type(value["selection_skipped"]) is not list or len(value["selection_skipped"]) > _candidate_limit(manifest):
            raise ValueError
        for skipped in value["selection_skipped"]:
            if (type(skipped) is not dict or set(skipped) != {"job_id", "reason"} or skipped["job_id"] not in jobs
                or skipped["reason"] not in {"explicitly_excluded", "already_applied", "unchanged_draft", "preparation_limit"}):
                raise ValueError
        if selected is not None:
            all_ids = selected + [skipped["job_id"] for skipped in value["selection_skipped"]]
            if len(all_ids) != len(set(all_ids)) or set(all_ids) != set(jobs):
                raise ValueError
        elif value["selection_skipped"]:
            raise ValueError
        if value["batch_id"] is not None:
            opaque(value["batch_id"])
            if not selected:
                raise ValueError
        if (value["phase"] == "discovering") is not (selected is None):
            raise ValueError
        if value["phase"] == "complete" and selected and value["batch_id"] is None:
            raise ValueError
        if selected is not None and any(source["stage"] not in {"complete", "deferred"} for source in value["sources"]):
            raise ValueError
        return value

    @staticmethod
    def _transition(previous: dict[str, Any], state: dict[str, Any], action: str, manifest: dict[str, Any]) -> None:
        allowed = {
            "source_order_reserved": {"source_order"},
            "resumed": {"stop_reason"}, "source_started": {"sources", "inflight_bytes"},
            "request_reserved": {"requests_used", "bytes_charged", "inflight_bytes"},
            "request_settled": {"bytes_charged", "inflight_bytes"}, "source_completed": {"sources"},
            "source_deferred": {"sources", "inflight_bytes", "stop_reason"},
            "selection_frozen": {"phase", "selected_job_ids", "selection_skipped"},
            "child_created": {"batch_id"}, "child_progress": {"phase", "stop_reason"}, "stopped": {"stop_reason"},
        }
        if action not in allowed or {key for key in set(state) | set(previous) if state.get(key) != previous.get(key)} - allowed[action]:
            raise ValueError
        if action == "source_order_reserved" and (not _pristine(previous, manifest) or "source_order" not in state):
            raise ValueError
        if action == "resumed" and (previous["stop_reason"] is None or state["stop_reason"] is not None):
            raise ValueError
        if action == "stopped" and state["stop_reason"] is None:
            raise ValueError
        if action in {"source_started", "source_completed", "source_deferred", "request_reserved", "request_settled", "selection_frozen"} and previous["phase"] != "discovering":
            raise ValueError
        if action in {"source_started", "source_completed", "source_deferred"}:
            changes = [(old, new) for old, new in zip(previous["sources"], state["sources"], strict=True) if old != new]
            if len(changes) != 1:
                raise ValueError
            old, new = changes[0]
            if old["stage"] not in {"pending", "fetching"}:
                raise ValueError
            first_pending = next(previous["sources"][index] for index in _source_indices(previous, manifest)
                if previous["sources"][index]["stage"] in {"pending", "fetching"})
            if old["source_id"] != first_pending["source_id"]:
                raise ValueError
            if action == "source_started":
                expected = {**old, "stage": "fetching", "attempts": old["attempts"] + 1}
                if "window" not in old and "window" in new:
                    expected["window"] = new["window"]
                if (new != expected
                    or state["inflight_bytes"] != 0 or previous["inflight_bytes"] and old["stage"] != "fetching"):
                    raise ValueError
            elif action == "source_completed":
                if "window" in old:
                    if ("window" not in new or {key: value for key, value in new["window"].items() if key not in {"progress", "completed_epoch"}}
                        != {key: value for key, value in old["window"].items() if key not in {"progress", "completed_epoch"}}):
                        raise ValueError
                elif "window" in new:
                    raise ValueError
                if old["stage"] != "fetching" or new["stage"] != "complete" or new["attempts"] != old["attempts"] or previous["inflight_bytes"]:
                    raise ValueError
            else:
                if (new != {**old, "stage": "deferred"} or state["inflight_bytes"] != 0
                    or state["stop_reason"] not in {None, "request_budget", "byte_budget"}
                    or not (old["attempts"] >= 3 or previous["requests_used"] >= manifest["max_requests"]
                        or previous["bytes_charged"] >= manifest["max_bytes"])):
                    raise ValueError
        if action in {"request_reserved", "request_settled"}:
            if sum(source["stage"] == "fetching" for source in previous["sources"]) != 1:
                raise ValueError
            if action == "request_settled" and previous["inflight_bytes"] <= 0:
                raise ValueError
        if action == "selection_frozen":
            if previous["selected_job_ids"] is not None or state["selected_job_ids"] is None:
                raise ValueError
            skipped = {item["job_id"]: item["reason"] for item in state["selection_skipped"]}
            ordered = _round_robin([state["sources"][index] for index in _source_indices(state, manifest)])
            if state["selected_job_ids"] != [identifier for identifier in ordered if identifier not in skipped]:
                raise ValueError
            selected_seen = 0
            for identifier in ordered:
                if identifier not in skipped:
                    selected_seen += 1
                elif skipped[identifier] == "preparation_limit" and selected_seen != manifest["max_jobs"]:
                    raise ValueError
            if state["phase"] != ("preparing" if state["selected_job_ids"] else "complete"):
                raise ValueError
        if action == "child_created" and (previous["phase"] != "preparing" or previous["batch_id"] is not None or state["batch_id"] is None):
            raise ValueError
        if action == "child_progress" and (previous["batch_id"] is None or state["phase"] not in {"preparing", "complete"}):
            raise ValueError

    def _history(self, repository: SQLiteRepository, run_id: str, manifest: dict[str, Any]) -> tuple[dict[str, Any], list[Record]]:
        events = repository.list_search_events(run_id)
        if not events:
            raise SearchIntegrityError("Search checkpoints are missing")
        previous, previous_hash, previous_at = None, None, None
        try:
            for position, event in enumerate(events):
                state = self._state(json.loads(event["state_json"]), manifest)
                if (event["run_id"] != run_id or event["position"] != position or event["action"] not in _ACTIONS
                    or event["id"] != str(uuid5(NAMESPACE_URL, f"{run_id}/event/{position}")) or not _valid_at(event["at"])
                    or previous_at is not None and event["at"] < previous_at or event["previous_sha256"] != previous_hash
                    or event["event_sha256"] != digest(_event(run_id, position, event["at"], event["action"], state, previous_hash))):
                    raise ValueError
                if previous is None:
                    if event["action"] != "created" or state != _initial(manifest):
                        raise ValueError
                else:
                    self._transition(previous, state, event["action"], manifest)
                    if (previous["selected_job_ids"] is not None and (state["selected_job_ids"] != previous["selected_job_ids"]
                        or state["selection_skipped"] != previous["selection_skipped"])) or (previous["batch_id"] is not None and state["batch_id"] != previous["batch_id"]):
                        raise ValueError
                    if state["requests_used"] < previous["requests_used"]:
                        raise ValueError
                    for old, new in zip(previous["sources"], state["sources"], strict=True):
                        if old["stage"] in {"complete", "deferred"} and old != new or new["attempts"] < old["attempts"]:
                            raise ValueError
                    if event["action"] == "request_reserved":
                        if (state["requests_used"] != previous["requests_used"] + 1 or previous["inflight_bytes"] != 0
                            or not 1 <= state["inflight_bytes"] <= MAX_RESPONSE_BYTES
                            or state["bytes_charged"] != previous["bytes_charged"] + state["inflight_bytes"]):
                            raise ValueError
                    elif state["requests_used"] != previous["requests_used"]:
                        raise ValueError
                    if event["action"] == "request_settled":
                        if (state["inflight_bytes"] != 0 or not previous["bytes_charged"] - previous["inflight_bytes"] <= state["bytes_charged"] <= previous["bytes_charged"]):
                            raise ValueError
                    elif event["action"] != "request_reserved" and state["bytes_charged"] != previous["bytes_charged"]:
                        raise ValueError
                previous, previous_hash, previous_at = state, event["event_sha256"], event["at"]
        except (ValueError, TypeError, KeyError):
            raise SearchIntegrityError("Search checkpoint history failed integrity checks") from None
        assert previous is not None
        return previous, events

    def _validated(self, repository: SQLiteRepository, run_id: str, *, check_windows: bool = True,
                   check_rotation: bool = True) -> tuple[Record, dict[str, Any], dict[str, Any]]:
        opaque(run_id)
        run = repository.get_search_run(run_id)
        if run is None:
            raise ValueError("Search run does not exist")
        _, manifest = self._scope(repository, run["search_id"])
        try:
            workflow = repository.get_workflow_run(run["workflow_run_id"])
            if workflow is None:
                raise ValueError
            validate_workflow(workflow, "search_run_create", {"version": 1, "search_id": run["search_id"],
                "manifest_sha256": digest(manifest), "idempotency_sha256": workflow["idempotency_key"]})
            if (run_id != str(uuid5(NAMESPACE_URL, f"grounded-apply.search-run@1/{run['search_id']}/{workflow['idempotency_key']}"))
                or workflow["created_at"] != run["created_at"] or not _valid_at(run["created_at"])
                or json.loads(workflow["generated_artifacts_json"]) != [run_id]):
                raise ValueError
            state, events = self._history(repository, run_id, manifest)
            if events[0]["at"] != run["created_at"]:
                raise ValueError
            for source, spec in zip(state["sources"], manifest["sources"], strict=True):
                for job_id in source["job_ids"]:
                    job = JobService(repository).get(job_id)
                    if job.discovery is None or job.discovery["provider"] != spec["provider"] or job.discovery["board"] != spec.get("board"):
                        raise ValueError
                    _require_preparation_match(manifest, job)
            link = repository.get_search_run_batch_link(run_id)
            if (link is None) != (state["batch_id"] is None) or link is not None and link["batch_id"] != state["batch_id"]:
                raise ValueError
            if state["batch_id"] is not None:
                batch = repository.get_preparation_batch(state["batch_id"])
                expected_batch = str(uuid5(NAMESPACE_URL, "grounded-apply.batch@1/" + hash_bytes(("search-batch." + run_id).encode())))
                if (state["batch_id"] != expected_batch or batch is None
                    or json.loads(batch["manifest_json"]) != self._batch_manifest(manifest, state["selected_job_ids"])):
                    raise ValueError
                if state["phase"] == "complete":
                    _, items, _ = BatchService(repository, MaterialService(repository, self._renderer))._validated(state["batch_id"])
                    if any(item_state["stage"] in {"queued", "building"} for _, _, item_state in items):
                        raise ValueError
        except (ValueError, KeyError, TypeError):
            raise SearchIntegrityError("Search run failed integrity checks") from None
        if check_windows:
            for index, source in enumerate(state["sources"]):
                if "window" in source:
                    self._window_ledger(repository, run["search_id"], manifest, index)
        if check_rotation and "source_order" in state:
            self._rotation_ledger(repository, run["search_id"], manifest)
        return run, manifest, state

    def _rotation_ledger(self, repository: SQLiteRepository, search_id: str, manifest: dict[str, Any]) -> tuple[SourceRotation, ...]:
        reservations = []
        for prior in repository.list_search_runs(search_id):
            _, policy, state = self._validated(repository, prior["id"], check_windows=False, check_rotation=False)
            if policy != manifest:
                raise SearchIntegrityError("Source ordering belongs to another search policy")
            if "source_order" in state:
                reservations.append(validate_source_rotation(state["source_order"]))
        try:
            return validate_rotation_ledger(tuple(reservations), lease_epoch=self._lease(repository, search_id)["epoch"])
        except ValueError:
            raise SearchIntegrityError("Source ordering generation custody is invalid") from None

    def _reserve_source_order(self, repository: SQLiteRepository, run: Record, manifest: dict[str, Any],
                              state: dict[str, Any], owner: str, epoch: int) -> None:
        # Legacy runs that already started retain their original ordering. An
        # untouched run reserves exactly once after its first successful lease.
        if not _pristine(state, manifest):
            return
        self._assert_lease(repository, run["search_id"], run["id"], owner, epoch)
        reservations = self._rotation_ledger(repository, run["search_id"], manifest)
        state["source_order"] = SourceRotation(len(reservations) + 1, epoch).to_dict()
        self._append(repository, run["id"], manifest, "source_order_reserved", state)
        self._assert_lease(repository, run["search_id"], run["id"], owner, epoch)

    def _window_ledger(self, repository: SQLiteRepository, search_id: str, manifest: dict[str, Any], index: int) -> list[tuple[str, dict[str, Any]]]:
        """Validate scope, generation and lease-ordered custody across immutable runs."""
        records = []
        for prior in repository.list_search_runs(search_id):
            _, policy, state = self._validated(repository, prior["id"], check_windows=False, check_rotation=False)
            if policy != manifest:
                raise SearchIntegrityError("Netflix window belongs to another search policy")
            window = state["sources"][index].get("window")
            if window is not None:
                records.append((prior["id"], window))
        records.sort(key=lambda entry: entry[1]["generation"])
        lease = self._lease(repository, search_id)
        previous_epoch = 0
        for generation, (run_id, window) in enumerate(records, 1):
            if (window["generation"] != generation or not previous_epoch < window["reserved_epoch"] <= lease["epoch"]
                or window["completed_epoch"] is not None and window["completed_epoch"] > lease["epoch"]):
                raise SearchIntegrityError("Netflix window generation custody is invalid")
            candidates = [(identifier, value) for identifier, value in records[:generation - 1]
                if value["completed_epoch"] is not None and value["completed_epoch"] < window["reserved_epoch"]]
            base = candidates[-1] if candidates else None
            expected = (None, None, None, None) if base is None else (
                base[0], base[1]["generation"], digest(base[1]["progress"]), base[1]["progress"]["next"])
            if (window["base_run_id"], window["base_generation"], window["base_progress_sha256"], window["after"]) != expected:
                raise SearchIntegrityError("Netflix window input does not match its committed base")
            previous_epoch = window["reserved_epoch"]
        return records

    def _start_source(self, run_id: str, index: int, owner: str, epoch: int) -> dict[str, Any]:
        with self._open(False) as repository, repository.transaction():
            run, manifest, state = self._validated(repository, run_id)
            self._assert_lease(repository, run["search_id"], run_id, owner, epoch)
            source = state["sources"][index]
            if manifest["sources"][index]["provider"] == "netflix" and "window" not in source:
                records = self._window_ledger(repository, run["search_id"], manifest, index)
                completed = [(identifier, value) for identifier, value in records if value["progress"] is not None]
                base = completed[-1] if completed else None
                source["window"] = {"schema_version": 1, "policy": "netflix_head1_tail6@1",
                    "generation": len(records) + 1, "reserved_epoch": epoch,
                    "base_run_id": None if base is None else base[0],
                    "base_generation": None if base is None else base[1]["generation"],
                    "base_progress_sha256": None if base is None else digest(base[1]["progress"]),
                    "after": None if base is None else base[1]["progress"]["next"],
                    "completed_epoch": None, "progress": None}
            source["stage"] = "fetching"
            source["attempts"] += 1
            state["inflight_bytes"] = 0
            self._append(repository, run_id, manifest, "source_started", state)
            self._assert_lease(repository, run["search_id"], run_id, owner, epoch)
            self._validated(repository, run_id)
            return source

    def _append(self, repository: SQLiteRepository, run_id: str, manifest: dict[str, Any], action: str,
                state: dict[str, Any], *, at: str | None = None, reserve: bool = True) -> None:
        self._state(state, manifest)
        events = repository.list_search_events(run_id)
        moment = self._now() if at is None else at
        if events and moment < events[-1]["at"]:
            raise SearchIntegrityError("Search clock moved backwards")
        previous = None if not events else events[-1]["event_sha256"]
        position = len(events)
        repository.insert_search_event(event_id=str(uuid5(NAMESPACE_URL, f"{run_id}/event/{position}")),
            run_id=run_id, position=position, at=moment, action=action, state=state, previous_sha256=previous,
            event_sha256=digest(_event(run_id, position, moment, action, state, previous)))
        self._history(repository, run_id, manifest)
        self._capacity(repository, reserve=reserve)

    def _change(self, run_id: str, owner: str, epoch: int, action: str,
                change: Callable[[dict[str, Any], dict[str, Any]], None], *, reserve: bool = True) -> dict[str, Any]:
        with self._open(False) as repository, repository.transaction():
            run, manifest, state = self._validated(repository, run_id)
            self._assert_lease(repository, run["search_id"], run_id, owner, epoch)
            change(state, manifest)
            self._append(repository, run_id, manifest, action, state, reserve=reserve)
            self._assert_lease(repository, run["search_id"], run_id, owner, epoch)
            return state

    def run(self, search_id: str, *, idempotency_key: str, max_items: int = 20, max_seconds: int = 900) -> dict[str, Any]:
        self._budgets(max_items, max_seconds)
        opaque(idempotency_key)
        with self._open(False) as repository, repository.transaction():
            if self._parent_guard is not None:
                self._parent_guard(repository)
            _, manifest = self._scope(repository, search_id)
            payload = request_input("search-run." + digest({"search_id": search_id, "key": idempotency_key}),
                {"search_id": search_id, "manifest_sha256": digest(manifest)})
            run_id = str(uuid5(NAMESPACE_URL, f"grounded-apply.search-run@1/{search_id}/{payload['idempotency_sha256']}"))
            existing = existing_workflow(repository, "search_run_create", payload)
            if existing is None:
                at = self._now()
                workflow = start_workflow(repository, "search_run_create", payload, at)
                repository.insert_search_run(run_id=run_id, search_id=search_id, created_at=at, workflow_run_id=workflow["id"])
                self._append(repository, run_id, manifest, "created", _initial(manifest), at=at)
                finish_workflow(repository, workflow["id"], [run_id], at)
            if self._bind_run is not None:
                self._bind_run(repository, run_id)
            self._validated(repository, run_id)
            if self._parent_guard is not None:
                self._parent_guard(repository)
        return self.resume(run_id, max_items=max_items, max_seconds=max_seconds)

    @staticmethod
    def _budgets(max_items: int, max_seconds: int) -> None:
        if type(max_items) is not int or not 1 <= max_items <= 50 or type(max_seconds) is not int or not 1 <= max_seconds <= 3600:
            raise ValueError("Search invocation budgets require one to fifty items and one to three thousand six hundred seconds")

    @staticmethod
    def _batch_manifest(manifest: dict[str, Any], job_ids: list[str]) -> dict[str, Any]:
        return validate_batch_manifest({"schema_version": 1,
            **{key: manifest[key] for key in ("claim_ids", "layout", "questions", "questionnaire_coverage")},
            "jobs": [{"job_id": job_id} for job_id in job_ids]})

    def _policy(self, repository: SQLiteRepository, manifest: dict[str, Any]) -> SearchEligibilityPolicy:
        return SearchEligibilityPolicy(repository, MaterialService(repository, self._renderer),
            excluded_identities=validate_excluded_identities(manifest["excluded_identities"]))

    def _preparation_history(self, repository: SQLiteRepository, search_id: str, current_run: str,
                             candidate_versions: frozenset[tuple[str, ...]]) -> tuple[set[tuple[str, ...]], dict[tuple[str, ...], int]]:
        """Count actual attempts for exact posting versions in this saved scope.

        Current draft validation still controls reuse. Historical attempt counts
        only distribute a bounded preparation opportunity; they never infer fit
        or suppress a blocker permanently.
        """
        handled: set[tuple[str, ...]] = set()
        attempts: dict[tuple[str, ...], int] = {}
        _, manifest = self._scope(repository, search_id)
        self._rotation_ledger(repository, search_id, manifest)
        for prior in repository.list_search_runs(search_id):
            if prior["id"] == current_run:
                continue
            _, _, state = self._validated(repository, prior["id"], check_rotation=False)
            if state["batch_id"] is None:
                continue
            versions = {}
            for job_id in state["selected_job_ids"]:
                job = JobService(repository).get(job_id)
                assert job.discovery is not None
                versions[job_id] = tuple(job.discovery[key] for key in ("provider", "board", "external_id", "content_sha256"))
            current_job_ids = frozenset(job_id for job_id, identity in versions.items() if identity in candidate_versions)
            history = BatchService(repository, MaterialService(repository, self._renderer)).search_history(
                state["batch_id"], current_job_ids=current_job_ids)
            for item in history:
                identity = versions[item.job_id]
                attempts[identity] = attempts.get(identity, 0) + item.attempts
                if item.reusable:
                    handled.add(identity)
        return handled, attempts

    def _handled(self, repository: SQLiteRepository, search_id: str, current_run: str,
                 candidate_versions: frozenset[tuple[str, ...]]) -> set[tuple[str, ...]]:
        return self._preparation_history(repository, search_id, current_run, candidate_versions)[0]

    def _capture_source(self, run_id: str, index: int, discovered: Any, owner: str, epoch: int, *, progress: object = None) -> None:
        with self._open(False) as repository, repository.transaction():
            if repository.get_scheduled_search_link(run_id) is not None and self._parent_guard is None:
                raise ValueError("Resume this search run through its daily schedule")
            if self._parent_guard is not None:
                self._parent_guard(repository)
            run, manifest, state = self._validated(repository, run_id)
            self._assert_lease(repository, run["search_id"], run_id, owner, epoch)
            source = state["sources"][index]
            policy = self._policy(repository, manifest).snapshot()
            used = sum(len(entry["job_ids"]) for entry in state["sources"])
            remaining_sources = sum(entry["stage"] in {"pending", "fetching"} and spec["provider"] != "manual"
                for entry, spec in zip(state["sources"], manifest["sources"], strict=True))
            remaining = _candidate_limit(manifest) - used
            quota = min(manifest["max_jobs"], (remaining + max(1, remaining_sources) - 1) // max(1, remaining_sources))
            filters = _preparation_filters(manifest)
            candidates = []
            for job in discovered.jobs:
                filter_reason = None if filters is None else filters.assess(title=job.title, location=job.location)
                if filter_reason is not None:
                    source["skipped"][filter_reason] += 1
                    continue
                decision = policy.assess_discovered(job)
                if not decision.eligible:
                    source["skipped"][decision.reason] += 1
                else:
                    candidates.append(job)
            candidate_versions = frozenset((job.provider, job.board, job.external_id, job.content_sha256) for job in candidates)
            handled, prior_attempts = self._preparation_history(repository, run["search_id"], run_id, candidate_versions)
            # Every historical artifact remains audited, while current evidence
            # is resolved only for incoming, eligible exact posting versions.
            candidates.sort(key=lambda job: prior_attempts.get((job.provider, job.board, job.external_id, job.content_sha256), 0))
            for job in candidates:
                if (job.provider, job.board, job.external_id, job.content_sha256) in handled:
                    source["skipped"]["unchanged_draft"] += 1
                elif len(source["job_ids"]) >= quota:
                    source["skipped"]["deferred"] += 1
                else:
                    try:
                        captured = JobService(repository).capture_discovered(job)
                    except DiscoveryRecordError:
                        source["skipped"]["invalid_record"] += 1
                        continue
                    source["job_ids"].append(captured["job_id"])
            source["stage"] = "complete"
            source["report"] = json.loads(canonical(asdict(discovered.sources[0])))
            if "window" in source:
                source["window"] = {**source["window"], "progress": progress, "completed_epoch": epoch}
            elif progress is not None:
                raise SearchIntegrityError("Unexpected source window progress")
            self._assert_lease(repository, run["search_id"], run_id, owner, epoch)
            self._append(repository, run_id, manifest, "source_completed", state)
            self._assert_lease(repository, run["search_id"], run_id, owner, epoch)

    def _freeze(self, run_id: str, owner: str, epoch: int) -> None:
        with self._open(False) as repository, repository.transaction():
            run, manifest, state = self._validated(repository, run_id)
            self._assert_lease(repository, run["search_id"], run_id, owner, epoch)
            policy = self._policy(repository, manifest).snapshot()
            ordered = _round_robin([state["sources"][index] for index in _source_indices(state, manifest)])
            jobs, decisions = {}, {}
            for identifier in ordered:
                job = JobService(repository).get(identifier)
                _require_preparation_match(manifest, job)
                jobs[identifier], decisions[identifier] = job, policy.assess(job)
            candidate_versions = frozenset(tuple(job.discovery[key] for key in ("provider", "board", "external_id", "content_sha256"))
                for identifier, job in jobs.items() if decisions[identifier].eligible)
            handled = self._handled(repository, run["search_id"], run_id, candidate_versions)
            selected = []
            for identifier in ordered:
                job, decision = jobs[identifier], decisions[identifier]
                assert job.discovery is not None
                identity = tuple(job.discovery[key] for key in ("provider", "board", "external_id", "content_sha256"))
                reason = decision.reason if not decision.eligible else "unchanged_draft" if identity in handled else "preparation_limit" if len(selected) >= manifest["max_jobs"] else None
                if reason is None:
                    selected.append(identifier)
                else:
                    state["selection_skipped"].append({"job_id": identifier, "reason": reason})
            state["selected_job_ids"] = selected
            state["phase"] = "preparing" if selected else "complete"
            self._append(repository, run_id, manifest, "selection_frozen", state)
            self._assert_lease(repository, run["search_id"], run_id, owner, epoch)

    def _child(self, run_id: str, owner: str, epoch: int) -> str:
        with self._open(False) as repository, repository.transaction():
            run, manifest, state = self._validated(repository, run_id)
            self._assert_lease(repository, run["search_id"], run_id, owner, epoch)
            if state["batch_id"] is None:
                created = BatchService(repository, MaterialService(repository, self._renderer),
                    clock=self._clock, monotonic=self._monotonic).create(
                    self._batch_manifest(manifest, state["selected_job_ids"]), idempotency_key="search-batch." + run_id)
                state["batch_id"] = created["batch_id"]
                repository.insert_search_batch_link(state["batch_id"], run_id)
                self._append(repository, run_id, manifest, "child_created", state)
            self._assert_lease(repository, run["search_id"], run_id, owner, epoch)
            return state["batch_id"]

    def resume(self, run_id: str, *, max_items: int = 20, max_seconds: int = 900) -> dict[str, Any]:
        self._budgets(max_items, max_seconds)
        owner, deadline = str(uuid4()), self._monotonic() + max_seconds
        with self._open(False) as repository, repository.transaction():
            if repository.get_scheduled_search_link(run_id) is not None and self._parent_guard is None:
                raise ValueError("Resume this search run through its daily schedule")
            if self._parent_guard is not None:
                self._parent_guard(repository)
            run, manifest, state = self._validated(repository, run_id)
            lease = self._lease(repository, run["search_id"])
            now = self._now()
            epoch = repository.acquire_search_lease(run["search_id"], run_id=run_id, expected_epoch=lease["epoch"],
                owner=owner, now=now, expires_at=_at(datetime.fromisoformat(now) + timedelta(seconds=max_seconds + 180)))
            if epoch is None:
                raise SearchLeaseActiveError(run_id)
            self._capacity(repository)
            durable_source_gap = state["stop_reason"] in {"request_budget", "byte_budget"} and any(
                source["stage"] == "deferred" for source in state["sources"])
            if state["stop_reason"] is not None and not durable_source_gap:
                state["stop_reason"] = None
                self._append(repository, run_id, manifest, "resumed", state, reserve=False)
            self._reserve_source_order(repository, run, manifest, state, owner, epoch)
            self._assert_lease(repository, run["search_id"], run_id, owner, epoch)
        stop_reason = None
        try:
            if state["phase"] == "discovering":
                for index in _source_indices(state, manifest):
                    spec = manifest["sources"][index]
                    with self._open(True) as repository:
                        _, _, state = self._validated(repository, run_id)
                    source = state["sources"][index]
                    if source["stage"] in {"complete", "deferred"}:
                        continue
                    if self._monotonic() >= deadline:
                        raise _Pause("time_budget")
                    budget = "request_budget" if state["requests_used"] >= manifest["max_requests"] else "byte_budget" if state["bytes_charged"] >= manifest["max_bytes"] else None
                    if source["attempts"] >= 3 or budget is not None and spec["provider"] != "manual":
                        def deferred(value, policy):
                            value["sources"][index]["stage"] = "deferred"
                            value["inflight_bytes"] = 0
                            value["stop_reason"] = budget
                        self._change(run_id, owner, epoch, "source_deferred", deferred)
                        stop_reason = budget or stop_reason
                        continue
                    source = self._start_source(run_id, index, owner, epoch)
                    transport = _AccountedTransport(self, run_id, owner, epoch, deadline)
                    progress = None
                    try:
                        discovery = DiscoveryService(transport)
                        if "window" in source:
                            from grounded_apply.services.source_windows import validate_netflix_cursor
                            result = discovery.discover_netflix_window(SourceSpec(**spec),
                                after=validate_netflix_cursor(source["window"]["after"]),
                                title_contains=tuple(manifest["title_contains"]), limit_per_source=1000)
                            discovered, progress = result.report, result.progress.to_dict()
                        else:
                            discovered = discovery.discover((SourceSpec(**spec),),
                                title_contains=tuple(manifest["title_contains"]), limit_per_source=1000)
                    except _NetworkAbort as failure:
                        raise failure.cause
                    self._capture_source(run_id, index, discovered, owner, epoch, progress=progress)
                self._freeze(run_id, owner, epoch)
            with self._open(True) as repository:
                run, manifest, state = self._validated(repository, run_id)
            if state["selected_job_ids"]:
                if self._monotonic() >= deadline:
                    raise _Pause("time_budget")
                batch_id = self._child(run_id, owner, epoch)
                with self._open(False) as repository:
                    def parent_guard():
                        self._assert_lease(repository, run["search_id"], run_id, owner, epoch)
                        self._capacity(repository)
                    def eligibility_guard(job_id):
                        _require_preparation_match(manifest, JobService(repository).get(job_id))
                        decision = self._policy(repository, manifest).assess(job_id)
                        if not decision.eligible:
                            raise MaterialBlocked([{"kind": "need_info", "reason": decision.reason,
                                "intent": "search_eligibility", "question": "This posting is excluded or already applied. No new preparation is authorized by this search."}])
                    child = BatchService(repository, MaterialService(repository, self._renderer), clock=self._clock,
                        monotonic=self._monotonic, parent_guard=parent_guard, eligibility_guard=eligibility_guard).run(
                            batch_id, max_items=max_items, max_seconds=max(1, min(3600, int(deadline - self._monotonic()))))
                stop_reason = child["stop_reason"] or state["stop_reason"]
                def child_progress(value, policy):
                    value["phase"] = "complete" if child["remaining_count"] == 0 else "preparing"
                    value["stop_reason"] = stop_reason
                self._change(run_id, owner, epoch, "child_progress", child_progress, reserve=False)
        except _Pause as pause:
            stop_reason = pause.reason
        except (StorageCapacityError, DiscoveryCapacityError):
            stop_reason = "capacity_reached"
        except Exception as error:
            stop_reason = "shared_failure"
            raise SearchExecutionError(run_id) from error
        finally:
            with self._open(False) as repository, repository.transaction():
                lease = self._lease(repository, run["search_id"])
                if lease["owner"] == owner and lease["epoch"] == epoch and lease["run_id"] == run_id:
                    if stop_reason is not None and lease["expires_at"] > self._now():
                        authorized = True
                        if self._parent_guard is not None:
                            try:
                                self._parent_guard(repository)
                            except StorageCapacityError:
                                # The parent already checked its owner, epoch
                                # and clock before enforcing its work reserve.
                                # Our own live lease was checked above; the
                                # bounded final status still uses reserve=False.
                                pass
                            except Exception:
                                authorized = False
                        if authorized:
                            _, policy, final = self._validated(repository, run_id)
                            final["stop_reason"] = stop_reason
                            self._append(repository, run_id, policy, "stopped", final, reserve=False)
                    repository.release_search_lease(run["search_id"], run_id=run_id, owner=owner, epoch=epoch)
                    self._capacity(repository, reserve=False)
        return self.get(run_id)

    def get(self, run_id: str) -> dict[str, Any]:
        with self._open(True) as repository, repository.read_transaction():
            return self._view(repository, run_id)

    def export_snapshot(self, run_id: str) -> SearchReviewSnapshot:
        """Read current reviewable bundles without approving or changing a run.

        All history and current facts are checked in one database snapshot before
        any value reaches a filesystem adapter. Stale bundles remain visible as
        blockers in the index, but their bytes are never included. A current
        partial bundle is useful for review and retains its unanswered questions.
        """
        with self._open(True) as repository, repository.read_transaction():
            report = self._view(repository, run_id)
            _, manifest = self._scope(repository, report["search_id"])
            selection = report["selection"]
            if len(selection) > 50 or len({item["job_id"] for item in selection}) != len(selection):
                raise SearchIntegrityError("Search review selection exceeds its bound")
            by_job = {item["job_id"]: item for item in report["items"]}
            if len(by_job) != len(report["items"]) or set(by_job) - {item["job_id"] for item in selection}:
                raise SearchIntegrityError("Search review items do not match its selection")
            items, bundles = [], []
            materials = MaterialService(repository, self._renderer)
            for selected in selection:
                job = JobService(repository).get(selected["job_id"])
                item = dict(by_job[job.id]) if job.id in by_job else {
                    "item_id": None, "job_id": job.id, "status": "not_prepared", "attempts": 0,
                    "material_id": None, "bundle_sha256": None, "blockers": [], "currently_valid": False,
                    "questionnaire_coverage": manifest["questionnaire_coverage"],
                    "requires_approval": True, "material_approved": False, "visual_review_required": True,
                }
                item.update(source_id=selected["source_id"], title=job.discovery["title"],
                    source_url=job.source_url, location=job.discovery["location"], exported=False,
                    export_status="not_prepared")
                if item["material_id"] is not None:
                    if not item["currently_valid"]:
                        if repository.get_material_approval(item["material_id"]) is not None:
                            # An ordinary stale-fact blocker must not conceal a
                            # damaged approval audit on the historical bundle.
                            materials.is_approved(item["material_id"], require_current=False)
                        item["export_status"] = "stale_omitted"
                    else:
                        material = materials.get(item["material_id"])
                        if (material["id"] != item["material_id"] or material["job_id"] != job.id
                            or material["bundle_sha256"] != item["bundle_sha256"]):
                            raise SearchIntegrityError("Search review material does not match its item")
                        metadata = {key: material[key] for key in ("id", "job_id", "bundle_sha256",
                            "created_at", "workflow_run_id", "latex_text", "extracted_text", "structure", "manifest", "validation")}
                        bundles.append(ReviewMaterial(job.id, canonical(metadata), material["pdf_bytes"]))
                        item["exported"] = True
                        item["export_status"] = "current_partial" if item["status"] == "blocked" else "current_draft"
                items.append(item)
            # Keep source coverage and operational limitations, without copying
            # the nested batch a second time or volatile lease observations.
            index = {key: value for key, value in report.items() if key not in {"batch", "items", "lease_active"}}
            index["status"] = ("failed" if report["stop_reason"] == "shared_failure" else
                "incomplete" if report["remaining_count"] else "waiting_for_input" if report["counts"]["blocked"] else
                "completed" if report["coverage_complete"] else "completed_with_gaps")
            index.update(items=items, exported_material_count=len(bundles),
                omitted_material_count=sum(item["export_status"] == "stale_omitted" for item in items),
                review_snapshot=True, current_facts_checked=True,
                approvals_recorded=False, external_action_taken=False, application_ready=False)
            index["limitations"] = [*index["limitations"],
                "This private copy reflects one validated read snapshot; later fact or approval changes are not synchronized.",
                "Review status describes durable work and current facts; it does not indicate whether a worker is active.",
                "Current partial bundles retain unresolved questions; stale bundles are listed without exported files.",
                "Exporting does not approve a bundle or complete an application."]
            return SearchReviewSnapshot(run_id, canonical(index), tuple(bundles))

    def _view(self, repository: SQLiteRepository, run_id: str) -> dict[str, Any]:
        """Validate a report in the caller's existing snapshot, without reopening runtime."""
        run, manifest, state = self._validated(repository, run_id)
        lease = self._lease(repository, run["search_id"])
        child = None if state["batch_id"] is None else BatchService(repository, MaterialService(repository, self._renderer)).get(state["batch_id"])
        counts = {"total": 0, "queued": 0, "building": 0, "draft": 0, "blocked": 0} if child is None else child["counts"]
        source_remaining = sum(source["stage"] in {"pending", "fetching"} for source in state["sources"])
        remaining = source_remaining + (len(state["selected_job_ids"] or []) if child is None else child["remaining_count"])
        coverage = all(source["stage"] == "complete" and source["report"]["status"] == "successful"
            and source["skipped"]["invalid_record"] == 0 for source in state["sources"])
        active = lease["owner"] is not None and lease["run_id"] == run_id and lease["expires_at"] > self._now()
        status = "running" if active else "failed" if state["stop_reason"] == "shared_failure" else "budget_exhausted" if remaining else "waiting_for_input" if counts["blocked"] else "completed" if coverage else "completed_with_gaps"
        if state["phase"] == "discovering" and state["stop_reason"] is None and not active:
            status = "pending"
        selection = []
        by_job = {job_id: source["source_id"] for source in state["sources"] for job_id in source["job_ids"]}
        for job_id in state["selected_job_ids"] or []:
            job = JobService(repository).get(job_id)
            item = {"job_id": job_id, "source_id": by_job[job_id], "title": job.discovery["title"], "source_url": job.source_url}
            if manifest["schema_version"] == 2:
                item["location"] = job.discovery["location"]
            selection.append(item)
        skipped = {reason: sum(source["skipped"][reason] for source in state["sources"]) for reason in sorted(_skip_reasons(manifest))}
        for item in state["selection_skipped"]:
            skipped[item["reason"]] = skipped.get(item["reason"], 0) + 1
        result = {"schema_version": 1, "run_id": run_id, "search_id": run["search_id"], "batch_id": state["batch_id"],
            "created_at": run["created_at"], "status": status, "phase": state["phase"], "stop_reason": state["stop_reason"],
            "coverage_complete": coverage, "sources": [{**source, "captured_count": len(source["job_ids"])} for source in state["sources"]],
            "selection": selection, "skipped": skipped, "counts": counts, "remaining_count": remaining,
            "remaining_sources": source_remaining, "blockers_count": counts["blocked"],
            "grouped_blockers": [] if child is None else child["grouped_blockers"], "items": [] if child is None else child["items"], "batch": child,
            "requests_used": state["requests_used"], "bytes_charged": state["bytes_charged"], "request_limit": manifest["max_requests"],
            "byte_limit": manifest["max_bytes"], "candidate_limit": _candidate_limit(manifest), "lease_active": active,
            "external_action_taken": False, "approvals_recorded": False, "application_ready": False,
            "limitations": ["Title substring retrieval, least-attempted candidates within each source and source round-robin selection are not qualification ranking.",
                "New runs rotate automatic source priority; retries retain their reserved order and manual gaps follow automatic sources.",
                "Only up to 1000 filtered postings per source and a bounded candidate window are inspected for preparation.",
                "Netflix advances bounded detail windows within this saved search; unread source entries remain coverage gaps.",
                "No application form completion, approvals, submissions or daily schedule is implied."]}
        if "source_order" in state:
            result["source_order"] = state["source_order"]
            result["source_priority"] = [manifest["sources"][index]["id"] for index in _source_indices(state, manifest)]
        return result

    def list(self, search_id: str | None = None) -> tuple[dict[str, Any], ...]:
        with self._open(True) as repository:
            if search_id is not None:
                self._scope(repository, search_id)
            ids = [record["id"] for record in repository.list_search_runs(search_id)]
        return tuple(self.get(identifier) for identifier in ids)

    def list_searches(self) -> tuple[dict[str, Any], ...]:
        with self._open(True) as repository, repository.read_transaction():
            results = []
            for record in repository.list_saved_searches():
                scope, manifest = self._scope(repository, record["id"])
                results.append({"search_id": scope["id"], "created_at": scope["created_at"], "manifest": manifest})
            return tuple(results)


class _AccountedTransport:
    def __init__(self, service: SearchService, run_id: str, owner: str, epoch: int, deadline: float) -> None:
        self.service, self.run_id, self.owner, self.epoch, self.deadline = service, run_id, owner, epoch, deadline

    def get(self, url: str, *, max_bytes: int, timeout: float) -> bytes:
        if self.service._monotonic() >= self.deadline:
            raise _NetworkAbort(_Pause("time_budget"))
        def reserve(state, manifest):
            if state["requests_used"] >= manifest["max_requests"] or state["bytes_charged"] >= manifest["max_bytes"]:
                raise _NetworkBudget()
            amount = min(max_bytes, MAX_RESPONSE_BYTES, manifest["max_bytes"] - state["bytes_charged"])
            state["requests_used"] += 1
            state["bytes_charged"] += amount
            state["inflight_bytes"] = amount
        try:
            state = self.service._change(self.run_id, self.owner, self.epoch, "request_reserved", reserve)
        except _NetworkBudget:
            raise
        except SearchCapacityError:
            raise _NetworkAbort(_Pause("capacity_reached")) from None
        except Exception as error:
            raise _NetworkAbort(error) from None
        maximum = state["inflight_bytes"]
        try:
            # This call deliberately sits outside every runtime context manager.
            result = self.service._transport.get(url, max_bytes=maximum,
                timeout=min(timeout, max(0.001, self.deadline - self.service._monotonic())))
        except Exception:
            self._settle(maximum)
            raise
        if type(result) is bytes and len(result) <= maximum:
            self._settle(len(result))
        else:
            self._settle(maximum)
            if type(result) is bytes:
                raise _NetworkResponseTooLarge()
        return result

    def _settle(self, charged: int) -> None:
        def settle(state, manifest):
            state["bytes_charged"] -= state["inflight_bytes"] - charged
            state["inflight_bytes"] = 0
        try:
            self.service._change(self.run_id, self.owner, self.epoch, "request_settled", settle)
        except SearchCapacityError:
            raise _NetworkAbort(_Pause("capacity_reached")) from None
        except Exception as error:
            raise _NetworkAbort(error) from None
