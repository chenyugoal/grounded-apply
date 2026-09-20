"""Pure, bounded notification changes from validated saved-search reports.

Callers must obtain reports through SearchService.get before calling this helper.
This module performs no persistence or delivery. Summaries deliberately exclude
job text, URLs, candidate facts, question text, run IDs, and timestamps. Absence
from a later bounded search is not evidence that a posting or blocker resolved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from grounded_apply.services.discovery import DiscoveryErrorCode, MAX_SOURCES
from grounded_apply.services.jobs import validate_job_input
from grounded_apply.services.workflow import digest, opaque


MAX_NOTIFICATION_MATERIALS = 1000
MAX_NOTIFICATION_POSTINGS = 1000
MAX_NOTIFICATION_BLOCKERS = 1000
_SOURCE_STATUSES = {"successful", "partial", "failed", "manual_required", "deferred"}
_ERRORS = {error.value for error in DiscoveryErrorCode}
_STOPS = {None, "item_budget", "time_budget", "request_budget", "byte_budget", "capacity_reached", "shared_failure"}
_HEALTH = {"healthy", "budget_exhausted", "capacity_reached", "failed", "retry_limit"}
_SUMMARY_FIELDS = {"schema_version", "search_id", "seen_materials", "postings", "sources", "run_health"}


class NotificationCapacityError(ValueError):
    """The cumulative notification state must not be silently truncated."""


@dataclass(frozen=True, slots=True)
class NotificationUpdate:
    summary: dict[str, Any]
    delta: dict[str, Any]
    notify: bool


def _hash(value: object) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("Notification hash is invalid")
    return value


def _list(value: object, maximum: int) -> list[Any]:
    if type(value) is not list:
        raise ValueError("Notification list is invalid")
    if len(value) > maximum:
        raise NotificationCapacityError("Notification summary capacity reached")
    return value


def _shape(value: object, fields: set[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise ValueError("Notification fields are invalid")
    return value


def _source(value: object) -> dict[str, Any]:
    item = _shape(value, {"source_id", "status", "errors"})
    opaque(item["source_id"])
    if type(item["status"]) is not str or item["status"] not in _SOURCE_STATUSES:
        raise ValueError("Notification source status is invalid")
    errors = _list(item["errors"], len(_ERRORS))
    if any(type(error) is not str or error not in _ERRORS for error in errors) or len(set(errors)) != len(errors):
        raise ValueError("Notification source errors are invalid")
    if (item["status"] in {"partial", "failed"}) is not bool(errors):
        raise ValueError("Notification source health is inconsistent")
    return {"source_id": item["source_id"], "status": item["status"], "errors": sorted(errors)}


def _health(value: object) -> dict[str, Any]:
    item = _shape(value, {"status", "stop_reason", "coverage_complete"})
    if (type(item["status"]) is not str or item["status"] not in _HEALTH
        or item["stop_reason"] is not None and type(item["stop_reason"]) is not str
        or item["stop_reason"] not in _STOPS | {"retry_limit"}
        or item["coverage_complete"] is not None and type(item["coverage_complete"]) is not bool):
        raise ValueError("Notification run health is invalid")
    expected = "retry_limit" if item["stop_reason"] == "retry_limit" else "failed" if item["stop_reason"] == "shared_failure" else "capacity_reached" if item["stop_reason"] == "capacity_reached" else "budget_exhausted" if item["stop_reason"] is not None else None
    if expected is not None and item["status"] != expected or item["status"] in {"failed", "capacity_reached", "retry_limit"} and expected != item["status"]:
        raise ValueError("Notification run health is inconsistent")
    return dict(item)


def validate_notification_summary(value: object) -> dict[str, Any]:
    """Validate a closed persisted summary and return an independent copy."""
    item = _shape(value, _SUMMARY_FIELDS)
    if type(item["schema_version"]) is not int or item["schema_version"] != 1:
        raise ValueError("Notification summary version is invalid")
    opaque(item["search_id"])
    materials: dict[str, str] = {}
    for entry in _list(item["seen_materials"], MAX_NOTIFICATION_MATERIALS):
        record = _shape(entry, {"material_id", "bundle_sha256"})
        identifier = opaque(record["material_id"])
        if identifier in materials:
            raise ValueError("Notification material identities are duplicated")
        materials[identifier] = _hash(record["bundle_sha256"])
    postings: dict[str, list[str]] = {}
    for entry in _list(item["postings"], MAX_NOTIFICATION_POSTINGS):
        record = _shape(entry, {"posting_sha256", "blocker_sha256s"})
        identifier = _hash(record["posting_sha256"])
        hashes = [_hash(part) for part in _list(record["blocker_sha256s"], MAX_NOTIFICATION_BLOCKERS)]
        if identifier in postings or len(set(hashes)) != len(hashes):
            raise ValueError("Notification blocker identities are duplicated")
        postings[identifier] = sorted(hashes)
    if sum(len(hashes) for hashes in postings.values()) > MAX_NOTIFICATION_BLOCKERS:
        raise NotificationCapacityError("Notification summary capacity reached")
    sources: dict[str, dict[str, Any]] = {}
    for entry in _list(item["sources"], MAX_SOURCES):
        source = _source(entry)
        if source["source_id"] in sources:
            raise ValueError("Notification source identities are duplicated")
        sources[source["source_id"]] = source
    return {"schema_version": 1, "search_id": item["search_id"],
        "seen_materials": [{"material_id": key, "bundle_sha256": materials[key]} for key in sorted(materials)],
        "postings": [{"posting_sha256": key, "blocker_sha256s": postings[key]} for key in sorted(postings)],
        "sources": [sources[key] for key in sorted(sources)], "run_health": _health(item["run_health"])}


def _empty(search_id: str) -> dict[str, Any]:
    return {"schema_version": 1, "search_id": search_id, "seen_materials": [], "postings": [], "sources": [],
        "run_health": {"status": "healthy", "stop_reason": None, "coverage_complete": None}}


def notification_delta(previous: object | None, current: object) -> dict[str, Any]:
    """Recompute a durable notice from adjacent closed, validated summaries."""
    after = validate_notification_summary(current)
    before = _empty(after["search_id"]) if previous is None else validate_notification_summary(previous)
    if before["search_id"] != after["search_id"]:
        raise ValueError("Notification summary belongs to another saved search")
    old_materials = {item["material_id"]: item for item in before["seen_materials"]}
    new_materials = {item["material_id"]: item for item in after["seen_materials"]}
    if any(new_materials.get(key) != value for key, value in old_materials.items()):
        raise ValueError("Cumulative notification materials cannot disappear or change")
    old_postings = {item["posting_sha256"]: set(item["blocker_sha256s"]) for item in before["postings"]}
    new_postings = {item["posting_sha256"]: set(item["blocker_sha256s"]) for item in after["postings"]}
    old_sources = {item["source_id"]: item for item in before["sources"]}
    new_sources = {item["source_id"]: item for item in after["sources"]}
    if set(old_postings) - set(new_postings) or set(old_sources) - set(new_sources):
        raise ValueError("Observed notification identities cannot disappear")
    added, resolved = [], []
    for posting, blockers in new_postings.items():
        old = old_postings.get(posting, set())
        added.extend({"posting_sha256": posting, "blocker_sha256": key} for key in sorted(blockers - old))
        resolved.extend({"posting_sha256": posting, "blocker_sha256": key} for key in sorted(old - blockers))
    source_changes = [{"source_id": key, "previous": old_sources.get(key), "current": value}
        for key, value in new_sources.items() if old_sources.get(key) != value
        and (key in old_sources or value["status"] != "successful")]
    old_health, health = before["run_health"], after["run_health"]
    if old_health["coverage_complete"] is not None and health["coverage_complete"] is None:
        raise ValueError("Observed coverage cannot become unknown")
    health_changed = (old_health["status"] != health["status"] or old_health["stop_reason"] != health["stop_reason"]
        or old_health["coverage_complete"] is not None and health["coverage_complete"] != old_health["coverage_complete"])
    return {"new_materials": [value for key, value in new_materials.items() if key not in old_materials],
        "new_blockers": added, "resolved_blockers": resolved, "source_changes": source_changes,
        "run_health_change": {"previous": old_health, "current": health} if health_changed else None}


def _blocker_hash(value: object) -> str:
    basic = {"kind", "reason", "intent", "question", "related_claim_ids"}
    context = {"question_id", "question_label", "question_sha256"}
    if type(value) is not dict or set(value) not in (basic, basic | context):
        raise ValueError("Notification blocker fields are invalid")
    if type(value["kind"]) is not str or value["kind"] not in {"need_info", "contradiction"}:
        raise ValueError("Notification blocker kind is invalid")
    for key in ("reason", "intent", "question"):
        if type(value[key]) is not str or not value[key] or len(value[key]) > 2048:
            raise ValueError("Notification blocker text is invalid")
    related = [opaque(part) for part in _list(value["related_claim_ids"], 160)]
    question_hash = None
    if context <= set(value):
        opaque(value["question_id"])
        if type(value["question_label"]) is not str or not value["question_label"] or len(value["question_label"]) > 256:
            raise ValueError("Notification question context is invalid")
        question_hash = _hash(value["question_sha256"])
    # Wording and per-run IDs never become retained notification state. Include
    # question content identity, where supplied, to distinguish actual questions.
    return digest({"kind": value["kind"], "reason": value["reason"], "intent": value["intent"],
        "related_claim_ids": sorted(set(related)), "question_sha256": question_hash})


def notification_update(report: dict[str, Any], previous: object | None = None) -> NotificationUpdate:
    """Derive changes from a SearchService-validated report, without delivery.

    An incomplete discovery phase retains the previous coverage conclusion;
    unknown initial coverage is None. Unfinished sources retain prior observed
    health, so starting or deferring a bounded run cannot imply an outage or
    recovery. Run health still records the budget and coverage gap.
    """
    if type(report) is not dict or report.get("schema_version") != 1 or type(report.get("schema_version")) is not int:
        raise ValueError("Validated search report is required")
    search_id = opaque(report.get("search_id"))
    if previous is None:
        prior = _empty(search_id)
    else:
        prior = validate_notification_summary(previous)
        if prior["search_id"] != search_id:
            raise ValueError("Notification summary belongs to another saved search")
    if (type(report.get("phase")) is not str or report["phase"] not in {"discovering", "preparing", "complete"}
        or type(report.get("status")) is not str or report["status"] not in {"pending", "running", "failed", "budget_exhausted", "waiting_for_input", "completed", "completed_with_gaps"}
        or type(report.get("coverage_complete")) is not bool
        or report.get("stop_reason") is not None and type(report["stop_reason"]) is not str
        or report.get("stop_reason") not in _STOPS):
        raise ValueError("Search report health is invalid")
    materials = {item["material_id"]: item["bundle_sha256"] for item in prior["seen_materials"]}
    postings = {item["posting_sha256"]: set(item["blocker_sha256s"]) for item in prior["postings"]}
    sources = {item["source_id"]: item for item in prior["sources"]}
    observed: dict[str, set[str]] = {}
    seen_jobs: set[str] = set()
    for item in _list(report.get("items"), 50):
        if type(item) is not dict:
            raise ValueError("Search report item is invalid")
        job_id = opaque(item.get("job_id"))
        if job_id in seen_jobs:
            raise ValueError("Search report jobs are duplicated")
        seen_jobs.add(job_id)
        validate_job_input(item.get("source_url"), "Notification posting identity")
        posting = digest({"source_url": item["source_url"]})
        if type(item.get("status")) is not str or item["status"] not in {"queued", "building", "draft", "blocked"} or type(item.get("currently_valid")) is not bool:
            raise ValueError("Search report item state is invalid")
        blockers = {_blocker_hash(blocker) for blocker in _list(item.get("blockers"), 200)}
        if bool(blockers) is not (item["status"] == "blocked"):
            raise ValueError("Search report blocker state is inconsistent")
        identifier, bundle = item.get("material_id"), item.get("bundle_sha256")
        if (identifier is None) is not (bundle is None):
            raise ValueError("Search report material identity is incomplete")
        if identifier is not None:
            opaque(identifier)
            _hash(bundle)
            if identifier in materials and materials[identifier] != bundle:
                raise ValueError("Immutable material bundle identity changed")
            if item["currently_valid"] and identifier not in materials:
                materials[identifier] = bundle
        elif item["currently_valid"] or item["status"] == "draft":
            raise ValueError("Search report material state is inconsistent")
        if item["status"] in {"draft", "blocked"}:
            # Two versions of the same exact posting may coexist. Union their
            # blockers so an unblocked version cannot hide another active gap.
            observed.setdefault(posting, set()).update(blockers)
    for posting, blockers in observed.items():
        postings[posting] = blockers
    seen_sources: set[str] = set()
    for entry in _list(report.get("sources"), MAX_SOURCES):
        if type(entry) is not dict:
            raise ValueError("Search report source is invalid")
        identifier = opaque(entry.get("source_id"))
        if identifier in seen_sources:
            raise ValueError("Search report source identities are duplicated")
        seen_sources.add(identifier)
        stage = entry.get("stage")
        if type(stage) is not str or stage not in {"pending", "fetching", "complete", "deferred"}:
            raise ValueError("Search report source stage is invalid")
        source_report = entry.get("report")
        if stage != "complete":
            if source_report is not None:
                raise ValueError("Unfinished source has a finished report")
            continue
        else:
            if type(source_report) is not dict or source_report.get("source_id") != identifier:
                raise ValueError("Search source report identity is invalid")
            current = _source({"source_id": identifier, "status": source_report.get("status"), "errors": source_report.get("errors")})
            skipped = entry.get("skipped")
            if type(skipped) is not dict or type(skipped.get("invalid_record")) is not int or skipped["invalid_record"] < 0:
                raise ValueError("Search source capture health is invalid")
            if skipped["invalid_record"]:
                current["status"] = "partial"
                current["errors"] = sorted(set(current["errors"]) | {"invalid_record"})
        sources[identifier] = current
    stop = report.get("stop_reason")
    status = "failed" if stop == "shared_failure" or report["status"] == "failed" else "capacity_reached" if stop == "capacity_reached" else "budget_exhausted" if stop is not None or report["status"] == "budget_exhausted" else "healthy"
    if status == "failed":
        stop = "shared_failure"
    if report["status"] in {"pending", "running"} and stop is None:
        # Merely starting another attempt is not evidence that a prior failure
        # or exhausted invocation recovered.
        status, stop = prior["run_health"]["status"], prior["run_health"]["stop_reason"]
    health = {"status": status, "stop_reason": stop,
        "coverage_complete": report["coverage_complete"] if report["phase"] != "discovering" else prior["run_health"]["coverage_complete"]}
    summary = validate_notification_summary({"schema_version": 1, "search_id": search_id,
        "seen_materials": [{"material_id": key, "bundle_sha256": value} for key, value in materials.items()],
        "postings": [{"posting_sha256": key, "blocker_sha256s": sorted(value)} for key, value in postings.items()],
        "sources": list(sources.values()), "run_health": health})
    delta = notification_delta(prior, summary)
    return NotificationUpdate(summary, delta, any(bool(value) for value in delta.values()))


def notification_failure(search_id: str, previous: object | None = None) -> NotificationUpdate:
    """Record a content-free failure when a full search report cannot validate.

    This preserves earlier observed identities without returning their artifacts
    as current search results. The caller owns recording the failure and delivery.
    """
    opaque(search_id)
    return notification_update({"schema_version": 1, "search_id": search_id, "phase": "discovering",
        "status": "failed", "stop_reason": "shared_failure", "coverage_complete": False,
        "sources": [], "items": []}, previous)


def notification_retry_limit(search_id: str, previous: object | None = None, *,
                             report: dict[str, Any] | None = None) -> NotificationUpdate:
    """Expose exhausted parent attempts while retaining observed child changes."""
    opaque(search_id)
    summary = (_empty(search_id) if previous is None else validate_notification_summary(previous))
    if summary["search_id"] != search_id:
        raise ValueError("Notification summary belongs to another saved search")
    if report is not None:
        summary = notification_update(report, summary).summary
    summary["run_health"] = {"status": "retry_limit", "stop_reason": "retry_limit",
        "coverage_complete": summary["run_health"]["coverage_complete"]}
    summary = validate_notification_summary(summary)
    delta = notification_delta(previous, summary)
    return NotificationUpdate(summary, delta, any(bool(value) for value in delta.values()))


def notification_capacity(search_id: str, previous: object | None = None, *,
                          report: dict[str, Any] | None = None) -> NotificationUpdate:
    """Record a storage stop, preserving only validated observed child changes."""
    opaque(search_id)
    summary = (_empty(search_id) if previous is None else validate_notification_summary(previous))
    if summary["search_id"] != search_id:
        raise ValueError("Notification summary belongs to another saved search")
    if report is not None:
        summary = notification_update(report, summary).summary
    summary["run_health"] = {"status": "capacity_reached", "stop_reason": "capacity_reached",
        "coverage_complete": summary["run_health"]["coverage_complete"]}
    summary = validate_notification_summary(summary)
    delta = notification_delta(previous, summary)
    return NotificationUpdate(summary, delta, any(bool(value) for value in delta.values()))
