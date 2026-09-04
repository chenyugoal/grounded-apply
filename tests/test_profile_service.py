from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from grounded_apply.domain import (
    ApprovalStatus,
    ClaimStatus,
    ClaimUsePolicy,
    Contradiction,
    Derivation,
    EvidenceConfirmationStatus,
    NeedInfo,
    NeedInfoReason,
    Resolved,
    Sensitivity,
    SourceType,
)
from grounded_apply.repositories import RepositoryError, SQLiteRepository
from grounded_apply.services import (
    CreateClaim,
    CreateEvidence,
    CreateImportProposal,
    CreateProfileReviewDecision,
    ProfileReviewDecisionResult,
    ProfileReviewItem,
    ProfileService,
    ProposedImportClaim,
    TextSourceSpan,
)


NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
NOW_TEXT = "2026-08-11T12:00:00Z"
REVIEW_NOW = NOW + timedelta(hours=1)


def reviewable_import_request(
    *,
    idempotency_key: str = "synthetic-profile-review-import",
    claim_type: str = "skill_use",
    value: str | dict[str, str] = "Python",
    canonical_text: str = "Used Python on a fictional project",
    subject_type: str = "candidate",
    subject_id: str | None = None,
    confidence: float = 1.0,
    sensitivity: Sensitivity = Sensitivity.PERSONAL,
    unselected_header: str = "Synthetic document header with unrelated material.",
) -> CreateImportProposal:
    source_text = (
        f"{unselected_header}\n"
        "Synthetic supporting evidence.\n"
        "Synthetic document footer with unrelated material."
    )
    selected_text = "Synthetic supporting evidence."
    start = source_text.index(selected_text)
    return CreateImportProposal(
        idempotency_key=idempotency_key,
        source_text=source_text,
        expected_source_sha256=hashlib.sha256(
            source_text.encode("utf-8")
        ).hexdigest(),
        proposals=(
            ProposedImportClaim(
                claim_type=claim_type,
                value=value,
                canonical_text=canonical_text,
                subject_type=subject_type,
                subject_id=subject_id,
                confidence=confidence,
                sensitivity=sensitivity,
                span=TextSourceSpan(
                    start=start,
                    end=start + len(selected_text),
                    text=selected_text,
                ),
            ),
        ),
    )


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

    def import_review_item(
        self,
        request: CreateImportProposal | None = None,
    ) -> tuple[CreateImportProposal, ProfileReviewItem]:
        import_request = request or reviewable_import_request()
        imported = self.service.create_import_proposal(import_request, now=NOW)
        items = self.service.list_review_items()
        item = next(candidate for candidate in items if candidate.claim.id == imported.claims[0].id)
        self.assertEqual(item.import_workflow_run_id, imported.workflow_run_id)
        self.assertEqual(item.proposal_index, 0)
        self.assertRegex(item.review_token, r"\A[0-9a-f]{64}\Z")
        return import_request, item

    @staticmethod
    def review_decision(
        item: ProfileReviewItem,
        *,
        decision: ApprovalStatus = ApprovalStatus.APPROVED,
        actor_id: str = "synthetic-reviewer",
        idempotency_key: str = "synthetic-review-decision",
        review_token: str | None = None,
    ) -> CreateProfileReviewDecision:
        return CreateProfileReviewDecision(
            claim_id=item.claim.id,
            review_token=item.review_token if review_token is None else review_token,
            decision=decision,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
        )

    def assert_item_still_pending(self, item: ProfileReviewItem) -> None:
        claim = self.service.get_claim(item.claim.id)
        evidence = self.repository.get_evidence(item.evidence[0].id)
        self.assertEqual(claim.status, ClaimStatus.NEEDS_REVIEW)
        self.assertEqual(claim.approval_status, ApprovalStatus.PENDING)
        self.assertIsNotNone(evidence)
        assert evidence is not None
        self.assertEqual(evidence["confirmation_status"], "pending")
        self.assertIsNone(evidence["confirmed_at"])
        self.assertIsNone(evidence["confirmed_by"])

    def test_profile_review_approval_projects_verified_state_and_minimal_audit(
        self,
    ) -> None:
        import_request, item = self.import_review_item()
        decision_request = self.review_decision(item)

        result = self.service.decide_review_item(decision_request, now=REVIEW_NOW)

        self.assertIsInstance(result, ProfileReviewDecisionResult)
        self.assertEqual(result.import_workflow_run_id, item.import_workflow_run_id)
        self.assertEqual(result.proposal_index, item.proposal_index)
        self.assertEqual(result.claim.id, item.claim.id)
        self.assertEqual(result.evidence.id, item.evidence[0].id)
        self.assertEqual(result.decision, ApprovalStatus.APPROVED)
        self.assertEqual(result.actor_id, decision_request.actor_id)
        self.assertEqual(result.decided_at, REVIEW_NOW)
        self.assertNotEqual(
            result.decision_workflow_run_id,
            result.import_workflow_run_id,
        )

        claim = self.service.get_claim(item.claim.id)
        evidence = self.repository.get_evidence(item.evidence[0].id)
        self.assertEqual(claim.status, ClaimStatus.VERIFIED)
        self.assertEqual(claim.approval_status, ApprovalStatus.APPROVED)
        self.assertEqual(claim.verified_at, REVIEW_NOW)
        self.assertEqual(claim.verified_by, decision_request.actor_id)
        self.assertIsNotNone(evidence)
        assert evidence is not None
        self.assertEqual(evidence["confirmation_status"], "confirmed")
        self.assertEqual(evidence["confirmed_at"], "2026-08-11T13:00:00Z")
        self.assertEqual(evidence["confirmed_by"], decision_request.actor_id)

        resolved = self.service.resolve(
            intent="skill_use",
            policy=ClaimUsePolicy(
                as_of=REVIEW_NOW,
                allowed_sensitivities=frozenset(
                    {Sensitivity.PUBLIC, Sensitivity.PERSONAL}
                ),
                require_confirmed_evidence=True,
            ),
        )
        self.assertIsInstance(resolved, Resolved)
        self.assertEqual(resolved.packet.claim_ids, (claim.id,))

        audit = self.repository.get_workflow_run(result.decision_workflow_run_id)
        self.assertIsNotNone(audit)
        assert audit is not None
        audit_text = str(audit)
        self.assertEqual(audit["status"], "succeeded")
        self.assertIn(decision_request.actor_id, audit_text)
        self.assertNotIn(import_request.idempotency_key, audit_text)
        self.assertNotIn(decision_request.idempotency_key, audit_text)
        self.assertNotIn(import_request.source_text, audit_text)
        self.assertNotIn(item.claim.canonical_text, audit_text)
        self.assertNotIn(str(item.claim.value_json), audit_text)

    def test_profile_review_rejection_is_terminal_and_unusable(self) -> None:
        _, item = self.import_review_item()

        result = self.service.decide_review_item(
            self.review_decision(
                item,
                decision=ApprovalStatus.REJECTED,
                idempotency_key="synthetic-rejection-decision",
            ),
            now=REVIEW_NOW,
        )

        self.assertIsInstance(result, ProfileReviewDecisionResult)
        self.assertEqual(result.decision, ApprovalStatus.REJECTED)
        claim = self.service.get_claim(item.claim.id)
        evidence = self.repository.get_evidence(item.evidence[0].id)
        self.assertEqual(claim.status, ClaimStatus.WITHDRAWN)
        self.assertEqual(claim.approval_status, ApprovalStatus.REJECTED)
        self.assertIsNone(claim.verified_at)
        self.assertIsNone(claim.verified_by)
        self.assertIsNotNone(evidence)
        assert evidence is not None
        self.assertEqual(evidence["confirmation_status"], "rejected")
        self.assertIsNone(evidence["confirmed_at"])
        self.assertIsNone(evidence["confirmed_by"])
        self.assertNotIn(item.claim.id, {entry.claim.id for entry in self.service.list_review_items()})
        resolved = self.service.resolve(
            intent="skill_use",
            policy=ClaimUsePolicy(
                as_of=REVIEW_NOW,
                allowed_sensitivities=frozenset(
                    {Sensitivity.PUBLIC, Sensitivity.PERSONAL}
                ),
            ),
        )
        self.assertNotIsInstance(resolved, Resolved)

    def test_profile_review_decision_is_exactly_idempotent(self) -> None:
        _, item = self.import_review_item()
        request = self.review_decision(item)

        first = self.service.decide_review_item(request, now=REVIEW_NOW)
        replay = self.service.decide_review_item(
            request,
            now=REVIEW_NOW + timedelta(days=1),
        )

        self.assertEqual(replay, first)
        self.assertEqual(len(self.repository.list_workflow_runs()), 2)
        changed_actor = replace(request, actor_id="different-synthetic-reviewer")
        with self.assertRaisesRegex(RepositoryError, "idempotency|different input"):
            self.service.decide_review_item(changed_actor, now=REVIEW_NOW)
        self.assertEqual(len(self.repository.list_workflow_runs()), 2)

    def test_profile_review_terminal_replay_ignores_caller_now(self) -> None:
        _, item = self.import_review_item()
        request = self.review_decision(item)
        first = self.service.decide_review_item(request, now=REVIEW_NOW)

        replay = self.service.decide_review_item(
            request,
            now=NOW - timedelta(days=1),
        )

        self.assertEqual(replay, first)
        self.assertEqual(len(self.repository.list_workflow_runs()), 2)

    def test_profile_review_rejects_wrong_or_stale_review_tokens_without_writing(
        self,
    ) -> None:
        _, item = self.import_review_item()
        workflow_count = len(self.repository.list_workflow_runs())

        with self.assertRaises((ValueError, RepositoryError)):
            self.service.decide_review_item(
                self.review_decision(item, review_token="0" * 64),
                now=REVIEW_NOW,
            )

        self.assert_item_still_pending(item)
        self.assertEqual(len(self.repository.list_workflow_runs()), workflow_count)

        with closing(sqlite3.connect(self.repository.database)) as connection:
            connection.execute(
                "UPDATE claims SET canonical_text = ? WHERE id = ?",
                ("Used Python on a changed fictional project", item.claim.id),
            )
            connection.commit()
        with self.assertRaises((ValueError, RepositoryError)):
            self.service.decide_review_item(
                self.review_decision(
                    item,
                    idempotency_key="synthetic-stale-token-decision",
                ),
                now=REVIEW_NOW,
            )
        self.assert_item_still_pending(item)
        self.assertEqual(len(self.repository.list_workflow_runs()), workflow_count)

    def test_profile_review_approval_rejects_confidential_and_highly_sensitive_claims(
        self,
    ) -> None:
        for index, sensitivity in enumerate(
            (Sensitivity.CONFIDENTIAL, Sensitivity.HIGHLY_SENSITIVE)
        ):
            with self.subTest(sensitivity=sensitivity):
                request = reviewable_import_request(
                    idempotency_key=f"synthetic-sensitive-import-{index}",
                    sensitivity=sensitivity,
                )
                _, item = self.import_review_item(request)
                workflow_count = len(self.repository.list_workflow_runs())

                with self.assertRaises((ValueError, RepositoryError)):
                    self.service.decide_review_item(
                        self.review_decision(
                            item,
                            idempotency_key=f"synthetic-sensitive-decision-{index}",
                        ),
                        now=REVIEW_NOW,
                    )

                self.assert_item_still_pending(item)
                self.assertEqual(len(self.repository.list_workflow_runs()), workflow_count)

    def test_profile_review_detects_coherently_tampered_content_from_record_digest(
        self,
    ) -> None:
        _, item = self.import_review_item()
        evidence_id = item.evidence[0].id
        changed_evidence = "Synthetic supporting evidencf."
        self.assertEqual(len(changed_evidence), len(item.evidence[0].source_text or ""))
        with closing(sqlite3.connect(self.repository.database)) as connection:
            connection.execute(
                "UPDATE claims SET value_json = ?, canonical_text = ? WHERE id = ?",
                (
                    json.dumps("Cobol"),
                    "Used Cobol on a changed fictional project",
                    item.claim.id,
                ),
            )
            connection.execute(
                "UPDATE evidence SET source_text = ?, checksum_sha256 = ? WHERE id = ?",
                (
                    changed_evidence,
                    hashlib.sha256(changed_evidence.encode("utf-8")).hexdigest(),
                    evidence_id,
                ),
            )
            connection.commit()

        workflow_count = len(self.repository.list_workflow_runs())
        with self.assertRaisesRegex(
            (ValueError, RepositoryError),
            "digest|integrity|identity",
        ):
            self.service.decide_review_item(
                self.review_decision(item),
                now=REVIEW_NOW,
            )

        self.assertEqual(len(self.repository.list_workflow_runs()), workflow_count)
        stored = self.service.get_claim(item.claim.id)
        self.assertEqual(stored.status, ClaimStatus.NEEDS_REVIEW)
        self.assertEqual(stored.approval_status, ApprovalStatus.PENDING)

    def test_profile_review_rejects_hidden_pending_association(self) -> None:
        _, item = self.import_review_item()
        association = self.repository.get_profile_import_review_item(item.claim.id)
        self.assertIsNotNone(association)
        assert association is not None
        self.assertIsNone(association["decision"])
        with closing(sqlite3.connect(self.repository.database)) as connection:
            connection.execute(
                """
                UPDATE claims
                SET status = 'withdrawn', approval_status = 'rejected'
                WHERE id = ?
                """,
                (item.claim.id,),
            )
            connection.commit()

        with self.assertRaisesRegex(RepositoryError, "integrity|provenance"):
            self.service.list_review_items()

        stored_association = self.repository.get_profile_import_review_item(
            item.claim.id
        )
        self.assertIsNotNone(stored_association)
        assert stored_association is not None
        self.assertIsNone(stored_association["decision"])

    def test_resolve_revalidates_approved_import_record_digest(self) -> None:
        _, item = self.import_review_item()
        approved = self.service.decide_review_item(
            self.review_decision(item),
            now=REVIEW_NOW,
        )
        changed_evidence = "Synthetic supporting evidencf."
        self.assertEqual(len(changed_evidence), len(approved.evidence.source_text or ""))
        with closing(sqlite3.connect(self.repository.database)) as connection:
            connection.execute(
                "UPDATE claims SET value_json = ?, canonical_text = ? WHERE id = ?",
                (
                    json.dumps("Cobol"),
                    "Used Cobol on a changed fictional project",
                    approved.claim.id,
                ),
            )
            connection.execute(
                "UPDATE evidence SET source_text = ?, checksum_sha256 = ? WHERE id = ?",
                (
                    changed_evidence,
                    hashlib.sha256(changed_evidence.encode("utf-8")).hexdigest(),
                    approved.evidence.id,
                ),
            )
            connection.commit()

        with self.assertRaisesRegex(RepositoryError, "digest|integrity|identity"):
            self.service.resolve(
                intent="skill_use",
                policy=ClaimUsePolicy(
                    as_of=REVIEW_NOW,
                    allowed_sensitivities=frozenset(
                        {Sensitivity.PUBLIC, Sensitivity.PERSONAL}
                    ),
                    require_confirmed_evidence=True,
                ),
            )

    def test_resolve_rejects_laundered_approved_import_source_type(self) -> None:
        _, item = self.import_review_item()
        approved = self.service.decide_review_item(
            self.review_decision(item),
            now=REVIEW_NOW,
        )
        with closing(sqlite3.connect(self.repository.database)) as connection:
            connection.execute(
                "UPDATE claims SET source_type = 'user_statement' WHERE id = ?",
                (approved.claim.id,),
            )
            connection.commit()

        with self.assertRaisesRegex(RepositoryError, "integrity|provenance|identity"):
            self.service.resolve(
                intent="skill_use",
                policy=ClaimUsePolicy(
                    as_of=REVIEW_NOW,
                    allowed_sensitivities=frozenset(
                        {Sensitivity.PUBLIC, Sensitivity.PERSONAL}
                    ),
                    require_confirmed_evidence=True,
                ),
            )

    def test_resolve_ignores_unassociated_legacy_pending_import(self) -> None:
        approved = self.service.create_claim(
            self.request(
                status=ClaimStatus.VERIFIED,
                approval_status=ApprovalStatus.APPROVED,
                sensitivity=Sensitivity.PUBLIC,
                verified_by="synthetic-reviewer",
            ),
            now=NOW,
        )
        legacy = self.repository.add_claim(
            claim_id="synthetic-legacy-pending-import",
            claim_type="employment_title",
            value={
                "employer": "Example Legacy Works LLC",
                "title": "Synthetic Archivist",
            },
            canonical_text="Held a synthetic title in an unassociated legacy import",
            subject_type="employment",
            subject_id="synthetic-legacy-employment",
            source_type=SourceType.IMPORTED_RESUME.value,
            source_ref="synthetic-legacy-import-ref",
            created_at=NOW_TEXT,
        )

        result = self.service.resolve(
            intent="skill_use",
            policy=ClaimUsePolicy(as_of=NOW),
        )

        self.assertIsInstance(result, Resolved)
        assert isinstance(result, Resolved)
        self.assertEqual(result.packet.claim_ids, (approved.id,))
        stored_legacy = self.service.get_claim(str(legacy["id"]))
        self.assertEqual(stored_legacy.status, ClaimStatus.NEEDS_REVIEW)
        self.assertEqual(stored_legacy.approval_status, ApprovalStatus.PENDING)

    def test_resolve_rejects_unassociated_legacy_approved_import(self) -> None:
        legacy = self.repository.add_claim(
            claim_id="synthetic-legacy-approved-import",
            claim_type="skill_use",
            value="Rust",
            canonical_text="Used Rust in a synthetic unassociated legacy import",
            subject_type="candidate",
            status=ClaimStatus.VERIFIED.value,
            approval_status=ApprovalStatus.APPROVED.value,
            sensitivity=Sensitivity.PUBLIC.value,
            source_type=SourceType.IMPORTED_RESUME.value,
            source_ref="synthetic-legacy-import-ref",
            verified_at=NOW_TEXT,
            verified_by="synthetic-legacy-reviewer",
            created_at=NOW_TEXT,
        )

        with self.assertRaisesRegex(RepositoryError, "integrity|provenance|review"):
            self.service.resolve(
                intent="skill_use",
                policy=ClaimUsePolicy(as_of=NOW),
            )

        stored = self.repository.get_claim(str(legacy["id"]))
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored["status"], ClaimStatus.VERIFIED.value)
        self.assertEqual(stored["approval_status"], ApprovalStatus.APPROVED.value)

    def test_profile_review_approval_returns_contradiction_without_writing(self) -> None:
        conflicting_request = CreateClaim(
            claim_type="skill_use",
            value="Rust",
            canonical_text="Used Rust on a different fictional project",
            subject_type="candidate",
            source_type=SourceType.USER_STATEMENT,
            source_ref="synthetic-user-statement:conflicting-skill",
            status=ClaimStatus.CONTRADICTED,
            approval_status=ApprovalStatus.REJECTED,
            sensitivity=Sensitivity.PUBLIC,
        )
        conflicting = self.service.create_claim(conflicting_request, now=NOW)
        _, item = self.import_review_item()
        before_claims = self.repository.list_claims()
        before_evidence = self.repository.list_evidence()
        before_workflows = self.repository.list_workflow_runs()

        outcome = self.service.decide_review_item(
            self.review_decision(item),
            now=REVIEW_NOW,
        )

        self.assertIsInstance(outcome, Contradiction)
        self.assertEqual(outcome.conflicting_claim_ids, (conflicting.id,))
        self.assertEqual(self.repository.list_claims(), before_claims)
        self.assertEqual(self.repository.list_evidence(), before_evidence)
        self.assertEqual(self.repository.list_workflow_runs(), before_workflows)

    def test_profile_review_private_conflict_blocks_without_disclosure_or_writes(
        self,
    ) -> None:
        private_value = "SYNTHETIC_PRIVATE_CONFLICT_VALUE_7QX"
        private_canonical_text = "SYNTHETIC_PRIVATE_CONFLICT_CANONICAL_8RY"
        private_source_ref = "synthetic-private-conflict-ref-9sz"
        conflicting = self.service.create_claim(
            CreateClaim(
                claim_type="skill_use",
                value=private_value,
                canonical_text=private_canonical_text,
                subject_type="candidate",
                source_type=SourceType.USER_STATEMENT,
                source_ref=private_source_ref,
                status=ClaimStatus.CONTRADICTED,
                approval_status=ApprovalStatus.REJECTED,
                sensitivity=Sensitivity.HIGHLY_SENSITIVE,
            ),
            now=NOW,
        )
        _, item = self.import_review_item()
        self.assertEqual(conflicting.subject_type, item.claim.subject_type)
        self.assertEqual(conflicting.subject_id, item.claim.subject_id)
        before_claims = self.repository.list_claims()
        before_evidence = self.repository.list_evidence()
        before_workflows = self.repository.list_workflow_runs()

        with self.assertRaisesRegex(RepositoryError, "conflict|review|resolve") as raised:
            self.service.decide_review_item(
                self.review_decision(item),
                now=REVIEW_NOW,
            )

        disclosure_surfaces = (
            repr(raised.exception),
            repr(self.repository.list_workflow_runs()),
        )
        for private_text in (
            private_value,
            private_canonical_text,
            private_source_ref,
        ):
            for surface in disclosure_surfaces:
                self.assertNotIn(private_text, surface)
        self.assertEqual(self.repository.list_claims(), before_claims)
        self.assertEqual(self.repository.list_evidence(), before_evidence)
        self.assertEqual(self.repository.list_workflow_runs(), before_workflows)

    def test_profile_review_approval_allows_ordinary_distinct_claims(self) -> None:
        compatible_cases = (
            (
                "multi-valued-skills",
                CreateClaim(
                    claim_type="skill_use",
                    value="Rust",
                    canonical_text="Used Rust on a different fictional project",
                    subject_type="candidate",
                    source_type=SourceType.USER_STATEMENT,
                    source_ref="synthetic-user-statement:compatible-skill",
                    status=ClaimStatus.VERIFIED,
                    approval_status=ApprovalStatus.APPROVED,
                    sensitivity=Sensitivity.PUBLIC,
                    verified_by="synthetic-reviewer",
                ),
                reviewable_import_request(),
            ),
            (
                "different-employment-subjects",
                CreateClaim(
                    claim_type="employment_title",
                    value={
                        "employer": "Example Robotics LLC",
                        "title": "Synthetic Staff Engineer",
                    },
                    canonical_text=(
                        "Held the Synthetic Staff Engineer title at Example Robotics LLC"
                    ),
                    subject_type="employment",
                    subject_id="synthetic-employment-one",
                    source_type=SourceType.USER_STATEMENT,
                    source_ref="synthetic-user-statement:compatible-title",
                    status=ClaimStatus.VERIFIED,
                    approval_status=ApprovalStatus.APPROVED,
                    sensitivity=Sensitivity.PUBLIC,
                    verified_by="synthetic-reviewer",
                ),
                reviewable_import_request(
                    idempotency_key="synthetic-other-subject-import",
                    claim_type="employment_title",
                    value={
                        "employer": "Example Aeronautics LLC",
                        "title": "Synthetic Principal Engineer",
                    },
                    canonical_text=(
                        "Held the Synthetic Principal Engineer title at "
                        "Example Aeronautics LLC"
                    ),
                    subject_type="employment",
                    subject_id="synthetic-employment-two",
                ),
            ),
        )
        for case_name, existing_request, import_request in compatible_cases:
            with (
                self.subTest(case=case_name),
                tempfile.TemporaryDirectory() as directory,
                SQLiteRepository(Path(directory) / "profile.db") as repository,
            ):
                service = ProfileService(repository)
                existing = service.create_claim(existing_request, now=NOW)
                imported = service.create_import_proposal(import_request, now=NOW)
                item = next(
                    candidate
                    for candidate in service.list_review_items()
                    if candidate.claim.id == imported.claims[0].id
                )

                result = service.decide_review_item(
                    self.review_decision(item),
                    now=REVIEW_NOW,
                )

                self.assertIsInstance(result, ProfileReviewDecisionResult)
                assert isinstance(result, ProfileReviewDecisionResult)
                self.assertEqual(result.decision, ApprovalStatus.APPROVED)
                self.assertEqual(service.get_claim(existing.id), existing)

    def test_profile_review_blocks_workflow_result_and_provenance_corruption(self) -> None:
        corruption_cases = (
            "workflow",
            "result_manifest",
            "checksum",
            "locator",
            "reverse_link",
        )
        for corruption in corruption_cases:
            with (
                self.subTest(corruption=corruption),
                tempfile.TemporaryDirectory() as directory,
                SQLiteRepository(Path(directory) / "profile.db") as repository,
            ):
                service = ProfileService(repository)
                imported = service.create_import_proposal(
                    reviewable_import_request(),
                    now=NOW,
                )
                item = next(
                    candidate
                    for candidate in service.list_review_items()
                    if candidate.claim.id == imported.claims[0].id
                )
                evidence_id = item.evidence[0].id
                if corruption == "reverse_link":
                    other = service.create_claim(
                        CreateClaim(
                            claim_type="skill_use",
                            value="Python",
                            canonical_text="Used Python in another fictional source",
                            source_type=SourceType.USER_STATEMENT,
                            source_ref="synthetic-user-statement:reverse-link",
                        ),
                        now=NOW,
                    )
                    repository.link_claim_evidence(other.id, evidence_id)
                else:
                    with closing(sqlite3.connect(repository.database)) as connection:
                        if corruption == "workflow":
                            connection.execute(
                                "UPDATE workflow_runs SET input_hash_sha256 = ? WHERE id = ?",
                                ("0" * 64, imported.workflow_run_id),
                            )
                        elif corruption == "result_manifest":
                            connection.execute(
                                "UPDATE workflow_runs SET generated_artifacts_json = ? "
                                "WHERE id = ?",
                                ("{}", imported.workflow_run_id),
                            )
                        elif corruption == "checksum":
                            connection.execute(
                                "UPDATE evidence SET checksum_sha256 = ? WHERE id = ?",
                                ("0" * 64, evidence_id),
                            )
                        else:
                            locator = dict(item.evidence[0].locator)  # type: ignore[arg-type]
                            locator["source_sha256"] = "0" * 64
                            connection.execute(
                                "UPDATE evidence SET locator_json = ? WHERE id = ?",
                                (json.dumps(locator), evidence_id),
                            )
                        connection.commit()

                workflow_count = len(repository.list_workflow_runs())
                with self.assertRaises((ValueError, RepositoryError)):
                    service.decide_review_item(
                        self.review_decision(
                            item,
                            idempotency_key=f"synthetic-corruption-{corruption}",
                        ),
                        now=REVIEW_NOW,
                    )

                claim = service.get_claim(item.claim.id)
                self.assertEqual(claim.status, ClaimStatus.NEEDS_REVIEW)
                self.assertEqual(claim.approval_status, ApprovalStatus.PENDING)
                self.assertEqual(len(repository.list_workflow_runs()), workflow_count)

    def test_profile_review_rejects_inconsistent_source_artifact_timestamp(
        self,
    ) -> None:
        private_source_marker = "SYNTHETIC_PRIVATE_ARTIFACT_SOURCE_4NV"
        private_timestamp = "2001-02-03T04:05:06Z"
        request, item = self.import_review_item(
            reviewable_import_request(
                idempotency_key="synthetic-backdated-artifact-import",
                unselected_header=private_source_marker,
            )
        )
        with closing(sqlite3.connect(self.repository.database)) as connection:
            connection.execute(
                "UPDATE artifacts SET captured_at = ? WHERE id = ?",
                (
                    private_timestamp,
                    request.source_artifact_id,
                ),
            )
            connection.commit()
        before_claims = self.repository.list_claims()
        before_evidence = self.repository.list_evidence()
        before_artifacts = self.repository.list_artifacts()
        before_workflows = self.repository.list_workflow_runs()

        errors: list[RepositoryError] = []
        with self.assertRaisesRegex(
            RepositoryError,
            "integrity|provenance|identity",
        ) as list_error:
            self.service.list_review_items()
        errors.append(list_error.exception)
        with self.assertRaisesRegex(
            RepositoryError,
            "integrity|provenance|identity",
        ) as decision_error:
            self.service.decide_review_item(
                self.review_decision(
                    item,
                    idempotency_key="synthetic-backdated-artifact-decision",
                ),
                now=REVIEW_NOW,
            )
        errors.append(decision_error.exception)

        disclosure_surfaces = (
            *(repr(error) for error in errors),
            repr(self.repository.list_workflow_runs()),
        )
        for private_text in (
            private_source_marker,
            private_timestamp,
            request.source_text,
            item.claim.canonical_text,
        ):
            for surface in disclosure_surfaces:
                self.assertNotIn(private_text, surface)
        self.assertEqual(self.repository.list_claims(), before_claims)
        self.assertEqual(self.repository.list_evidence(), before_evidence)
        self.assertEqual(self.repository.list_artifacts(), before_artifacts)
        self.assertEqual(self.repository.list_workflow_runs(), before_workflows)

    def test_profile_review_rolls_back_if_final_audit_checkpoint_fails(self) -> None:
        _, item = self.import_review_item()
        workflow_count = len(self.repository.list_workflow_runs())

        with patch.object(
            self.repository,
            "update_workflow_run",
            side_effect=RuntimeError("synthetic final audit checkpoint failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "final audit checkpoint"):
                self.service.decide_review_item(
                    self.review_decision(item),
                    now=REVIEW_NOW,
                )

        self.assert_item_still_pending(item)
        self.assertEqual(len(self.repository.list_workflow_runs()), workflow_count)

    def test_profile_import_replay_remains_valid_after_review_decisions(self) -> None:
        approved_request, approved_item = self.import_review_item()
        approved = self.service.decide_review_item(
            self.review_decision(approved_item),
            now=REVIEW_NOW,
        )
        approved_replay = self.service.create_import_proposal(
            approved_request,
            now=REVIEW_NOW + timedelta(days=1),
        )
        self.assertEqual(approved_replay.workflow_run_id, approved.import_workflow_run_id)
        self.assertEqual(approved_replay.claims[0].id, approved.claim.id)
        self.assertEqual(approved_replay.claims[0].status, ClaimStatus.VERIFIED)
        self.assertEqual(
            approved_replay.evidence[0].confirmation_status,
            EvidenceConfirmationStatus.CONFIRMED,
        )

        rejected_request = reviewable_import_request(
            idempotency_key="synthetic-rejected-import-replay",
            value="Rust",
            canonical_text="Used Rust on a separate fictional project",
        )
        rejected_request, rejected_item = self.import_review_item(rejected_request)
        rejected = self.service.decide_review_item(
            self.review_decision(
                rejected_item,
                decision=ApprovalStatus.REJECTED,
                idempotency_key="synthetic-rejected-review-decision",
            ),
            now=REVIEW_NOW,
        )
        rejected_replay = self.service.create_import_proposal(
            rejected_request,
            now=REVIEW_NOW + timedelta(days=1),
        )
        self.assertEqual(rejected_replay.workflow_run_id, rejected.import_workflow_run_id)
        self.assertEqual(rejected_replay.claims[0].id, rejected.claim.id)
        self.assertEqual(rejected_replay.claims[0].status, ClaimStatus.WITHDRAWN)
        self.assertEqual(
            rejected_replay.evidence[0].confirmation_status,
            EvidenceConfirmationStatus.REJECTED,
        )

    def test_profile_import_normalizes_signed_zero_before_review_and_replay(self) -> None:
        request = reviewable_import_request(
            idempotency_key="synthetic-negative-zero-import",
            confidence=-0.0,
        )

        created = self.service.create_import_proposal(request, now=NOW)
        item = next(
            candidate
            for candidate in self.service.list_review_items()
            if candidate.claim.id == created.claims[0].id
        )
        replay = self.service.create_import_proposal(
            request,
            now=REVIEW_NOW,
        )

        self.assertEqual(item.claim.id, created.claims[0].id)
        self.assertEqual(math.copysign(1.0, item.claim.confidence), 1.0)
        self.assertEqual(replay, created)

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
                source_type=SourceType.OTHER,
                source_ref="synthetic-source.txt#line=7",
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
                source_type=SourceType.OTHER,
                source_ref="synthetic-source.txt#line=7",
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

    def test_public_claim_creation_rejects_imported_resume_for_every_trust_state(
        self,
    ) -> None:
        cases = (
            {},
            {
                "status": ClaimStatus.VERIFIED,
                "approval_status": ApprovalStatus.APPROVED,
                "verified_by": "synthetic-user",
            },
        )

        for overrides in cases:
            with self.subTest(status=overrides.get("status", ClaimStatus.NEEDS_REVIEW)):
                with self.assertRaisesRegex(ValueError, "profile import workflow"):
                    self.service.create_claim(
                        self.request(
                            source_type=SourceType.IMPORTED_RESUME,
                            **overrides,
                        ),
                        now=NOW,
                    )

        self.assertEqual(self.repository.list_claims(), [])

    def test_public_evidence_creation_rejects_imported_resume_for_every_trust_state(
        self,
    ) -> None:
        claim = self.service.create_claim(self.request(), now=NOW)
        cases = (
            {},
            {
                "confirmation_status": EvidenceConfirmationStatus.CONFIRMED,
                "confirmed_by": "synthetic-user",
            },
        )

        for overrides in cases:
            with self.subTest(
                confirmation_status=overrides.get(
                    "confirmation_status",
                    EvidenceConfirmationStatus.PENDING,
                )
            ):
                with self.assertRaisesRegex(ValueError, "profile import workflow"):
                    self.service.create_evidence(
                        CreateEvidence(
                            claim_id=claim.id,
                            source_type=SourceType.IMPORTED_RESUME,
                            source_ref="synthetic-resume.txt#line=7",
                            source_text="Synthetic imported evidence.",
                            **overrides,
                        ),
                        now=NOW,
                    )

        self.assertEqual(self.repository.list_evidence(), [])

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
            source_type="other",
            source_ref="synthetic-source.txt#line=8",
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
            source_type="other",
            source_ref="synthetic-source.txt#line=9",
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
