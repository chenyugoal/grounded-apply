"""Typed, dependency-free domain records for evidence-backed claims."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .enums import (
    ApprovalStatus,
    ClaimRejectionReason,
    ClaimStatus,
    EvidenceConfirmationStatus,
    MemoryProposalStatus,
    NeedInfoAction,
    NeedInfoReason,
    ResolutionKind,
    RetentionPolicy,
    ReusePolicy,
    ScopeType,
    Sensitivity,
    SourceType,
)

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


def utc_now() -> datetime:
    """Return an aware UTC timestamp for record-construction defaults."""

    return datetime.now(UTC)


def _require_text(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _require_aware(value: datetime | None, field_name: str) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError(f"{field_name} must be timezone-aware")


def _validate_json(value: JsonValue, path: str = "value_json") -> None:
    """Reject objects that cannot be represented faithfully in JSON."""

    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must not contain NaN or infinity")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} object keys must be strings")
            _validate_json(item, f"{path}.{key}")
        return
    raise TypeError(f"{path} contains a non-JSON value: {type(value).__name__}")


@dataclass(frozen=True, slots=True, kw_only=True)
class Scope:
    """A reuse boundary for a claim or answer.

    Global records can be considered in any requested scope. Every narrower
    scope must match both the requested scope type and identifier exactly. A
    caller that knows hierarchy (for example, an application belonging to a
    company) should make that context explicit before invoking resolution.
    """

    type: ScopeType = ScopeType.GLOBAL
    id: str | None = None

    def __post_init__(self) -> None:
        if self.type is ScopeType.GLOBAL:
            if self.id is not None:
                raise ValueError("global scope must not have an id")
            return
        if self.id is None or not self.id.strip():
            raise ValueError(f"{self.type.value} scope requires a non-blank id")

    def permits(self, requested: Scope) -> bool:
        """Return whether this stored scope may be used in ``requested``."""

        return self.type is ScopeType.GLOBAL or self == requested


@dataclass(frozen=True, slots=True, kw_only=True)
class Derivation:
    """Provenance for a value produced by a named deterministic rule."""

    rule_name: str
    rule_version: str
    input_claim_ids: tuple[str, ...]
    staleness_policy: str = "inputs_effective_window"
    calculated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _require_text(self.rule_name, "rule_name")
        _require_text(self.rule_version, "rule_version")
        _require_text(self.staleness_policy, "staleness_policy")
        if not self.input_claim_ids or any(not item.strip() for item in self.input_claim_ids):
            raise ValueError("input_claim_ids must contain non-blank claim ids")
        if len(set(self.input_claim_ids)) != len(self.input_claim_ids):
            raise ValueError("input_claim_ids must be unique")
        _require_aware(self.calculated_at, "calculated_at")


@dataclass(frozen=True, slots=True, kw_only=True)
class Evidence:
    """An exact source location supporting one atomic claim."""

    id: str
    claim_id: str
    source_type: SourceType
    source_ref: str
    artifact_id: str | None = None
    locator: JsonValue = None
    source_text: str | None = None
    extraction_method: str | None = None
    captured_at: datetime = field(default_factory=utc_now)
    checksum: str | None = None
    confirmation_status: EvidenceConfirmationStatus = EvidenceConfirmationStatus.PENDING

    def __post_init__(self) -> None:
        _require_text(self.id, "id")
        _require_text(self.claim_id, "claim_id")
        _require_text(self.source_ref, "source_ref")
        _validate_json(self.locator, "locator")
        _require_aware(self.captured_at, "captured_at")


@dataclass(frozen=True, slots=True, kw_only=True)
class Claim:
    """One atomic assertion that may be used in application material."""

    id: str
    claim_type: str
    value_json: JsonValue
    canonical_text: str
    subject_type: str = "person"
    subject_id: str | None = None
    status: ClaimStatus = ClaimStatus.NEEDS_REVIEW
    approval_status: ApprovalStatus = ApprovalStatus.PENDING
    confidence: float = 1.0
    sensitivity: Sensitivity = Sensitivity.PERSONAL
    scope: Scope = field(default_factory=Scope)
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    source_type: SourceType = SourceType.OTHER
    source_ref: str | None = None
    evidence_ids: tuple[str, ...] = ()
    verified_at: datetime | None = None
    verified_by: str | None = None
    derivation: Derivation | None = None
    supersedes_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _require_text(self.id, "id")
        _require_text(self.claim_type, "claim_type")
        _require_text(self.canonical_text, "canonical_text")
        _require_text(self.subject_type, "subject_type")
        if not 0.0 <= self.confidence <= 1.0 or not math.isfinite(self.confidence):
            raise ValueError("confidence must be a finite value from 0.0 through 1.0")
        _validate_json(self.value_json)
        for field_name, value in (
            ("effective_from", self.effective_from),
            ("effective_to", self.effective_to),
            ("verified_at", self.verified_at),
            ("created_at", self.created_at),
            ("updated_at", self.updated_at),
        ):
            _require_aware(value, field_name)
        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_from >= self.effective_to
        ):
            raise ValueError("effective_from must be earlier than effective_to")
        if any(not item.strip() for item in self.evidence_ids):
            raise ValueError("evidence_ids must not contain blank ids")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("evidence_ids must be unique")
        if self.status is ClaimStatus.DERIVED and self.derivation is None:
            raise ValueError("a derived claim requires derivation provenance")
        if self.status is not ClaimStatus.DERIVED and self.derivation is not None:
            raise ValueError("derivation provenance is only valid for derived claims")
        if self.status is ClaimStatus.VERIFIED and self.verified_at is None:
            raise ValueError("a verified claim requires verified_at")


@dataclass(frozen=True, slots=True, kw_only=True)
class ClaimPacket:
    """The complete, traceable input from which prose may be generated."""

    intent: str
    claims: tuple[Claim, ...]
    supporting_claims: tuple[Claim, ...] = ()
    evidence: tuple[Evidence, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.intent, "intent")
        if not self.claims:
            raise ValueError("a ClaimPacket requires at least one claim")
        claim_ids = tuple(claim.id for claim in self.claims)
        if len(set(claim_ids)) != len(claim_ids):
            raise ValueError("ClaimPacket claims must be unique")
        if any(claim.claim_type != self.intent for claim in self.claims):
            raise ValueError("every ClaimPacket claim must match its intent")
        if any(
            claim.status is not ClaimStatus.VERIFIED
            or claim.approval_status is not ApprovalStatus.APPROVED
            for claim in self.claims + self.supporting_claims
        ):
            raise ValueError(
                "ClaimPacket claims must be approved verified claims; derived claims "
                "require a registered evaluator"
            )
        value_keys = {
            json.dumps(
                claim.value_json,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            for claim in self.claims
        }
        if len(value_keys) != 1:
            raise ValueError("ClaimPacket claims must contain compatible values")
        supporting_ids = tuple(claim.id for claim in self.supporting_claims)
        if len(set(supporting_ids)) != len(supporting_ids):
            raise ValueError("ClaimPacket supporting_claims must be unique")
        if set(claim_ids) & set(supporting_ids):
            raise ValueError("primary and supporting ClaimPacket claims must not overlap")
        all_claim_ids = set(claim_ids) | set(supporting_ids)
        for claim in self.claims + self.supporting_claims:
            if claim.derivation is not None and not set(
                claim.derivation.input_claim_ids
            ).issubset(all_claim_ids):
                raise ValueError("ClaimPacket must include every derived claim input")
        if any(item.claim_id not in all_claim_ids for item in self.evidence):
            raise ValueError("ClaimPacket evidence must reference a packet claim")
        evidence_links = tuple((item.id, item.claim_id) for item in self.evidence)
        if len(set(evidence_links)) != len(evidence_links):
            raise ValueError("ClaimPacket evidence links must be unique")

    @property
    def claim_ids(self) -> tuple[str, ...]:
        return tuple(claim.id for claim in self.claims)

    @property
    def supporting_claim_ids(self) -> tuple[str, ...]:
        return tuple(claim.id for claim in self.supporting_claims)

    @property
    def value_json(self) -> JsonValue:
        """Return the compatible value shared by all packet claims."""

        return self.claims[0].value_json


@dataclass(frozen=True, slots=True, kw_only=True)
class NeedInfo:
    """A structured request emitted when policy cannot safely resolve a value."""

    intent: str
    question: str
    reason: NeedInfoReason
    sensitivity: Sensitivity
    requested_scope: Scope
    reuse_policy: ReusePolicy = ReusePolicy.CONFIRM
    allowed_actions: tuple[NeedInfoAction, ...] = (
        NeedInfoAction.ANSWER_ONCE,
        NeedInfoAction.ANSWER_AND_REMEMBER,
        NeedInfoAction.SKIP,
    )
    related_claim_ids: tuple[str, ...] = ()
    detail: str | None = None
    kind: ResolutionKind = field(default=ResolutionKind.NEED_INFO, init=False)

    def __post_init__(self) -> None:
        _require_text(self.intent, "intent")
        _require_text(self.question, "question")
        if not self.allowed_actions:
            raise ValueError("allowed_actions must not be empty")
        if len(set(self.allowed_actions)) != len(self.allowed_actions):
            raise ValueError("allowed_actions must be unique")
        if len(set(self.related_claim_ids)) != len(self.related_claim_ids):
            raise ValueError("related_claim_ids must be unique")


@dataclass(frozen=True, slots=True, kw_only=True)
class ProposedClaim:
    """One reviewable atomic memory extracted from a user-written answer."""

    claim_type: str
    value_json: JsonValue
    canonical_text: str
    scope: Scope
    sensitivity: Sensitivity
    reuse_policy: ReusePolicy
    retention_policy: RetentionPolicy
    linked_entity_type: str | None = None
    linked_entity_id: str | None = None
    supporting_claim_ids: tuple[str, ...] = ()
    conflicts_with_claim_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.claim_type, "claim_type")
        _require_text(self.canonical_text, "canonical_text")
        _validate_json(self.value_json)
        if (self.linked_entity_type is None) != (self.linked_entity_id is None):
            raise ValueError("linked entity type and id must be supplied together")
        if len(set(self.supporting_claim_ids)) != len(self.supporting_claim_ids):
            raise ValueError("supporting_claim_ids must be unique")
        if len(set(self.conflicts_with_claim_ids)) != len(self.conflicts_with_claim_ids):
            raise ValueError("conflicts_with_claim_ids must be unique")


@dataclass(frozen=True, slots=True, kw_only=True)
class MemoryProposal:
    """A user-reviewable plan for retaining all or part of a manual answer."""

    id: str
    normalized_intent: str
    question_text: str
    verbatim_answer: str
    proposed_claims: tuple[ProposedClaim, ...]
    answer_scope: Scope
    answer_sensitivity: Sensitivity
    answer_reuse_policy: ReusePolicy
    answer_retention_policy: RetentionPolicy
    existing_memory_links: tuple[str, ...] = ()
    contradiction_claim_ids: tuple[str, ...] = ()
    future_reuse_preview: str | None = None
    status: MemoryProposalStatus = MemoryProposalStatus.PENDING
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _require_text(self.id, "id")
        _require_text(self.normalized_intent, "normalized_intent")
        _require_text(self.question_text, "question_text")
        _require_text(self.verbatim_answer, "verbatim_answer")
        _require_aware(self.created_at, "created_at")
        if len(set(self.existing_memory_links)) != len(self.existing_memory_links):
            raise ValueError("existing_memory_links must be unique")
        if len(set(self.contradiction_claim_ids)) != len(self.contradiction_claim_ids):
            raise ValueError("contradiction_claim_ids must be unique")


@dataclass(frozen=True, slots=True, kw_only=True)
class Resolved:
    """Successful resolution containing only policy-approved claims."""

    packet: ClaimPacket
    kind: ResolutionKind = field(default=ResolutionKind.RESOLVED, init=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class Contradiction:
    """Mutually incompatible active claims; callers must not choose silently."""

    intent: str
    question: str
    requested_scope: Scope
    conflicting_claims: tuple[Claim, ...]
    detail: str | None = None
    kind: ResolutionKind = field(default=ResolutionKind.CONTRADICTION, init=False)

    def __post_init__(self) -> None:
        _require_text(self.intent, "intent")
        _require_text(self.question, "question")
        if not self.conflicting_claims:
            raise ValueError("Contradiction requires at least one conflicting claim")
        if any(item.claim_type != self.intent for item in self.conflicting_claims):
            raise ValueError("every conflicting claim must match the contradiction intent")

    @property
    def conflicting_claim_ids(self) -> tuple[str, ...]:
        return tuple(claim.id for claim in self.conflicting_claims)


type ResolutionOutcome = Resolved | NeedInfo | Contradiction


@dataclass(frozen=True, slots=True, kw_only=True)
class RejectedClaim:
    """A claim and the single first-order reason policy excluded it."""

    claim: Claim
    reason: ClaimRejectionReason
    blocking_claim_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if len(set(self.blocking_claim_ids)) != len(self.blocking_claim_ids):
            raise ValueError("blocking_claim_ids must be unique")


@dataclass(frozen=True, slots=True, kw_only=True)
class FilteredClaims:
    """Auditable result of applying a :class:`ClaimUsePolicy`."""

    accepted: tuple[Claim, ...]
    rejected: tuple[RejectedClaim, ...]

    @property
    def rejected_by_reason(self) -> dict[ClaimRejectionReason, tuple[Claim, ...]]:
        grouped: dict[ClaimRejectionReason, list[Claim]] = {}
        for item in self.rejected:
            grouped.setdefault(item.reason, []).append(item.claim)
        return {reason: tuple(claims) for reason, claims in grouped.items()}
