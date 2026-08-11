from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from grounded_apply.domain import (
    ApprovalStatus,
    Claim,
    ClaimStatus,
    ClaimUsePolicy,
    Contradiction,
    Derivation,
    Evidence,
    EvidenceConfirmationStatus,
    NeedInfo,
    NeedInfoReason,
    Resolved,
    Scope,
    ScopeType,
    Sensitivity,
    SourceType,
    resolve_claims,
)


AS_OF = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


def claim(
    *,
    claim_id: str = "claim-1",
    value: object = "Python",
    sensitivity: Sensitivity = Sensitivity.PUBLIC,
    scope: Scope | None = None,
    approval: ApprovalStatus = ApprovalStatus.APPROVED,
    effective_to: datetime | None = None,
) -> Claim:
    return Claim(
        id=claim_id,
        claim_type="skill_use",
        value_json=value,  # type: ignore[arg-type]
        canonical_text=f"Used {value}",
        status=ClaimStatus.VERIFIED,
        approval_status=approval,
        sensitivity=sensitivity,
        scope=scope or Scope(),
        effective_to=effective_to,
        source_type=SourceType.USER_STATEMENT,
        source_ref="synthetic onboarding answer",
        verified_at=AS_OF - timedelta(days=1),
        verified_by="synthetic-user",
    )


class ClaimResolutionTests(unittest.TestCase):
    def policy(self, **overrides: object) -> ClaimUsePolicy:
        values: dict[str, object] = {"as_of": AS_OF}
        values.update(overrides)
        return ClaimUsePolicy(**values)  # type: ignore[arg-type]

    def test_missing_fact_becomes_need_info(self) -> None:
        result = resolve_claims((), intent="skill_use", policy=self.policy())

        self.assertIsInstance(result, NeedInfo)
        self.assertEqual(result.reason, NeedInfoReason.MISSING)
        self.assertEqual(result.related_claim_ids, ())

    def test_sensitive_fact_requires_explicit_authorization(self) -> None:
        sensitive = claim(sensitivity=Sensitivity.HIGHLY_SENSITIVE)

        result = resolve_claims((sensitive,), intent="skill_use", policy=self.policy())

        self.assertIsInstance(result, NeedInfo)
        self.assertEqual(result.reason, NeedInfoReason.SENSITIVE)
        self.assertEqual(result.related_claim_ids, (sensitive.id,))

    def test_exact_claim_authorization_does_not_grant_category_access(self) -> None:
        authorized = claim(claim_id="allowed", sensitivity=Sensitivity.CONFIDENTIAL)
        unauthorized = claim(claim_id="blocked", sensitivity=Sensitivity.CONFIDENTIAL)
        policy = self.policy(authorized_claim_ids=frozenset({authorized.id}))

        first = resolve_claims((authorized,), intent="skill_use", policy=policy)
        second = resolve_claims((unauthorized,), intent="skill_use", policy=policy)

        self.assertIsInstance(first, Resolved)
        self.assertIsInstance(second, NeedInfo)
        self.assertEqual(second.reason, NeedInfoReason.SENSITIVE)

    def test_stale_fact_is_not_resolved(self) -> None:
        stale = claim(effective_to=AS_OF)

        result = resolve_claims((stale,), intent="skill_use", policy=self.policy())

        self.assertIsInstance(result, NeedInfo)
        self.assertEqual(result.reason, NeedInfoReason.STALE)

    def test_incompatible_approved_values_are_a_contradiction(self) -> None:
        claims = (claim(claim_id="python"), claim(claim_id="rust", value="Rust"))

        result = resolve_claims(claims, intent="skill_use", policy=self.policy())

        self.assertIsInstance(result, Contradiction)
        self.assertEqual(set(result.conflicting_claim_ids), {"python", "rust"})

    def test_confirmed_evidence_policy_fails_closed(self) -> None:
        supported = claim()
        evidence = Evidence(
            id="evidence-1",
            claim_id=supported.id,
            source_type=SourceType.IMPORTED_RESUME,
            source_ref="synthetic-resume.txt#line=4",
            source_text="Python",
            confirmation_status=EvidenceConfirmationStatus.PENDING,
        )
        policy = self.policy(require_confirmed_evidence=True)

        pending = resolve_claims(
            (supported,), intent="skill_use", policy=policy, evidence=(evidence,)
        )
        confirmed = resolve_claims(
            (supported,),
            intent="skill_use",
            policy=policy,
            evidence=(
                Evidence(
                    id=evidence.id,
                    claim_id=evidence.claim_id,
                    source_type=evidence.source_type,
                    source_ref=evidence.source_ref,
                    source_text=evidence.source_text,
                    confirmation_status=EvidenceConfirmationStatus.CONFIRMED,
                ),
            ),
        )

        self.assertIsInstance(pending, NeedInfo)
        self.assertEqual(pending.reason, NeedInfoReason.UNSUPPORTED)
        self.assertIsInstance(confirmed, Resolved)
        self.assertEqual(confirmed.packet.claim_ids, (supported.id,))

    def test_company_scoped_fact_cannot_escape_its_scope(self) -> None:
        acme_scope = Scope(type=ScopeType.COMPANY, id="acme-example")
        scoped = claim(scope=acme_scope)

        result = resolve_claims(
            (scoped,),
            intent="skill_use",
            policy=self.policy(requested_scope=Scope(type=ScopeType.COMPANY, id="other-example")),
        )

        self.assertIsInstance(result, NeedInfo)
        self.assertEqual(result.reason, NeedInfoReason.OUT_OF_SCOPE)

    def test_derived_claim_requires_named_provenance(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires derivation"):
            Claim(
                id="derived-without-rule",
                claim_type="experience_years",
                value_json=5,
                canonical_text="Five years of experience",
                status=ClaimStatus.DERIVED,
                approval_status=ApprovalStatus.APPROVED,
            )

        valid = Claim(
            id="derived-valid",
            claim_type="experience_years",
            value_json=5,
            canonical_text="Five years of experience",
            status=ClaimStatus.DERIVED,
            approval_status=ApprovalStatus.APPROVED,
            derivation=Derivation(
                rule_name="dated-experience-total",
                rule_version="1",
                input_claim_ids=("employment-start", "employment-end"),
                calculated_at=AS_OF,
            ),
        )

        self.assertEqual(valid.derivation.rule_name, "dated-experience-total")

    def test_verified_claim_requires_a_verification_timestamp(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires verified_at"):
            Claim(
                id="not-actually-verified",
                claim_type="skill_use",
                value_json="Python",
                canonical_text="Used Python",
                status=ClaimStatus.VERIFIED,
                approval_status=ApprovalStatus.APPROVED,
            )

    def test_unregistered_derived_claim_is_never_resolved(self) -> None:
        start = Claim(
            id="employment-start",
            claim_type="employment_date",
            value_json="2021-08-01",
            canonical_text="Started in August 2021",
            status=ClaimStatus.VERIFIED,
            approval_status=ApprovalStatus.APPROVED,
            sensitivity=Sensitivity.PERSONAL,
            verified_at=AS_OF - timedelta(days=1),
        )
        total = Claim(
            id="experience-total",
            claim_type="experience_years",
            value_json=5,
            canonical_text="Five years of experience",
            status=ClaimStatus.DERIVED,
            approval_status=ApprovalStatus.APPROVED,
            sensitivity=Sensitivity.PUBLIC,
            derivation=Derivation(
                rule_name="dated-experience-total",
                rule_version="1",
                input_claim_ids=(start.id,),
                calculated_at=AS_OF,
            ),
        )

        blocked = resolve_claims(
            (total, start), intent="experience_years", policy=self.policy()
        )
        allowed = resolve_claims(
            (total, start),
            intent="experience_years",
            policy=self.policy(
                allowed_sensitivities=frozenset(
                    {Sensitivity.PUBLIC, Sensitivity.PERSONAL}
                )
            ),
        )

        self.assertIsInstance(blocked, NeedInfo)
        self.assertEqual(blocked.reason, NeedInfoReason.UNSUPPORTED)
        self.assertIn(total.id, blocked.related_claim_ids)
        self.assertIsInstance(allowed, NeedInfo)
        self.assertEqual(allowed.reason, NeedInfoReason.UNSUPPORTED)

    def test_derived_claim_cannot_be_wrapped_in_a_packet_directly(self) -> None:
        from grounded_apply.domain import ClaimPacket

        derived = Claim(
            id="arbitrary-derived",
            claim_type="experience_years",
            value_json=999999,
            canonical_text="An invented total",
            status=ClaimStatus.DERIVED,
            approval_status=ApprovalStatus.APPROVED,
            sensitivity=Sensitivity.PUBLIC,
            derivation=Derivation(
                rule_name="arbitrary-unregistered-rule",
                rule_version="999",
                input_claim_ids=("unrelated-input",),
                calculated_at=AS_OF,
            ),
        )

        with self.assertRaisesRegex(ValueError, "registered evaluator"):
            ClaimPacket(intent="experience_years", claims=(derived,))

    def test_active_explicit_contradiction_includes_opposing_claim(self) -> None:
        contradicted = Claim(
            id="contradicted",
            claim_type="skill_use",
            value_json="Python",
            canonical_text="Used Python",
            status=ClaimStatus.CONTRADICTED,
            approval_status=ApprovalStatus.REJECTED,
            sensitivity=Sensitivity.PUBLIC,
        )
        opposing = claim(claim_id="opposing", value="Rust")

        result = resolve_claims(
            (contradicted, opposing), intent="skill_use", policy=self.policy()
        )

        self.assertIsInstance(result, Contradiction)
        self.assertEqual(
            set(result.conflicting_claim_ids), {contradicted.id, opposing.id}
        )

    def test_expired_contradiction_does_not_poison_current_fact(self) -> None:
        expired = Claim(
            id="expired-contradiction",
            claim_type="skill_use",
            value_json="Rust",
            canonical_text="Used Rust",
            status=ClaimStatus.CONTRADICTED,
            approval_status=ApprovalStatus.REJECTED,
            sensitivity=Sensitivity.PUBLIC,
            effective_to=AS_OF,
        )
        current = claim(claim_id="current", value="Python")

        result = resolve_claims(
            (expired, current), intent="skill_use", policy=self.policy()
        )

        self.assertIsInstance(result, Resolved)
        self.assertEqual(result.packet.claim_ids, (current.id,))


if __name__ == "__main__":
    unittest.main()
