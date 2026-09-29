"""Audited withdrawal/replacement without rewriting imported truth history."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256

from grounded_apply.domain import ApprovalStatus, Claim, ClaimStatus, Evidence, Sensitivity, SourceType, to_jsonable
from grounded_apply.repositories import Record, RepositoryError, SQLiteRepository
from grounded_apply.services.profile import ProfileService

WORKFLOW = "profile_retirement"


def digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _claim_digest(claim: Claim, evidence: tuple[Evidence, ...]) -> str:
    return digest({"claim": to_jsonable(claim), "evidence": to_jsonable(tuple(e for e in evidence if e.claim_id == claim.id))})


def _input(claim_id: str, replacement_id: str | None, claim_sha: str, replacement_sha: str | None, actor: str, key_sha: str) -> dict[str, object]:
    return {"version": 1, "claim_id": claim_id, "replacement_claim_id": replacement_id,
            "claim_sha256": claim_sha, "replacement_sha256": replacement_sha,
            "actor_id": actor, "idempotency_sha256": key_sha}


def _validate_record(repository: SQLiteRepository, record: Record, claims: dict[str, Claim], evidence: tuple[Evidence, ...]) -> None:
    try:
        claim = claims[record["claim_id"]]
        replacement_id = record["replacement_claim_id"]
        replacement = None if replacement_id is None else claims[replacement_id]
        payload = _input(claim.id, replacement_id, _claim_digest(claim, evidence),
                         None if replacement is None else _claim_digest(replacement, evidence),
                         record["actor_id"], record["idempotency_sha256"])
        token = digest(payload)
        workflow = repository.get_workflow_run(record["workflow_run_id"])
        if (
            workflow is None or workflow["workflow_type"] != WORKFLOW
            or workflow["status"] != "succeeded" or workflow["input_hash_sha256"] != token
            or json.loads(workflow["input_json"]) != payload
            or workflow["idempotency_key"] != record["idempotency_sha256"]
            or workflow["finished_at"] != record["retired_at"]
            or workflow["created_at"] != record["retired_at"]
            or workflow["current_step"] != "retirement_recorded"
            or json.loads(workflow["completed_steps_json"]) != ["validate", "retire"]
            or json.loads(workflow["generated_artifacts_json"]) != [claim.id]
            or record["preview_token"] != token
            or record["claim_sha256"] != payload["claim_sha256"]
            or record["replacement_sha256"] != payload["replacement_sha256"]
            or claim.status != ClaimStatus.VERIFIED or claim.approval_status != ApprovalStatus.APPROVED
            or claim.source_type not in (SourceType.IMPORTED_RESUME, SourceType.USER_STATEMENT)
            or repository.get_profile_import_review_item(claim.id) is None
        ):
            raise ValueError
        retired_at = datetime.fromisoformat(record["retired_at"])
        if retired_at.tzinfo is None or claim.verified_at is None or retired_at < claim.verified_at:
            raise ValueError
    except (KeyError, ValueError, TypeError):
        raise RepositoryError("Claim retirement failed integrity checks") from None


def project_retirements(repository: SQLiteRepository, claims: tuple[Claim, ...], evidence: tuple[Evidence, ...]) -> tuple[Claim, ...]:
    by_id = {c.id: c for c in claims}
    records = repository.list_claim_retirements()
    for record in records:
        _validate_record(repository, record, by_id, evidence)
    for record in records:
        claim = by_id[record["claim_id"]]
        by_id[claim.id] = replace(claim, status=ClaimStatus.SUPERSEDED if record["replacement_claim_id"] else ClaimStatus.WITHDRAWN)
    return tuple(by_id[c.id] for c in claims)


@dataclass(frozen=True, slots=True)
class RetirementResult:
    claim_id: str
    replacement_claim_id: str | None
    preview_token: str
    dry_run: bool
    recorded: bool
    replayed: bool
    external_action_taken: bool = False


class ProfileLifecycleService:
    def __init__(self, repository: SQLiteRepository) -> None:
        self._repository = repository

    def retire(
        self, claim_id: str, *, replacement_claim_id: str | None = None,
        actor_id: str, idempotency_key: str, confirm: bool = False,
        preview_token: str | None = None, now: datetime | None = None,
    ) -> RetirementResult:
        for value in (claim_id, actor_id, idempotency_key, replacement_claim_id):
            if value is not None and (type(value) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", value)):
                raise ValueError("Retirement requires opaque identifiers")
        if type(confirm) is not bool or (confirm and (type(preview_token) is not str or not re.fullmatch(r"[0-9a-f]{64}", preview_token))):
            raise ValueError("Confirmed retirement requires a preview token")
        at = now or datetime.now(UTC)
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("Retirement time must be timezone-aware")
        with (self._repository.transaction() if confirm else self._repository.read_transaction()):
            claims, evidence = ProfileService(self._repository).validated_profile(apply_retirements=False)
            effective = {c.id: c for c in project_retirements(self._repository, claims, evidence)}
            originals = {c.id: c for c in claims}
            claim = originals.get(claim_id)
            if (
                claim is None or claim.source_type not in (SourceType.IMPORTED_RESUME, SourceType.USER_STATEMENT)
                or self._repository.get_profile_import_review_item(claim_id) is None
                or claim.status != ClaimStatus.VERIFIED or claim.approval_status != ApprovalStatus.APPROVED
            ):
                raise ValueError("Retirement requires an approved imported claim")
            replacement = None if replacement_claim_id is None else originals.get(replacement_claim_id)
            if replacement_claim_id is not None and (
                replacement is None or replacement.id == claim.id
                or replacement.status != ClaimStatus.VERIFIED or replacement.approval_status != ApprovalStatus.APPROVED
                or replacement.sensitivity != Sensitivity.PERSONAL
                or (replacement.claim_type, replacement.subject_type, replacement.subject_id, replacement.scope)
                != (claim.claim_type, claim.subject_type, claim.subject_id, claim.scope)
            ):
                raise ValueError("Replacement must be an approved distinct claim for the same fact and scope")
            payload = _input(claim_id, replacement_claim_id, _claim_digest(claim, evidence),
                             None if replacement is None else _claim_digest(replacement, evidence),
                             actor_id, sha256(idempotency_key.encode()).hexdigest())
            token = digest(payload)
            existing = self._repository.get_claim_retirement(claim_id)
            if existing is not None:
                if confirm and preview_token == token == existing["preview_token"]:
                    return RetirementResult(claim_id, replacement_claim_id, token, False, True, True)
                raise ValueError("Claim was already retired; only the original confirmed request may be replayed")
            if replacement is not None and effective[replacement.id].status != ClaimStatus.VERIFIED:
                raise ValueError("Replacement is already retired")
            if preview_token is not None and token != preview_token:
                raise ValueError("Retirement preview is stale or does not match")
            if claim.verified_at is None or at < claim.verified_at or (replacement is not None and (replacement.verified_at is None or at < replacement.verified_at)):
                raise ValueError("Retirement cannot precede approval")
            if not confirm:
                return RetirementResult(claim_id, replacement_claim_id, token, True, False, False)
            timestamp = at.astimezone(UTC).isoformat()
            workflow = self._repository.add_workflow_run(
                workflow_type=WORKFLOW, status="running", idempotency_key=payload["idempotency_sha256"],
                input_hash_sha256=token, input_data=payload, current_step="validate", created_at=timestamp,
            )
            self._repository.add_claim_retirement(
                claim_id=claim_id, replacement_claim_id=replacement_claim_id,
                workflow_run_id=workflow["id"], actor_id=actor_id, preview_token=token,
                claim_sha256=payload["claim_sha256"], replacement_sha256=payload["replacement_sha256"],
                idempotency_sha256=payload["idempotency_sha256"], retired_at=timestamp,
            )
            self._repository.update_workflow_run(
                workflow["id"], status="succeeded", current_step="retirement_recorded",
                completed_steps=("validate", "retire"), generated_artifacts=(claim_id,), finished_at=timestamp,
            )
            record = self._repository.get_claim_retirement(claim_id)
            assert record is not None
            _validate_record(self._repository, record, originals, evidence)
            return RetirementResult(claim_id, replacement_claim_id, token, False, True, False)
