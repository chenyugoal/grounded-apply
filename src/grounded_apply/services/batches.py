"""Durable, bounded draft preparation over immutable saved job versions.

Material generation owns factual validation. This service owns request identity,
per-item checkpoints and invocation leases; it never approves or submits.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from grounded_apply.domain import to_jsonable
from grounded_apply.repositories import Record, RepositoryError, SQLiteRepository
from grounded_apply.services.backup import MAX_SNAPSHOT_BYTES
from grounded_apply.services.storage_limits import StorageCapacityError
from grounded_apply.services.jobs import JobService
from grounded_apply.services.material_models import MaterialValidationError, validate_layout
from grounded_apply.services.materials import (
    MaterialBlocked, MaterialCapacityError, MaterialRenderError, MaterialService,
)
from grounded_apply.services.profile import ProfileService
from grounded_apply.services.questionnaires import QuestionnaireService, validate_question_specs
from grounded_apply.services.workflow import (
    canonical, digest, existing_workflow, finish_workflow, hash_bytes, opaque,
    request_input, start_workflow, validate_workflow,
)


MAX_BATCH_ITEMS = 50
MAX_ITEM_ATTEMPTS = 100
CHECKPOINT_RESERVE_BYTES = 256 * 1024
_STOP_REASONS = {None, "item_budget", "time_budget", "capacity_reached", "shared_failure"}
_STAGES = {"queued", "building", "draft", "blocked"}
_PREPARATION_FIELDS = {"claim_ids", "layout", "questions", "questionnaire_coverage"}
_STATE_FIELDS = {"stage", "attempts", "child_key", "fingerprint", "material_id", "bundle_sha256", "blockers"}


class BatchIntegrityError(RepositoryError):
    """A durable batch request, event chain, or artifact association is invalid."""


class BatchLeaseActiveError(ValueError):
    """Another invocation still owns this batch's bounded execution lease."""


class BatchLeaseLostError(RepositoryError):
    """An expired/replaced invocation cannot persist a parent checkpoint."""


class BatchCapacityError(StorageCapacityError):
    """A new request or checkpoint would exceed supported storage capacity."""


@dataclass(frozen=True, slots=True)
class BatchSearchHistoryItem:
    """Audited attempt history and candidate reuse, never application readiness."""

    job_id: str
    attempts: int
    reusable: bool


def _claim_ids(value: object) -> list[str]:
    if type(value) is not list or not 1 <= len(value) <= 80:
        raise ValueError("Batch preparation needs one to eighty ordered claim IDs")
    for identifier in value:
        opaque(identifier)
    if len(set(value)) != len(value):
        raise ValueError("Batch claim IDs must be distinct")
    return list(value)


def _preparation(value: dict[str, Any], defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = {} if defaults is None else dict(defaults)
    merged.update(value)
    claims = _claim_ids(merged.get("claim_ids"))
    layout = {"schema_version": 1, "presentations": validate_layout(merged.get("layout"), tuple(claims))}
    questions = list(validate_question_specs(merged.get("questions", [])))
    coverage = merged.get("questionnaire_coverage", "unknown")
    if type(coverage) is not str or coverage not in {"unknown", "provided"}:
        raise ValueError("Questionnaire coverage must be unknown or provided")
    return {"claim_ids": claims, "layout": layout, "questions": questions, "questionnaire_coverage": coverage}


def validate_batch_manifest(manifest: object) -> dict[str, Any]:
    """Validate/copy the closed request before any runtime or database access.

    Shared choices are resolved into each job. ``provided`` describes supplied
    question specifications; it never certifies a complete external form.
    """
    if (type(manifest) is not dict or not {"schema_version", "claim_ids", "jobs"} <= set(manifest)
        or set(manifest) - {"schema_version", "jobs"} - _PREPARATION_FIELDS
        or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1
        or type(manifest["jobs"]) is not list or not 1 <= len(manifest["jobs"]) <= MAX_BATCH_ITEMS):
        raise ValueError("Batch requires schema_version 1, shared claim_ids and one to fifty jobs")
    shared = _preparation({key: value for key, value in manifest.items() if key in _PREPARATION_FIELDS})
    jobs = []
    seen: set[str] = set()
    for entry in manifest["jobs"]:
        if type(entry) is not dict or "job_id" not in entry or set(entry) - {"job_id"} - _PREPARATION_FIELDS:
            raise ValueError("Batch job contains unsupported fields")
        identifier = opaque(entry["job_id"])
        if identifier in seen:
            raise ValueError("Batch jobs must be distinct saved versions")
        seen.add(identifier)
        preparation = _preparation({key: value for key, value in entry.items() if key in _PREPARATION_FIELDS}, shared)
        jobs.append({"job_id": identifier, **preparation})
    result = {"schema_version": 1, **shared, "jobs": jobs}
    if len(canonical(result).encode("utf-8")) > 1024 * 1024:
        raise ValueError("Batch specification exceeds its bounded size")
    return result


def _at(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Batch time must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _valid_at(value: object) -> bool:
    try:
        return type(value) is str and _at(datetime.fromisoformat(value)) == value
    except (ValueError, TypeError):
        return False


def _hash(value: object) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _initial_state() -> dict[str, Any]:
    return {"stage": "queued", "attempts": 0, "child_key": None, "fingerprint": None,
            "material_id": None, "bundle_sha256": None, "blockers": []}


def _issue(reason: str, question: str, *, intent: str = "batch_preparation") -> dict[str, Any]:
    return {"kind": "need_info", "reason": reason, "intent": intent,
            "question": question, "related_claim_ids": []}


def _blockers(outcomes: object) -> list[dict[str, Any]]:
    """Retain actionable reasons/IDs, not copied candidate claims or answers."""
    # Fifty valid questions can each produce ten independent evidence outcomes.
    # Deduplicate those outcomes before applying the durable summary bound.
    if type(outcomes) not in {list, tuple} or len(outcomes) > 500:
        raise BatchIntegrityError("Batch blockers are invalid")
    result = []
    oversized = False
    for original in outcomes:
        value = to_jsonable(original)
        if type(value) is not dict or value.get("kind") not in {"need_info", "contradiction"}:
            raise BatchIntegrityError("Batch blocker kind is invalid")
        related = value.get("related_claim_ids", [])
        if value["kind"] == "contradiction" and "conflicting_claims" in value:
            claims = value.get("conflicting_claims", [])
            if type(claims) is not list:
                raise BatchIntegrityError("Batch contradiction is invalid")
            related = [claim["id"] for claim in claims]
        if type(related) is not list:
            raise BatchIntegrityError("Batch blocker references are invalid")
        for identifier in related:
            opaque(identifier)
        if len(related) > 160:
            oversized = True
            related = []
        item = {"kind": value["kind"], "reason": value.get("reason", "contradiction"),
            "intent": value.get("intent", "career_answer"),
            "question": value.get("question", "Review the requested evidence."),
            "related_claim_ids": sorted(set(related))}
        if any(type(item[key]) is not str or not item[key] or len(item[key]) > 2048
               for key in ("kind", "reason", "intent", "question")):
            raise BatchIntegrityError("Batch blocker text is invalid")
        context_fields = {"question_id", "question_label", "question_sha256"}
        if context_fields & set(value):
            if not context_fields <= set(value):
                raise BatchIntegrityError("Batch question context is incomplete")
            opaque(value["question_id"])
            label = value["question_label"]
            if (type(label) is not str or not label.strip() or len(label) > 256
                or any(ord(character) < 32 for character in label) or not _hash(value["question_sha256"])):
                raise BatchIntegrityError("Batch question context is invalid")
            item.update({key: value[key] for key in context_fields})
        if item not in result:
            result.append(item)
    if oversized or len(result) > 200 or len(canonical(result).encode("utf-8")) > 128 * 1024:
        # A large but valid evidence gap is an item blocker, never a shared
        # integrity failure. Keep affected question identities when available;
        # the immutable material retains the full supplied questions/answers.
        summaries = []
        for item in result:
            summary = _issue("blocker_details_exceed_limit",
                "Review or narrow this item's evidence and supplied questions. Any prepared material retains the full required-answer details.")
            summary.update({key: item[key] for key in ("question_id", "question_label", "question_sha256") if key in item})
            if summary not in summaries:
                summaries.append(summary)
        if len(summaries) > 50 or len(canonical(summaries).encode("utf-8")) > 128 * 1024:
            return [_issue("blocker_details_exceed_limit", "Review or narrow this item's evidence and supplied questions before retrying.")]
        return summaries
    return result


def _answer_blockers(answers: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    return _blockers([{**issue, "question_id": answer["question_id"],
        "question_label": answer["question"].strip()[:256], "question_sha256": hash_bytes(answer["question"].encode("utf-8"))}
        for answer in answers if answer["required"] for issue in answer["need_info"]])


def _material_fingerprint(spec: dict[str, Any], structure: object, answers: object) -> str:
    return digest({"schema_version": 1, "job_id": spec["job_id"], "claim_ids": spec["claim_ids"],
        "layout": spec["layout"], "questions": spec["questions"], "structure": structure, "answers": answers})


def _event_payload(item_id: str, position: int, at: str, state: object, previous: str | None) -> dict[str, Any]:
    return {"item_id": item_id, "position": position, "at": at, "state": state, "previous_sha256": previous}


def _counts(items: list[dict[str, Any]]) -> dict[str, int]:
    return {"total": len(items), **{stage: sum(item["status"] == stage for item in items) for stage in ("queued", "building", "draft", "blocked")}}


def _groups(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for item in items:
        for blocker in item["blockers"]:
            key = digest(blocker)
            group = groups.setdefault(key, {**blocker, "item_ids": [], "job_ids": []})
            if item["item_id"] not in group["item_ids"]:
                group["item_ids"].append(item["item_id"])
                group["job_ids"].append(item["job_id"])
    return list(groups.values())


class BatchService:
    def __init__(self, repository: SQLiteRepository, materials: MaterialService, *,
                 clock: Callable[[], datetime] | None = None,
                 monotonic: Callable[[], float] = time.monotonic,
                 parent_guard: Callable[[], None] | None = None,
                 eligibility_guard: Callable[[str], None] | None = None) -> None:
        self._repository = repository
        self._materials = materials
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic = monotonic
        self._parent_guard = parent_guard
        self._eligibility_guard = eligibility_guard

    def _now(self) -> str:
        return _at(self._clock())

    def _preflight(self, spec: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
        structure = asdict(self._materials.plan(spec["job_id"], tuple(spec["claim_ids"]), layout=spec["layout"]))
        answers = list(QuestionnaireService(self._repository).prepare(spec["job_id"], spec["questions"]))
        return structure, answers, _material_fingerprint(spec, structure, answers)

    def plan(self, manifest: object) -> dict[str, Any]:
        spec = validate_batch_manifest(manifest)
        items = []
        with self._repository.read_transaction():
            for position, item in enumerate(spec["jobs"]):
                job = JobService(self._repository).get(item["job_id"])
                try:
                    _, answers, _ = self._preflight(item)
                    blockers = _answer_blockers(answers)
                except MaterialBlocked as blocked:
                    blockers = _blockers(blocked.outcomes)
                except MaterialValidationError:
                    blockers = [_issue("invalid_presentation", "Review the selected claim presentation.")]
                items.append({"item_id": f"preview-{position}", "job_id": item["job_id"],
                    "source_url": job.source_url, "title": None if job.discovery is None else job.discovery["title"],
                    "status": "blocked" if blockers else "queued", "attempts": 0,
                    "material_id": None, "bundle_sha256": None, "currently_valid": False,
                    "blockers": blockers, "questionnaire_coverage": item["questionnaire_coverage"],
                    "requires_approval": True, "material_approved": False, "visual_review_required": True})
        return self._report(None, items, lease_active=False, stop_reason=None, dry_run=True)

    def create(self, manifest: object, *, idempotency_key: str, dry_run: bool = False) -> dict[str, Any]:
        spec = validate_batch_manifest(manifest)
        if type(dry_run) is not bool:
            raise ValueError("Dry run must be boolean")
        payload = request_input(idempotency_key, {"manifest_sha256": digest(spec), "batch_schema_version": 1})
        if dry_run:
            return self.plan(spec)
        batch_id = str(uuid5(NAMESPACE_URL, "grounded-apply.batch@1/" + payload["idempotency_sha256"]))
        replayed = False
        with self._repository.transaction():
            existing = existing_workflow(self._repository, "batch_create", payload)
            if existing is not None:
                if json.loads(existing["generated_artifacts_json"]) != [batch_id]:
                    raise BatchIntegrityError("Batch creation replay is invalid")
                self._validated(batch_id)
                replayed = True
            else:
                for item in spec["jobs"]:
                    JobService(self._repository).get(item["job_id"])
                at = self._now()
                workflow = start_workflow(self._repository, "batch_create", payload, at)
                self._repository.insert_preparation_batch(batch_id=batch_id, manifest=spec,
                    manifest_sha256=digest(spec), created_at=at, workflow_run_id=workflow["id"])
                for position, item in enumerate(spec["jobs"]):
                    item_id = str(uuid5(NAMESPACE_URL, f"{batch_id}/item/{position}/{item['job_id']}"))
                    self._repository.insert_preparation_item(item_id=item_id, batch_id=batch_id,
                        position=position, job_id=item["job_id"], spec=item, spec_sha256=digest(item))
                    self._append_event(item_id, _initial_state(), at=at)
                finish_workflow(self._repository, workflow["id"], [batch_id], at)
                self._capacity(MAX_SNAPSHOT_BYTES - CHECKPOINT_RESERVE_BYTES)
                self._validated(batch_id)
        result = self.get(batch_id)
        result["replayed"] = replayed
        return result

    def _capacity(self, maximum: int = MAX_SNAPSHOT_BYTES) -> None:
        if self._repository.database_size_bytes() > maximum:
            raise BatchCapacityError("Batch storage capacity reached; the current checkpoint was not added")

    def _lease(self, batch_id: str) -> Record:
        lease = self._repository.get_preparation_lease(batch_id)
        try:
            if (lease is None or lease["batch_id"] != batch_id or type(lease["epoch"]) is not int
                or lease["epoch"] < 0 or lease["stop_reason"] not in _STOP_REASONS
                or (lease["owner"] is None) != (lease["expires_at"] is None)):
                raise ValueError
            if lease["owner"] is not None:
                opaque(lease["owner"])
                if not _valid_at(lease["expires_at"]):
                    raise ValueError
        except (ValueError, TypeError, KeyError):
            raise BatchIntegrityError("Batch lease failed integrity checks") from None
        return lease

    def _validate_state(self, state: object, previous: dict[str, Any] | None) -> dict[str, Any]:
        if (type(state) is not dict or set(state) != _STATE_FIELDS or state["stage"] not in _STAGES
            or type(state["attempts"]) is not int or not 0 <= state["attempts"] <= MAX_ITEM_ATTEMPTS
            or type(state["blockers"]) is not list or _blockers(state["blockers"]) != state["blockers"]
            or (state["child_key"] is None) != (state["fingerprint"] is None)
            or (state["material_id"] is None) != (state["bundle_sha256"] is None)):
            raise BatchIntegrityError("Batch item state is invalid")
        if state["fingerprint"] is not None and (not _hash(state["fingerprint"])
            or state["child_key"] != "batch-material-v1." + state["fingerprint"]):
            raise BatchIntegrityError("Batch child identity is invalid")
        if state["material_id"] is not None:
            opaque(state["material_id"])
            if not _hash(state["bundle_sha256"]) or state["child_key"] is None:
                raise BatchIntegrityError("Batch material reference is invalid")
        if state["stage"] == "draft" and (state["material_id"] is None or state["blockers"]):
            raise BatchIntegrityError("Draft checkpoint lacks a complete validated material")
        if state["stage"] == "blocked" and not state["blockers"]:
            raise BatchIntegrityError("Blocked checkpoint lacks an explanation")
        if state["stage"] in {"queued", "building"} and state["blockers"]:
            raise BatchIntegrityError("Pending checkpoint has terminal blockers")
        if previous is None:
            if state != _initial_state():
                raise BatchIntegrityError("Batch initial checkpoint is invalid")
        else:
            attempt_delta = state["attempts"] - previous["attempts"]
            if state["stage"] == "queued" or attempt_delta not in {0, 1}:
                raise BatchIntegrityError("Invalid batch item transition")
            if attempt_delta == 1 and (state["stage"] != "building" or previous["stage"] == "draft"):
                raise BatchIntegrityError("Invalid batch attempt transition")
            attempt_limit = (previous["stage"] == "blocked" and state["stage"] == "blocked"
                and state["attempts"] == MAX_ITEM_ATTEMPTS
                and len(state["blockers"]) == 1 and state["blockers"][0]["reason"] == "attempt_limit")
            if attempt_delta == 0 and not (previous["stage"] == "building" or previous["stage"] == "draft" and state["stage"] == "blocked" or attempt_limit):
                raise BatchIntegrityError("Batch item transition skipped its attempt")
            if previous["fingerprint"] is not None and (state["fingerprint"] != previous["fingerprint"] or state["child_key"] != previous["child_key"]):
                raise BatchIntegrityError("Batch preparation identity changed")
            if previous["material_id"] is not None and (state["material_id"] != previous["material_id"] or state["bundle_sha256"] != previous["bundle_sha256"]):
                raise BatchIntegrityError("Batch material history was replaced")
        return state

    def _item_state(self, item_id: str) -> tuple[dict[str, Any], list[Record]]:
        events = self._repository.list_preparation_events(item_id)
        if not events:
            raise BatchIntegrityError("Batch item checkpoints are missing")
        previous_hash = None
        previous_state = None
        previous_at = None
        try:
            for position, event in enumerate(events):
                state = json.loads(event["state_json"])
                expected = _event_payload(item_id, position, event["at"], state, previous_hash)
                if (event["item_id"] != item_id or event["position"] != position
                    or event["id"] != str(uuid5(NAMESPACE_URL, f"{item_id}/event/{position}"))
                    or not _valid_at(event["at"]) or previous_at is not None and event["at"] < previous_at
                    or event["previous_sha256"] != previous_hash or event["event_sha256"] != digest(expected)):
                    raise ValueError
                previous_state = self._validate_state(state, previous_state)
                previous_hash, previous_at = event["event_sha256"], event["at"]
        except (ValueError, TypeError, KeyError):
            raise BatchIntegrityError("Batch checkpoint chain failed integrity checks") from None
        assert previous_state is not None
        return previous_state, events

    def _append_event(self, item_id: str, state: dict[str, Any], *, at: str | None = None) -> None:
        events = self._repository.list_preparation_events(item_id)
        if events:
            previous_state, events = self._item_state(item_id)
            self._validate_state(state, previous_state)
        else:
            self._validate_state(state, None)
        moment = self._now() if at is None else at
        if events and moment < events[-1]["at"]:
            raise BatchIntegrityError("Batch clock moved backwards")
        position = len(events)
        previous = None if not events else events[-1]["event_sha256"]
        payload = _event_payload(item_id, position, moment, state, previous)
        self._repository.insert_preparation_event(event_id=str(uuid5(NAMESPACE_URL, f"{item_id}/event/{position}")),
            item_id=item_id, position=position, at=moment, state=state,
            previous_sha256=previous, event_sha256=digest(payload))

    def _validated(self, batch_id: str) -> tuple[Record, list[tuple[Record, dict[str, Any], dict[str, Any]]], Record]:
        opaque(batch_id)
        batch = self._repository.get_preparation_batch(batch_id)
        if batch is None:
            raise ValueError("Preparation batch does not exist")
        try:
            manifest = json.loads(batch["manifest_json"])
            if validate_batch_manifest(manifest) != manifest or digest(manifest) != batch["manifest_sha256"] or not _valid_at(batch["created_at"]):
                raise ValueError
            workflow = self._repository.get_workflow_run(batch["workflow_run_id"])
            if workflow is None:
                raise ValueError
            payload = {"version": 1, "manifest_sha256": digest(manifest), "batch_schema_version": 1,
                "idempotency_sha256": workflow["idempotency_key"]}
            validate_workflow(workflow, "batch_create", payload)
            if (batch_id != str(uuid5(NAMESPACE_URL, "grounded-apply.batch@1/" + workflow["idempotency_key"]))
                or workflow["created_at"] != batch["created_at"] or json.loads(workflow["generated_artifacts_json"]) != [batch_id]):
                raise ValueError
            rows = self._repository.list_preparation_items(batch_id)
            if len(rows) != len(manifest["jobs"]):
                raise ValueError
            items = []
            for position, (record, spec) in enumerate(zip(rows, manifest["jobs"], strict=True)):
                if (record["batch_id"] != batch_id or record["position"] != position or record["job_id"] != spec["job_id"]
                    or record["id"] != str(uuid5(NAMESPACE_URL, f"{batch_id}/item/{position}/{spec['job_id']}"))
                    or json.loads(record["spec_json"]) != spec or record["spec_sha256"] != digest(spec)):
                    raise ValueError
                job = JobService(self._repository).get(spec["job_id"])
                state, events = self._item_state(record["id"])
                if events[0]["at"] != batch["created_at"]:
                    raise ValueError
                question_context = {question["id"]: (question["text"].strip()[:256],
                    hash_bytes(question["text"].encode("utf-8"))) for question in spec["questions"]}
                for event in events:
                    for blocker in json.loads(event["state_json"])["blockers"]:
                        if "question_id" in blocker and question_context.get(blocker["question_id"]) != (
                            blocker["question_label"], blocker["question_sha256"]):
                            raise ValueError
                items.append(({**record, "source_url": job.source_url,
                    "title": None if job.discovery is None else job.discovery["title"]}, spec, state))
        except (ValueError, TypeError, KeyError):
            raise BatchIntegrityError("Batch request failed integrity checks") from None
        return batch, items, self._lease(batch_id)

    def _binding(self, material: dict[str, Any], spec: dict[str, Any], state: dict[str, Any]) -> None:
        fingerprint = _material_fingerprint(spec, material["structure"], material["manifest"]["answers"])
        workflow = self._repository.get_workflow_run(material["workflow_run_id"])
        if (material["job_id"] != spec["job_id"] or material["bundle_sha256"] != state["bundle_sha256"]
            or material["manifest"]["question_specs"] != spec["questions"] or fingerprint != state["fingerprint"]
            or workflow is None or workflow["idempotency_key"] != hash_bytes(state["child_key"].encode())):
            raise BatchIntegrityError("Batch material does not match its recorded preparation")

    def _view(self, record: Record, spec: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        stage, blockers, current, approved = state["stage"], state["blockers"], False, False
        if state["material_id"] is not None:
            try:
                material = self._materials.get(state["material_id"])
                current = True
            except MaterialBlocked as blocked:
                material = self._materials.get(state["material_id"], require_current=False)
                stage, blockers = "blocked", _blockers(blocked.outcomes)
            self._binding(material, spec, state)
            required = _answer_blockers(material["manifest"]["answers"])
            if current and required:
                stage, blockers = "blocked", required
            if state["stage"] == "draft" and required:
                raise BatchIntegrityError("Draft checkpoint concealed required unanswered questions")
            if current and self._repository.get_material_approval(state["material_id"]) is not None:
                approved = self._materials.is_approved(state["material_id"])
        return {"item_id": record["id"], "job_id": spec["job_id"], "status": stage,
            "source_url": record["source_url"], "title": record["title"],
            "attempts": state["attempts"], "material_id": state["material_id"], "bundle_sha256": state["bundle_sha256"],
            "blockers": blockers, "currently_valid": current, "questionnaire_coverage": spec["questionnaire_coverage"],
            "requires_approval": not approved, "material_approved": approved, "visual_review_required": not approved}

    def _report(self, batch_id: str | None, items: list[dict[str, Any]], *, lease_active: bool,
                stop_reason: str | None, dry_run: bool = False) -> dict[str, Any]:
        counts = _counts(items)
        remaining = counts["queued"] + counts["building"]
        status = "running" if lease_active else "completed" if not remaining and not counts["blocked"] else "waiting_for_input" if not remaining else "queued"
        if remaining and stop_reason in {"item_budget", "time_budget", "capacity_reached"}:
            status = "budget_exhausted"
        if stop_reason == "shared_failure":
            status = "failed"
        return {"schema_version": 1, "batch_id": batch_id, "status": status, "dry_run": dry_run,
            "items": items, "counts": counts, "remaining_count": remaining, "blockers_count": counts["blocked"],
            "grouped_blockers": _groups(items), "stop_reason": stop_reason, "lease_active": lease_active,
            "external_action_taken": False, "approvals_recorded": False, "application_ready": False,
            "limitations": ["Drafts require visual review and exact-bundle approval before use.",
                "Unknown questionnaire coverage does not mean an application form is complete.",
                "Grouped blockers do not authorize storing or reusing sensitive answers."]}

    def get(self, batch_id: str) -> dict[str, Any]:
        with self._repository.read_transaction():
            batch, records, lease = self._validated(batch_id)
            items = [self._view(record, spec, state) for record, spec, state in records]
            active = lease["owner"] is not None and lease["expires_at"] > self._now()
            result = self._report(batch_id, items, lease_active=active, stop_reason=lease["stop_reason"])
            result["created_at"] = batch["created_at"]
            return result

    def search_history(self, batch_id: str, *, current_job_ids: frozenset[str]) -> tuple[BatchSearchHistoryItem, ...]:
        """Audit all history, resolving current facts only for candidate drafts.

        The caller matches incoming posting versions to these immutable job IDs.
        Every saved artifact, binding, approval and profile provenance record is
        still checked on this call. No result grants approval or readiness.
        """
        opaque(batch_id)
        if type(current_job_ids) is not frozenset or len(current_job_ids) > 1000:
            raise ValueError("Search history needs at most one thousand distinct saved job IDs")
        for job_id in current_job_ids:
            opaque(job_id)
        with self._repository.read_transaction():
            _, records, _ = self._validated(batch_id)
            if any(state["material_id"] is not None for _, _, state in records):
                # Historical artifacts must not hide corrupt unrelated import,
                # review or retirement provenance when no posting can be reused.
                ProfileService(self._repository).validated_profile()
            history = []
            for _, spec, state in records:
                reusable = False
                material_id = state["material_id"]
                if material_id is not None:
                    material = self._materials.get(material_id, require_current=False)
                    self._binding(material, spec, state)
                    answers = material["manifest"]["answers"]
                    required = _answer_blockers(answers)
                    incomplete = any(answer["required"] and answer["status"] != "draft" for answer in answers)
                    if state["stage"] == "draft" and (required or incomplete):
                        raise BatchIntegrityError("Draft checkpoint concealed required unanswered questions")
                    if self._repository.get_material_approval(material_id) is not None:
                        self._materials.is_approved(material_id, require_current=False)
                    if state["stage"] == "draft" and spec["job_id"] in current_job_ids:
                        try:
                            self._materials.get(material_id)
                            reusable = True
                        except MaterialBlocked:
                            pass
                history.append(BatchSearchHistoryItem(spec["job_id"], state["attempts"], reusable))
            return tuple(history)

    def list(self) -> tuple[dict[str, Any], ...]:
        with self._repository.read_transaction():
            return tuple(self.get(record["id"]) for record in self._repository.list_preparation_batches())

    def _assert_lease(self, batch_id: str, owner: str, epoch: int) -> None:
        lease = self._lease(batch_id)
        if lease["owner"] != owner or lease["epoch"] != epoch or lease["expires_at"] <= self._now():
            raise BatchLeaseLostError("Batch lease expired or was replaced; resume from its durable checkpoints")

    def _checkpoint(self, batch_id: str, item_id: str, state: dict[str, Any], owner: str, epoch: int) -> None:
        with self._repository.transaction():
            if self._parent_guard is not None:
                self._parent_guard()
            self._assert_lease(batch_id, owner, epoch)
            self._append_event(item_id, state)
            self._capacity()
            if self._parent_guard is not None:
                self._parent_guard()
            self._assert_lease(batch_id, owner, epoch)

    def run(self, batch_id: str, *, max_items: int = 20, max_seconds: int = 900) -> dict[str, Any]:
        if (type(max_items) is not int or not 1 <= max_items <= MAX_BATCH_ITEMS
            or type(max_seconds) is not int or not 1 <= max_seconds <= 3600):
            raise ValueError("Batch invocation budgets require one to fifty items and one to three thousand six hundred seconds")
        owner, started = str(uuid4()), self._monotonic()
        with self._repository.transaction():
            if self._repository.get_search_batch_link(batch_id) is not None and self._parent_guard is None:
                raise ValueError("Resume this batch through its saved search run")
            if self._parent_guard is not None:
                self._parent_guard()
            _, records, lease = self._validated(batch_id)
            now = self._now()
            until = _at(datetime.fromisoformat(now) + timedelta(seconds=max_seconds + 120))
            epoch = self._repository.acquire_preparation_lease(batch_id, expected_epoch=lease["epoch"], owner=owner, now=now, expires_at=until)
            if epoch is None:
                raise BatchLeaseActiveError("Another invocation owns this batch; inspect it or resume after its lease expires")
            self._capacity()
        attempted, stop_reason = 0, None
        try:
            for record, spec, previous in records:
                if self._monotonic() - started >= max_seconds:
                    stop_reason = "time_budget"
                    break
                if previous["stage"] == "draft":
                    # Current evidence is rechecked even when no new output is
                    # requested. Integrity/PDF failures are shared failures.
                    try:
                        if self._eligibility_guard is not None:
                            self._eligibility_guard(spec["job_id"])
                        view = self._view(record, spec, previous)
                    except MaterialBlocked as blocked:
                        view = {"status": "blocked", "blockers": _blockers(blocked.outcomes)}
                    if view["status"] == "blocked":
                        self._checkpoint(batch_id, record["id"], {**previous, "stage": "blocked", "blockers": view["blockers"]}, owner, epoch)
                    continue
                if attempted >= max_items:
                    stop_reason = "item_budget"
                    break
                if self._monotonic() - started >= max_seconds:
                    stop_reason = "time_budget"
                    break
                if previous["attempts"] >= MAX_ITEM_ATTEMPTS:
                    issue = _issue("attempt_limit", "This item reached its retry bound. Review its blockers and create a new batch.")
                    if previous["blockers"] != [issue]:
                        self._checkpoint(batch_id, record["id"], {**previous, "stage": "blocked", "blockers": [issue]}, owner, epoch)
                    continue
                attempted += 1
                state = {**previous, "stage": "building", "attempts": previous["attempts"] + 1, "blockers": []}
                self._checkpoint(batch_id, record["id"], state, owner, epoch)
                try:
                    if self._eligibility_guard is not None:
                        self._eligibility_guard(spec["job_id"])
                    structure, answers, fingerprint = self._preflight(spec)
                except MaterialBlocked as blocked:
                    self._checkpoint(batch_id, record["id"], {**state, "stage": "blocked", "blockers": _blockers(blocked.outcomes)}, owner, epoch)
                    continue
                except MaterialValidationError:
                    self._checkpoint(batch_id, record["id"], {**state, "stage": "blocked", "blockers": [_issue("invalid_presentation", "Review the selected claim presentation.")]}, owner, epoch)
                    continue
                if state["fingerprint"] is not None and state["fingerprint"] != fingerprint:
                    self._checkpoint(batch_id, record["id"], {**state, "stage": "blocked", "blockers": [_issue("preparation_inputs_changed", "Approved preparation inputs changed. Create a new batch to prepare a fresh version.")]}, owner, epoch)
                    continue
                if state["fingerprint"] is None:
                    state = {**state, "fingerprint": fingerprint, "child_key": "batch-material-v1." + fingerprint}
                    self._checkpoint(batch_id, record["id"], state, owner, epoch)
                try:
                    result = self._materials.build(spec["job_id"], tuple(spec["claim_ids"]),
                        idempotency_key=state["child_key"], questions=spec["questions"], layout=spec["layout"],
                        max_database_bytes=MAX_SNAPSHOT_BYTES - CHECKPOINT_RESERVE_BYTES,
                        commit_guard=lambda: self._material_guard(batch_id, owner, epoch, spec["job_id"]),
                        expected_plan_sha256=digest({"structure": structure, "answers": answers}))
                except MaterialBlocked as blocked:
                    self._checkpoint(batch_id, record["id"], {**state, "stage": "blocked", "blockers": _blockers(blocked.outcomes)}, owner, epoch)
                    continue
                except MaterialRenderError as error:
                    self._checkpoint(batch_id, record["id"], {**state, "stage": "blocked", "blockers": [_issue(error.reason, str(error))]}, owner, epoch)
                    continue
                except MaterialCapacityError:
                    stop_reason = "capacity_reached"
                    break
                blockers = _answer_blockers(answers)
                self._checkpoint(batch_id, record["id"], {**state, "stage": "blocked" if blockers else "draft",
                    "material_id": result["material_id"], "bundle_sha256": result["bundle_sha256"], "blockers": blockers}, owner, epoch)
        except StorageCapacityError:
            stop_reason = "capacity_reached"
        except BaseException:
            stop_reason = "shared_failure"
            raise
        finally:
            with self._repository.transaction():
                self._repository.release_preparation_lease(batch_id, owner=owner, epoch=epoch, stop_reason=stop_reason)
                self._capacity()
        result = self.get(batch_id)
        result["attempted_this_run"] = attempted
        return result

    def _material_guard(self, batch_id: str, owner: str, epoch: int, job_id: str) -> None:
        if self._parent_guard is not None:
            self._parent_guard()
        self._assert_lease(batch_id, owner, epoch)
        if self._eligibility_guard is not None:
            self._eligibility_guard(job_id)
