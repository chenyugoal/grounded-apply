"""Pure policies for filtering and resolving candidate claims.

These functions perform no persistence, I/O, or clock reads. Callers provide an
explicit ``as_of`` timestamp through :class:`ClaimUsePolicy`, making a decision
reproducible and safe to retry.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from .enums import (
    ApprovalStatus,
    ClaimRejectionReason,
    ClaimStatus,
    EvidenceConfirmationStatus,
    NeedInfoAction,
    NeedInfoReason,
    ReusePolicy,
    Sensitivity,
)
from .models import (
    Claim,
    ClaimPacket,
    Contradiction,
    Evidence,
    FilteredClaims,
    NeedInfo,
    RejectedClaim,
    ResolutionOutcome,
    Resolved,
    Scope,
)

_ELIGIBLE_STATUSES = frozenset({ClaimStatus.VERIFIED})
_IGNORABLE_HISTORICAL_STATUSES = frozenset(
    {ClaimStatus.SUPERSEDED, ClaimStatus.WITHDRAWN}
)


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("ClaimUsePolicy.as_of must be timezone-aware")


@dataclass(frozen=True, slots=True, kw_only=True)
class ClaimUsePolicy:
    """Explicit authority and context for one resolution decision.

    The safe default authorizes public claims only. Non-public claims require
    either their sensitivity class in ``allowed_sensitivities`` or their exact
    id in ``authorized_claim_ids``. The latter supports one-time consent without
    granting broad access to a category of private data.
    """

    as_of: datetime
    requested_scope: Scope = field(default_factory=Scope)
    allowed_sensitivities: frozenset[Sensitivity] = frozenset({Sensitivity.PUBLIC})
    authorized_claim_ids: frozenset[str] = frozenset()
    require_confirmed_evidence: bool = False

    def __post_init__(self) -> None:
        _require_aware(self.as_of)
        if any(not item.strip() for item in self.authorized_claim_ids):
            raise ValueError("authorized_claim_ids must not contain blank ids")

    def authorizes(self, claim: Claim) -> bool:
        return (
            claim.sensitivity in self.allowed_sensitivities
            or claim.id in self.authorized_claim_ids
        )


def _confirmed_evidence_claim_ids(evidence: Iterable[Evidence]) -> frozenset[str]:
    return frozenset(
        item.claim_id
        for item in evidence
        if item.confirmation_status is EvidenceConfirmationStatus.CONFIRMED
    )


def _is_current(claim: Claim, policy: ClaimUsePolicy) -> bool:
    return not (
        (claim.effective_from is not None and policy.as_of < claim.effective_from)
        or (claim.effective_to is not None and policy.as_of >= claim.effective_to)
    )


def _support_rejection(
    claim: Claim,
    *,
    claims_by_id: dict[str, Claim],
    policy: ClaimUsePolicy,
    confirmed_claim_ids: frozenset[str],
    visiting: frozenset[str],
) -> tuple[ClaimRejectionReason | None, tuple[str, ...]]:
    """Validate a derived input and its transitive inputs."""

    if claim.id in visiting:
        return ClaimRejectionReason.INVALID_DERIVATION, (claim.id,)
    if not claim.scope.permits(policy.requested_scope):
        return ClaimRejectionReason.OUT_OF_SCOPE, (claim.id,)
    if claim.status is ClaimStatus.DERIVED:
        return ClaimRejectionReason.INVALID_DERIVATION, (claim.id,)
    if claim.status not in _ELIGIBLE_STATUSES:
        return ClaimRejectionReason.UNAPPROVED, (claim.id,)
    if claim.approval_status is not ApprovalStatus.APPROVED:
        return ClaimRejectionReason.UNAPPROVED, (claim.id,)
    if claim.effective_from is not None and policy.as_of < claim.effective_from:
        return ClaimRejectionReason.NOT_YET_EFFECTIVE, (claim.id,)
    if claim.effective_to is not None and policy.as_of >= claim.effective_to:
        return ClaimRejectionReason.STALE, (claim.id,)
    if not policy.authorizes(claim):
        return ClaimRejectionReason.SENSITIVE, (claim.id,)

    if claim.status is ClaimStatus.DERIVED:
        # Claim construction guarantees this, but retaining the guard keeps the
        # resolver fail-closed if an object arrives from an unsafe decoder.
        if claim.derivation is None:
            return ClaimRejectionReason.INVALID_DERIVATION, (claim.id,)
        next_visiting = visiting | {claim.id}
        for input_id in claim.derivation.input_claim_ids:
            input_claim = claims_by_id.get(input_id)
            if input_claim is None:
                return ClaimRejectionReason.INVALID_DERIVATION, (input_id,)
            rejection, blocking_ids = _support_rejection(
                input_claim,
                claims_by_id=claims_by_id,
                policy=policy,
                confirmed_claim_ids=confirmed_claim_ids,
                visiting=next_visiting,
            )
            if rejection is not None:
                return rejection, blocking_ids
        return None, ()

    if policy.require_confirmed_evidence and claim.id not in confirmed_claim_ids:
        return ClaimRejectionReason.MISSING_EVIDENCE, (claim.id,)
    return None, ()


def filter_claims(
    claims: Iterable[Claim],
    *,
    intent: str,
    policy: ClaimUsePolicy,
    evidence: Iterable[Evidence] = (),
) -> FilteredClaims:
    """Partition claims according to deterministic, fail-closed use rules.

    Only approved, in-scope, currently effective verified/derived claims are
    accepted. Effective intervals are half-open: ``[effective_from,
    effective_to)``. Rejections record the first policy boundary encountered,
    in a stable order chosen to avoid disclosing sensitive values.
    """

    if not intent or not intent.strip():
        raise ValueError("intent must not be blank")

    all_claims = tuple(claims)
    confirmed_claim_ids = _confirmed_evidence_claim_ids(evidence)
    claims_by_id = {claim.id: claim for claim in all_claims}
    if len(claims_by_id) != len(all_claims):
        raise ValueError("claims must have unique ids")
    accepted: list[Claim] = []
    rejected: list[RejectedClaim] = []

    for claim in all_claims:
        reason: ClaimRejectionReason | None = None
        blocking_claim_ids: tuple[str, ...] = ()
        if claim.claim_type != intent:
            reason = ClaimRejectionReason.TYPE_MISMATCH
        elif not claim.scope.permits(policy.requested_scope):
            reason = ClaimRejectionReason.OUT_OF_SCOPE
        elif claim.status is ClaimStatus.DERIVED:
            reason = ClaimRejectionReason.INVALID_DERIVATION
        elif claim.status not in _ELIGIBLE_STATUSES:
            reason = ClaimRejectionReason.INELIGIBLE_STATUS
        elif claim.approval_status is not ApprovalStatus.APPROVED:
            reason = ClaimRejectionReason.UNAPPROVED
        elif claim.effective_from is not None and policy.as_of < claim.effective_from:
            reason = ClaimRejectionReason.NOT_YET_EFFECTIVE
        elif claim.effective_to is not None and policy.as_of >= claim.effective_to:
            reason = ClaimRejectionReason.STALE
        elif not policy.authorizes(claim):
            reason = ClaimRejectionReason.SENSITIVE
        elif policy.require_confirmed_evidence and claim.id not in confirmed_claim_ids:
            reason = ClaimRejectionReason.MISSING_EVIDENCE

        if reason is None:
            accepted.append(claim)
        else:
            rejected.append(
                RejectedClaim(
                    claim=claim,
                    reason=reason,
                    blocking_claim_ids=blocking_claim_ids or (claim.id,),
                )
            )

    return FilteredClaims(accepted=tuple(accepted), rejected=tuple(rejected))


def _value_key(claim: Claim) -> str:
    """Return a stable equality key without coercing JSON scalar types."""

    return json.dumps(
        claim.value_json,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _default_question(intent: str) -> str:
    return f"Please provide or confirm the information for {intent}."


def _highest_sensitivity(claims: Iterable[Claim], fallback: Sensitivity) -> Sensitivity:
    ranked = tuple(claims)
    if not ranked:
        return fallback
    order = tuple(Sensitivity)
    return max((claim.sensitivity for claim in ranked), key=order.index)


def _blocking_records(
    filtered: FilteredClaims,
    reason: ClaimRejectionReason,
    in_scope_ids: frozenset[str],
) -> tuple[RejectedClaim, ...]:
    return tuple(
        item
        for item in filtered.rejected
        if item.reason is reason and item.claim.id in in_scope_ids
    )


def _related_ids(records: Iterable[RejectedClaim]) -> tuple[str, ...]:
    result: list[str] = []
    for item in records:
        for claim_id in item.blocking_claim_ids or (item.claim.id,):
            if claim_id not in result:
                result.append(claim_id)
    return tuple(result)


def _collect_supporting_claims(
    accepted: tuple[Claim, ...], claims_by_id: dict[str, Claim]
) -> tuple[Claim, ...]:
    """Collect validated transitive derivation inputs in stable depth-first order."""

    primary_ids = {claim.id for claim in accepted}
    collected: list[Claim] = []
    collected_ids: set[str] = set()

    def visit(claim: Claim) -> None:
        if claim.derivation is None:
            return
        for input_id in claim.derivation.input_claim_ids:
            input_claim = claims_by_id[input_id]
            if input_id not in primary_ids and input_id not in collected_ids:
                collected.append(input_claim)
                collected_ids.add(input_id)
            visit(input_claim)

    for accepted_claim in accepted:
        visit(accepted_claim)
    return tuple(collected)


def _need_info(
    *,
    intent: str,
    question: str,
    reason: NeedInfoReason,
    sensitivity: Sensitivity,
    requested_scope: Scope,
    related_claim_ids: tuple[str, ...],
    detail: str,
    reuse_policy: ReusePolicy,
) -> NeedInfo:
    actions = (
        (NeedInfoAction.CONFIRM, NeedInfoAction.ANSWER_ONCE, NeedInfoAction.SKIP)
        if reason
        in {
            NeedInfoReason.STALE,
            NeedInfoReason.NOT_YET_EFFECTIVE,
            NeedInfoReason.SENSITIVE,
            NeedInfoReason.UNAPPROVED,
        }
        else (
            NeedInfoAction.ANSWER_ONCE,
            NeedInfoAction.ANSWER_AND_REMEMBER,
            NeedInfoAction.SKIP,
        )
    )
    return NeedInfo(
        intent=intent,
        question=question,
        reason=reason,
        sensitivity=sensitivity,
        requested_scope=requested_scope,
        reuse_policy=reuse_policy,
        allowed_actions=actions,
        related_claim_ids=related_claim_ids,
        detail=detail,
    )


def resolve_claims(
    claims: Iterable[Claim],
    *,
    intent: str,
    policy: ClaimUsePolicy,
    evidence: Iterable[Evidence] = (),
    question: str | None = None,
    requested_sensitivity: Sensitivity = Sensitivity.PERSONAL,
    reuse_policy: ReusePolicy = ReusePolicy.CONFIRM,
) -> ResolutionOutcome:
    """Resolve one intent to a traceable packet or an explicit safe stop.

    Resolution never ranks incompatible values. Unknown, contradicted, stale,
    sensitive, unapproved, or unsupported states return a blocking outcome.
    Historical superseded/withdrawn claims and temporally inactive claims do not
    poison a current approved value. An explicitly contradicted claim blocks
    only while it is effective; its outcome contains all active, in-scope
    non-historical candidates so the opposing claim cannot be hidden.
    """

    all_claims = tuple(claims)
    all_evidence = tuple(evidence)
    prompt = question or _default_question(intent)
    filtered = filter_claims(
        all_claims,
        intent=intent,
        policy=policy,
        evidence=all_evidence,
    )

    matching = tuple(claim for claim in all_claims if claim.claim_type == intent)
    if not matching:
        return _need_info(
            intent=intent,
            question=prompt,
            reason=NeedInfoReason.MISSING,
            sensitivity=requested_sensitivity,
            requested_scope=policy.requested_scope,
            related_claim_ids=(),
            detail="No claim exists for the requested intent.",
            reuse_policy=reuse_policy,
        )

    in_scope = tuple(
        claim for claim in matching if claim.scope.permits(policy.requested_scope)
    )
    if not in_scope:
        return _need_info(
            intent=intent,
            question=prompt,
            reason=NeedInfoReason.OUT_OF_SCOPE,
            sensitivity=requested_sensitivity,
            requested_scope=policy.requested_scope,
            related_claim_ids=tuple(claim.id for claim in matching),
            detail="Claims exist, but none is authorized for the requested scope.",
            reuse_policy=reuse_policy,
        )

    in_scope_ids = frozenset(claim.id for claim in in_scope)
    claims_by_id = {claim.id: claim for claim in all_claims}
    active_in_scope = tuple(claim for claim in in_scope if _is_current(claim, policy))

    # Authorization is checked before returning any claim-bearing outcome. This
    # prevents a contradiction payload from disclosing an unauthorized value.
    directly_sensitive = tuple(
        claim
        for claim in active_in_scope
        if claim.status not in _IGNORABLE_HISTORICAL_STATUSES
        and not policy.authorizes(claim)
    )
    derived_sensitive_records = _blocking_records(
        filtered, ClaimRejectionReason.SENSITIVE, in_scope_ids
    )
    if directly_sensitive or derived_sensitive_records:
        related_ids = list(claim.id for claim in directly_sensitive)
        for claim_id in _related_ids(derived_sensitive_records):
            if claim_id not in related_ids:
                related_ids.append(claim_id)
        sensitive_claims = tuple(
            claims_by_id[claim_id]
            for claim_id in related_ids
            if claim_id in claims_by_id
        )
        return _need_info(
            intent=intent,
            question=prompt,
            reason=NeedInfoReason.SENSITIVE,
            sensitivity=_highest_sensitivity(sensitive_claims, requested_sensitivity),
            requested_scope=policy.requested_scope,
            related_claim_ids=tuple(related_ids),
            detail="An active claim or derivation input requires explicit authorization.",
            reuse_policy=reuse_policy,
        )

    contradicted = tuple(
        claim
        for claim in active_in_scope
        if claim.status is ClaimStatus.CONTRADICTED
    )
    if contradicted:
        conflict_context = tuple(
            claim
            for claim in active_in_scope
            if claim.status not in _IGNORABLE_HISTORICAL_STATUSES
        )
        return Contradiction(
            intent=intent,
            question=prompt,
            requested_scope=policy.requested_scope,
            conflicting_claims=conflict_context,
            detail=(
                "An active in-scope claim is explicitly marked contradicted; "
                "all active opposing candidates are included for review."
            ),
        )

    for rejection_reason, need_reason, detail in (
        (
            ClaimRejectionReason.UNAPPROVED,
            NeedInfoReason.UNAPPROVED,
            "An in-scope claim has not been approved for use.",
        ),
        (
            ClaimRejectionReason.MISSING_EVIDENCE,
            NeedInfoReason.UNSUPPORTED,
            "Confirmed evidence is required but was not supplied.",
        ),
        (
            ClaimRejectionReason.INVALID_DERIVATION,
            NeedInfoReason.UNSUPPORTED,
            "Derived claims require a registered evaluator that recomputes their output.",
        ),
        (
            ClaimRejectionReason.OUT_OF_SCOPE,
            NeedInfoReason.OUT_OF_SCOPE,
            "A derived claim depends on an input outside the requested scope.",
        ),
    ):
        blocked_records = _blocking_records(
            filtered, rejection_reason, in_scope_ids
        )
        if blocked_records:
            blocking_ids = _related_ids(blocked_records)
            blocking_claims = tuple(
                claims_by_id[claim_id]
                for claim_id in blocking_ids
                if claim_id in claims_by_id
            )
            return _need_info(
                intent=intent,
                question=prompt,
                reason=need_reason,
                sensitivity=_highest_sensitivity(blocking_claims, requested_sensitivity),
                requested_scope=policy.requested_scope,
                related_claim_ids=blocking_ids,
                detail=detail,
                reuse_policy=reuse_policy,
            )

    accepted = filtered.accepted
    if accepted:
        groups: dict[str, list[Claim]] = {}
        for claim in accepted:
            groups.setdefault(_value_key(claim), []).append(claim)
        if len(groups) > 1:
            return Contradiction(
                intent=intent,
                question=prompt,
                requested_scope=policy.requested_scope,
                conflicting_claims=accepted,
                detail="Multiple approved, current claims contain incompatible values.",
            )

        supporting_claims = _collect_supporting_claims(accepted, claims_by_id)
        packet_ids = {claim.id for claim in accepted + supporting_claims}
        packet_evidence = tuple(
            item
            for item in all_evidence
            if item.claim_id in packet_ids
            and item.confirmation_status is EvidenceConfirmationStatus.CONFIRMED
        )
        return Resolved(
            packet=ClaimPacket(
                intent=intent,
                claims=accepted,
                supporting_claims=supporting_claims,
                evidence=packet_evidence,
            )
        )

    directly_stale = tuple(
        claim
        for claim in in_scope
        if claim.status not in _IGNORABLE_HISTORICAL_STATUSES
        and claim.effective_to is not None
        and policy.as_of >= claim.effective_to
    )
    stale_records = _blocking_records(
        filtered, ClaimRejectionReason.STALE, in_scope_ids
    )
    if directly_stale or stale_records:
        stale_ids = list(claim.id for claim in directly_stale)
        for claim_id in _related_ids(stale_records):
            if claim_id not in stale_ids:
                stale_ids.append(claim_id)
        stale_claims = tuple(
            claims_by_id[claim_id]
            for claim_id in stale_ids
            if claim_id in claims_by_id
        )
        return _need_info(
            intent=intent,
            question=prompt,
            reason=NeedInfoReason.STALE,
            sensitivity=_highest_sensitivity(stale_claims, requested_sensitivity),
            requested_scope=policy.requested_scope,
            related_claim_ids=tuple(stale_ids),
            detail="The available claim or one of its derivation inputs is no longer effective.",
            reuse_policy=reuse_policy,
        )

    directly_future = tuple(
        claim
        for claim in in_scope
        if claim.status not in _IGNORABLE_HISTORICAL_STATUSES
        and claim.effective_from is not None
        and policy.as_of < claim.effective_from
    )
    future_records = _blocking_records(
        filtered, ClaimRejectionReason.NOT_YET_EFFECTIVE, in_scope_ids
    )
    if directly_future or future_records:
        future_ids = list(claim.id for claim in directly_future)
        for claim_id in _related_ids(future_records):
            if claim_id not in future_ids:
                future_ids.append(claim_id)
        future_claims = tuple(
            claims_by_id[claim_id]
            for claim_id in future_ids
            if claim_id in claims_by_id
        )
        return _need_info(
            intent=intent,
            question=prompt,
            reason=NeedInfoReason.NOT_YET_EFFECTIVE,
            sensitivity=_highest_sensitivity(future_claims, requested_sensitivity),
            requested_scope=policy.requested_scope,
            related_claim_ids=tuple(future_ids),
            detail="The available claim or one of its derivation inputs is not effective yet.",
            reuse_policy=reuse_policy,
        )

    pending = tuple(
        claim
        for claim in in_scope
        if claim.status not in _ELIGIBLE_STATUSES
        and claim.status not in _IGNORABLE_HISTORICAL_STATUSES
    )
    if pending:
        return _need_info(
            intent=intent,
            question=prompt,
            reason=NeedInfoReason.UNAPPROVED,
            sensitivity=_highest_sensitivity(pending, requested_sensitivity),
            requested_scope=policy.requested_scope,
            related_claim_ids=tuple(claim.id for claim in pending),
            detail="The available claim is not in a usable verified or derived state.",
            reuse_policy=reuse_policy,
        )

    return _need_info(
        intent=intent,
        question=prompt,
        reason=NeedInfoReason.MISSING,
        sensitivity=requested_sensitivity,
        requested_scope=policy.requested_scope,
        related_claim_ids=tuple(claim.id for claim in in_scope),
        detail="No approved, effective, supported claim is available.",
        reuse_policy=reuse_policy,
    )
