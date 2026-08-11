from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from grounded_apply.domain import (
    ApprovalStatus,
    ClaimStatus,
    ClaimUsePolicy,
    Derivation,
    EvidenceConfirmationStatus,
    NeedInfo,
    NeedInfoReason,
    Resolved,
    Sensitivity,
    SourceType,
)
from grounded_apply.repositories import SQLiteRepository
from grounded_apply.services import CreateClaim, CreateEvidence, ProfileService


NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


class ProfileServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repository = SQLiteRepository(
            Path(self.directory.name) / "profile.db"
        ).initialize()
        self.addCleanup(self.repository.close)
        self.service = ProfileService(self.repository)

    def request(self, **overrides: object) -> CreateClaim:
        values: dict[str, object] = {
            "claim_type": "skill_use",
            "value": "Python",
            "canonical_text": "Used Python in a production service",
            "source_type": SourceType.USER_STATEMENT,
            "source_ref": "synthetic onboarding answer",
        }
        values.update(overrides)
        return CreateClaim(**values)  # type: ignore[arg-type]

    def test_safe_defaults_persist_but_do_not_resolve(self) -> None:
        stored = self.service.create_claim(self.request(), now=NOW)
        round_trip = self.service.get_claim(stored.id)

        result = self.service.resolve(
            intent="skill_use",
            policy=ClaimUsePolicy(
                as_of=NOW,
                allowed_sensitivities=frozenset(
                    {Sensitivity.PUBLIC, Sensitivity.PERSONAL}
                ),
            ),
        )

        self.assertEqual(round_trip.status, ClaimStatus.NEEDS_REVIEW)
        self.assertEqual(round_trip.approval_status, ApprovalStatus.PENDING)
        self.assertIsInstance(result, NeedInfo)
        self.assertEqual(result.reason, NeedInfoReason.UNAPPROVED)

    def test_verified_claim_requires_an_auditable_actor(self) -> None:
        with self.assertRaisesRegex(ValueError, "verified_by"):
            self.service.create_claim(
                self.request(
                    status=ClaimStatus.VERIFIED,
                    approval_status=ApprovalStatus.APPROVED,
                ),
                now=NOW,
            )

    def test_approved_public_claim_round_trips_and_resolves(self) -> None:
        stored = self.service.create_claim(
            self.request(
                status=ClaimStatus.VERIFIED,
                approval_status=ApprovalStatus.APPROVED,
                sensitivity=Sensitivity.PUBLIC,
                verified_by="synthetic-user",
            ),
            now=NOW,
        )

        result = self.service.resolve(
            intent="skill_use", policy=ClaimUsePolicy(as_of=NOW)
        )

        self.assertIsInstance(result, Resolved)
        self.assertEqual(result.packet.claim_ids, (stored.id,))

    def test_confirmed_evidence_round_trips_through_resolution(self) -> None:
        stored = self.service.create_claim(
            self.request(
                status=ClaimStatus.VERIFIED,
                approval_status=ApprovalStatus.APPROVED,
                sensitivity=Sensitivity.PUBLIC,
                verified_by="synthetic-user",
            ),
            now=NOW,
        )
        evidence = self.service.create_evidence(
            CreateEvidence(
                claim_id=stored.id,
                source_type=SourceType.IMPORTED_RESUME,
                source_ref="synthetic-resume.txt#line=7",
                source_text="Built a Python service for fictional test data.",
                locator={"line": 7},
                confirmation_status=EvidenceConfirmationStatus.CONFIRMED,
                confirmed_by="synthetic-user",
            ),
            now=NOW,
        )

        result = self.service.resolve(
            intent="skill_use",
            policy=ClaimUsePolicy(as_of=NOW, require_confirmed_evidence=True),
        )

        self.assertIsInstance(result, Resolved)
        self.assertEqual(result.packet.evidence[0].id, evidence.id)
        self.assertEqual(result.packet.evidence[0].locator, {"line": 7})

    def test_service_rejects_unregistered_derivation(self) -> None:
        with self.assertRaisesRegex(ValueError, "registered deterministic rule"):
            self.service.create_claim(
                self.request(
                    status=ClaimStatus.DERIVED,
                    approval_status=ApprovalStatus.APPROVED,
                    derivation=Derivation(
                        rule_name="arbitrary-rule",
                        rule_version="1",
                        input_claim_ids=("anything",),
                        calculated_at=NOW,
                    ),
                ),
                now=NOW,
            )

    def test_one_evidence_record_can_support_two_compatible_claims(self) -> None:
        request = self.request(
            status=ClaimStatus.VERIFIED,
            approval_status=ApprovalStatus.APPROVED,
            sensitivity=Sensitivity.PUBLIC,
            verified_by="synthetic-user",
        )
        first = self.service.create_claim(request, now=NOW)
        second = self.service.create_claim(request, now=NOW)
        evidence = self.service.create_evidence(
            CreateEvidence(
                claim_id=first.id,
                source_type=SourceType.IMPORTED_RESUME,
                source_ref="synthetic-resume.txt#line=7",
                source_text="Built a Python service for fictional test data.",
                confirmation_status=EvidenceConfirmationStatus.CONFIRMED,
                confirmed_by="synthetic-user",
            ),
            now=NOW,
        )
        self.repository.link_claim_evidence(second.id, evidence.id)

        result = self.service.resolve(
            intent="skill_use",
            policy=ClaimUsePolicy(as_of=NOW, require_confirmed_evidence=True),
        )

        self.assertIsInstance(result, Resolved)
        self.assertEqual(set(result.packet.claim_ids), {first.id, second.id})
        self.assertEqual(
            {(item.id, item.claim_id) for item in result.packet.evidence},
            {(evidence.id, first.id), (evidence.id, second.id)},
        )

    def test_contradicting_evidence_never_satisfies_support_requirement(self) -> None:
        stored = self.service.create_claim(
            self.request(
                status=ClaimStatus.VERIFIED,
                approval_status=ApprovalStatus.APPROVED,
                sensitivity=Sensitivity.PUBLIC,
                verified_by="synthetic-user",
            ),
            now=NOW,
        )
        evidence = self.repository.add_evidence(
            evidence_id="contradicting-evidence",
            source_type="imported_resume",
            source_ref="synthetic-resume.txt#line=8",
            source_text="A contradictory synthetic source span.",
            confirmation_status="confirmed",
            confirmed_by="synthetic-user",
        )
        self.repository.link_claim_evidence(
            stored.id,
            str(evidence["id"]),
            relationship="contradicts",
        )

        result = self.service.resolve(
            intent="skill_use",
            policy=ClaimUsePolicy(as_of=NOW, require_confirmed_evidence=True),
        )

        self.assertIsInstance(result, NeedInfo)
        self.assertEqual(result.reason, NeedInfoReason.UNSUPPORTED)

    def test_rejected_evidence_never_enters_a_resolved_packet(self) -> None:
        stored = self.service.create_claim(
            self.request(
                status=ClaimStatus.VERIFIED,
                approval_status=ApprovalStatus.APPROVED,
                sensitivity=Sensitivity.PUBLIC,
                verified_by="synthetic-user",
            ),
            now=NOW,
        )
        self.repository.add_evidence(
            evidence_id="rejected-evidence",
            source_type="imported_resume",
            source_ref="synthetic-resume.txt#line=9",
            source_text="A rejected synthetic source span.",
            confirmation_status="rejected",
            claim_id=stored.id,
        )

        result = self.service.resolve(
            intent="skill_use",
            policy=ClaimUsePolicy(as_of=NOW),
        )

        self.assertIsInstance(result, Resolved)
        self.assertEqual(result.packet.evidence, ())


if __name__ == "__main__":
    unittest.main()
