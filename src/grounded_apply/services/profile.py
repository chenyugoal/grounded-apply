"""Validated candidate-claim workflows over the mapping repository API."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from grounded_apply.domain import (
    ApprovalStatus,
    Claim,
    ClaimStatus,
    ClaimUsePolicy,
    Derivation,
    Evidence,
    EvidenceConfirmationStatus,
    JsonValue,
    ResolutionOutcome,
    Scope,
    ScopeType,
    Sensitivity,
    SourceType,
    resolve_claims,
)
from grounded_apply.repositories import Record, RecordNotFoundError, SQLiteRepository


def utc_now() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: object, *, field_name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be an ISO-8601 string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed


def _required_timestamp(record: Record, field_name: str) -> datetime:
    value = _parse_timestamp(record.get(field_name), field_name=field_name)
    if value is None:
        raise ValueError(f"Stored {field_name} must not be null")
    return value


def _json_value(value: object, *, field_name: str) -> JsonValue:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be stored as JSON text")
    decoded: Any = json.loads(value)
    return decoded


def _required_text(record: Record, field_name: str) -> str:
    value = record.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Stored {field_name} must be non-blank text")
    return value


def _optional_text(record: Record, field_name: str) -> str | None:
    value = record.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"Stored {field_name} must be text or null")
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateClaim:
    claim_type: str
    value: JsonValue
    canonical_text: str
    source_type: SourceType
    source_ref: str
    status: ClaimStatus = ClaimStatus.NEEDS_REVIEW
    approval_status: ApprovalStatus = ApprovalStatus.PENDING
    subject_type: str = "person"
    subject_id: str | None = None
    confidence: float = 1.0
    sensitivity: Sensitivity = Sensitivity.PERSONAL
    scope: Scope = field(default_factory=Scope)
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    verified_at: datetime | None = None
    verified_by: str | None = None
    derivation: Derivation | None = None
    supersedes_id: str | None = None
    evidence_ids: tuple[str, ...] = ()
    claim_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateEvidence:
    claim_id: str
    source_type: SourceType
    source_ref: str
    source_text: str
    locator: JsonValue = None
    extraction_method: str = "manual"
    confirmation_status: EvidenceConfirmationStatus = EvidenceConfirmationStatus.PENDING
    artifact_id: str | None = None
    checksum: str | None = None
    confirmed_by: str | None = None
    evidence_id: str | None = None


class ProfileService:
    """Validate storage mutations and expose deterministic resolution."""

    def __init__(self, repository: SQLiteRepository) -> None:
        self._repository = repository

    def create_claim(self, request: CreateClaim, *, now: datetime | None = None) -> Claim:
        created_at = now or utc_now()
        if request.status is ClaimStatus.DERIVED:
            raise ValueError(
                "Derived claims must be created by a registered deterministic rule"
            )
        verified_at = request.verified_at
        if request.status is ClaimStatus.VERIFIED and verified_at is None:
            verified_at = created_at
        if request.status is ClaimStatus.VERIFIED and not (
            request.verified_by and request.verified_by.strip()
        ):
            raise ValueError("verified claims require verified_by")
        claim = Claim(
            id=request.claim_id or str(uuid.uuid4()),
            claim_type=request.claim_type,
            value_json=request.value,
            canonical_text=request.canonical_text,
            subject_type=request.subject_type,
            subject_id=request.subject_id,
            status=request.status,
            approval_status=request.approval_status,
            confidence=request.confidence,
            sensitivity=request.sensitivity,
            scope=request.scope,
            effective_from=request.effective_from,
            effective_to=request.effective_to,
            source_type=request.source_type,
            source_ref=request.source_ref,
            evidence_ids=request.evidence_ids,
            verified_at=verified_at,
            verified_by=request.verified_by,
            derivation=request.derivation,
            supersedes_id=request.supersedes_id,
            created_at=created_at,
            updated_at=created_at,
        )
        if not request.source_ref.strip():
            raise ValueError("source_ref must not be blank")
        derivation = claim.derivation
        self._repository.add_claim(
            claim_id=claim.id,
            claim_type=claim.claim_type,
            value=claim.value_json,
            canonical_text=claim.canonical_text,
            subject_type=claim.subject_type,
            subject_id=claim.subject_id,
            status=claim.status.value,
            approval_status=claim.approval_status.value,
            confidence=claim.confidence,
            sensitivity=claim.sensitivity.value,
            scope_type=claim.scope.type.value,
            scope_id=claim.scope.id,
            effective_from=_timestamp(claim.effective_from),
            effective_to=_timestamp(claim.effective_to),
            source_type=claim.source_type.value,
            source_ref=claim.source_ref,
            verified_at=_timestamp(claim.verified_at),
            verified_by=claim.verified_by,
            derivation_rule_name=None if derivation is None else derivation.rule_name,
            derivation_rule_version=None if derivation is None else derivation.rule_version,
            derivation_input_claim_ids=(
                () if derivation is None else derivation.input_claim_ids
            ),
            derivation_staleness_policy=(
                None if derivation is None else derivation.staleness_policy
            ),
            derivation_calculated_at=(
                None if derivation is None else _timestamp(derivation.calculated_at)
            ),
            supersedes_id=claim.supersedes_id,
            evidence_ids=claim.evidence_ids,
            created_at=_timestamp(claim.created_at),
        )
        return claim

    def create_evidence(
        self,
        request: CreateEvidence,
        *,
        now: datetime | None = None,
    ) -> Evidence:
        captured_at = now or utc_now()
        evidence = Evidence(
            id=request.evidence_id or str(uuid.uuid4()),
            claim_id=request.claim_id,
            source_type=request.source_type,
            source_ref=request.source_ref,
            artifact_id=request.artifact_id,
            locator=request.locator,
            source_text=request.source_text,
            extraction_method=request.extraction_method,
            captured_at=captured_at,
            checksum=request.checksum,
            confirmation_status=request.confirmation_status,
        )
        if request.confirmation_status is EvidenceConfirmationStatus.CONFIRMED and not (
            request.confirmed_by and request.confirmed_by.strip()
        ):
            raise ValueError("confirmed evidence requires confirmed_by")
        if self._repository.get_claim(evidence.claim_id) is None:
            raise RecordNotFoundError(f"Claim does not exist: {evidence.claim_id}")
        self._repository.add_evidence(
            evidence_id=evidence.id,
            claim_id=evidence.claim_id,
            artifact_id=evidence.artifact_id,
            locator=evidence.locator,
            source_text=evidence.source_text,
            extraction_method=evidence.extraction_method or "manual",
            captured_at=_timestamp(evidence.captured_at),
            checksum_sha256=evidence.checksum,
            source_type=evidence.source_type.value,
            source_ref=evidence.source_ref,
            confirmation_status=evidence.confirmation_status.value,
            confirmed_by=request.confirmed_by,
        )
        return evidence

    def get_claim(self, claim_id: str) -> Claim:
        record = self._repository.get_claim(claim_id)
        if record is None:
            raise RecordNotFoundError(f"Claim does not exist: {claim_id}")
        return self._claim_from_record(record)

    def list_claims(
        self,
        *,
        claim_type: str | None = None,
        status: ClaimStatus | None = None,
        approval_status: ApprovalStatus | None = None,
    ) -> tuple[Claim, ...]:
        records = self._repository.list_claims(
            claim_type=claim_type,
            status=None if status is None else status.value,
            approval_status=None if approval_status is None else approval_status.value,
        )
        return tuple(self._claim_from_record(record) for record in records)

    def resolve(
        self,
        *,
        intent: str,
        policy: ClaimUsePolicy,
        require_question: str | None = None,
        requested_sensitivity: Sensitivity = Sensitivity.PERSONAL,
    ) -> ResolutionOutcome:
        claims = self.list_claims()
        evidence = tuple(
            item
            for claim in claims
            for item in self._evidence_for_claim(claim.id)
        )
        return resolve_claims(
            claims,
            intent=intent,
            policy=policy,
            evidence=evidence,
            question=require_question,
            requested_sensitivity=requested_sensitivity,
        )

    def _claim_from_record(self, record: Record) -> Claim:
        status = ClaimStatus(_required_text(record, "status"))
        derivation: Derivation | None = None
        if status is ClaimStatus.DERIVED:
            derivation = Derivation(
                rule_name=_required_text(record, "derivation_rule_name"),
                rule_version=_required_text(record, "derivation_rule_version"),
                input_claim_ids=tuple(
                    self._repository.list_derivation_input_ids(_required_text(record, "id"))
                ),
                staleness_policy=_required_text(record, "derivation_staleness_policy"),
                calculated_at=_required_timestamp(record, "derivation_calculated_at"),
            )
        evidence_ids = tuple(
            _required_text(link, "evidence_id")
            for link in self._repository.list_claim_evidence(
                claim_id=_required_text(record, "id")
            )
        )
        confidence = record.get("confidence")
        if not isinstance(confidence, (int, float)):
            raise TypeError("Stored confidence must be numeric")
        return Claim(
            id=_required_text(record, "id"),
            claim_type=_required_text(record, "claim_type"),
            value_json=_json_value(record.get("value_json"), field_name="value_json"),
            canonical_text=_required_text(record, "canonical_text"),
            subject_type=_required_text(record, "subject_type"),
            subject_id=_optional_text(record, "subject_id"),
            status=status,
            approval_status=ApprovalStatus(_required_text(record, "approval_status")),
            confidence=float(confidence),
            sensitivity=Sensitivity(_required_text(record, "sensitivity")),
            scope=Scope(
                type=ScopeType(_required_text(record, "scope_type")),
                id=_optional_text(record, "scope_id"),
            ),
            effective_from=_parse_timestamp(
                record.get("effective_from"), field_name="effective_from"
            ),
            effective_to=_parse_timestamp(
                record.get("effective_to"), field_name="effective_to"
            ),
            source_type=SourceType(_required_text(record, "source_type")),
            source_ref=_optional_text(record, "source_ref"),
            evidence_ids=evidence_ids,
            verified_at=_parse_timestamp(record.get("verified_at"), field_name="verified_at"),
            verified_by=_optional_text(record, "verified_by"),
            derivation=derivation,
            supersedes_id=_optional_text(record, "supersedes_id"),
            created_at=_required_timestamp(record, "created_at"),
            updated_at=_required_timestamp(record, "updated_at"),
        )

    def _evidence_for_claim(self, claim_id: str) -> tuple[Evidence, ...]:
        result: list[Evidence] = []
        links = self._repository.list_claim_evidence(
            claim_id=claim_id,
            relationship="supports",
        )
        for link in links:
            evidence_id = _required_text(link, "evidence_id")
            record = self._repository.get_evidence(evidence_id)
            if record is None:
                raise RecordNotFoundError(f"Evidence does not exist: {evidence_id}")
            locator = _json_value(record.get("locator_json"), field_name="locator_json")
            result.append(
                Evidence(
                    id=_required_text(record, "id"),
                    claim_id=claim_id,
                    source_type=SourceType(_required_text(record, "source_type")),
                    source_ref=_required_text(record, "source_ref"),
                    artifact_id=_optional_text(record, "artifact_id"),
                    locator=locator,
                    source_text=_optional_text(record, "source_text"),
                    extraction_method=_optional_text(record, "extraction_method"),
                    captured_at=_required_timestamp(record, "captured_at"),
                    checksum=_optional_text(record, "checksum_sha256"),
                    confirmation_status=EvidenceConfirmationStatus(
                        _required_text(record, "confirmation_status")
                    ),
                )
            )
        return tuple(result)
