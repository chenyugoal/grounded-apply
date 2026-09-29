"""Managed statement retirement preserves provenance and generic compatibility."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from grounded_apply.domain import (
    ApprovalStatus, ClaimStatus, ClaimUsePolicy, Contradiction,
    EvidenceConfirmationStatus, NeedInfo, ResolutionOutcome, Resolved, Sensitivity, SourceType,
)
from grounded_apply.repositories import RepositoryError, SQLiteRepository
from grounded_apply.services import (
    CreateClaim, CreateEvidence, CreateImportProposal, CreateProfileReviewDecision, ProfileService,
)
from grounded_apply.services.profile_lifecycle import ProfileLifecycleService
from tests.test_profile_service import NOW, REVIEW_NOW, reviewable_import_request


class StatementLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reset_repository()

    def reset_repository(self) -> None:
        if hasattr(self, "repository"):
            self.repository.close()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repository = SQLiteRepository(Path(directory.name) / "fictional.db").initialize()
        self.addCleanup(self.repository.close)
        self.service = ProfileService(self.repository)
        self.lifecycle = ProfileLifecycleService(self.repository)

    def imported(
        self, key: str, *, value: str = "Python",
        source_type: SourceType = SourceType.USER_STATEMENT,
        decision: ApprovalStatus | None = ApprovalStatus.APPROVED,
    ) -> tuple[str, CreateImportProposal, CreateProfileReviewDecision | None]:
        request = replace(reviewable_import_request(idempotency_key=key, value=value), source_type=source_type)
        result = self.service.create_import_proposal(request, now=NOW)
        item = next(item for item in self.service.list_review_items() if item.claim.id == result.claims[0].id)
        review = None
        if decision is not None:
            review = CreateProfileReviewDecision(claim_id=item.claim.id, review_token=item.review_token, decision=decision, actor_id="fictional-reviewer", idempotency_key=key + "-decision")
            self.service.decide_review_item(review, now=REVIEW_NOW)
        return item.claim.id, request, review

    def resolve_skill(self) -> ResolutionOutcome:
        return self.service.resolve(intent="skill_use", policy=ClaimUsePolicy(as_of=REVIEW_NOW + timedelta(days=1), allowed_sensitivities=frozenset({Sensitivity.PERSONAL}), require_confirmed_evidence=True))

    def test_statement_withdrawal_is_audited_replayable_and_preserves_originals(self) -> None:
        claim_id, request, decision = self.imported("fictional-statement")
        original_claim = self.repository.get_claim(claim_id)
        original_evidence = self.repository.list_evidence(claim_id=claim_id)
        before = Path(self.repository.database).read_bytes()
        preview = self.lifecycle.retire(claim_id, actor_id="fictional-reviewer", idempotency_key="fictional-retire", now=REVIEW_NOW)
        self.assertTrue(preview.dry_run)
        self.assertEqual(before, Path(self.repository.database).read_bytes())
        self.assertIsInstance(self.resolve_skill(), Resolved)
        result = self.lifecycle.retire(claim_id, actor_id="fictional-reviewer", idempotency_key="fictional-retire", confirm=True, preview_token=preview.preview_token, now=REVIEW_NOW)
        self.assertTrue(result.recorded)
        self.assertIsInstance(self.resolve_skill(), NeedInfo)
        self.assertEqual(original_claim, self.repository.get_claim(claim_id))
        self.assertEqual(original_evidence, self.repository.list_evidence(claim_id=claim_id))
        self.assertEqual(original_claim["source_type"], "user_statement")
        self.assertEqual(self.service.validated_profile()[0][0].status, ClaimStatus.WITHDRAWN)
        self.service.create_import_proposal(request, now=REVIEW_NOW)
        self.service.decide_review_item(decision, now=REVIEW_NOW)
        self.assertTrue(self.lifecycle.retire(claim_id, actor_id="fictional-reviewer", idempotency_key="fictional-retire", confirm=True, preview_token=preview.preview_token, now=REVIEW_NOW).replayed)

    def test_statement_and_resume_can_explicitly_replace_each_other(self) -> None:
        for old_origin, new_origin in ((SourceType.USER_STATEMENT, SourceType.IMPORTED_RESUME), (SourceType.IMPORTED_RESUME, SourceType.USER_STATEMENT)):
            with self.subTest(old_origin=old_origin):
                if old_origin is SourceType.IMPORTED_RESUME:
                    self.reset_repository()
                old, _, _ = self.imported("fictional-old", source_type=old_origin)
                new, _, _ = self.imported("fictional-new", value="SQL", source_type=new_origin)
                self.assertIsInstance(self.resolve_skill(), Contradiction)
                preview = self.lifecycle.retire(old, replacement_claim_id=new, actor_id="fictional-reviewer", idempotency_key="fictional-replace", now=REVIEW_NOW)
                self.lifecycle.retire(old, replacement_claim_id=new, actor_id="fictional-reviewer", idempotency_key="fictional-replace", confirm=True, preview_token=preview.preview_token, now=REVIEW_NOW)
                outcome = self.resolve_skill()
                self.assertIsInstance(outcome, Resolved)
                self.assertEqual(outcome.packet.value_json, "SQL")
                self.assertEqual(self.repository.get_claim(old)["source_type"], old_origin.value)
                self.assertEqual(self.repository.get_claim(new)["source_type"], new_origin.value)

    def test_ordinary_approved_user_statement_keeps_resolution_but_not_import_retirement(self) -> None:
        claim = self.service.create_claim(CreateClaim(claim_id="fictional-generic", claim_type="skill_use", value="Python", canonical_text="Used Python", source_type=SourceType.USER_STATEMENT, source_ref="fictional direct answer", status=ClaimStatus.VERIFIED, approval_status=ApprovalStatus.APPROVED, verified_by="fictional-reviewer"), now=NOW)
        self.service.create_evidence(CreateEvidence(claim_id=claim.id, source_type=SourceType.USER_STATEMENT, source_ref="fictional direct answer", source_text="Used Python", confirmation_status=EvidenceConfirmationStatus.CONFIRMED, confirmed_by="fictional-reviewer"), now=NOW)
        self.assertIsInstance(self.resolve_skill(), Resolved)
        self.assertIsNone(self.repository.get_profile_import_review_item(claim.id))
        before = Path(self.repository.database).read_bytes()
        with self.assertRaisesRegex(ValueError, "approved imported claim"):
            self.lifecycle.retire(claim.id, actor_id="fictional-reviewer", idempotency_key="fictional-retire", now=REVIEW_NOW)
        self.assertEqual(before, Path(self.repository.database).read_bytes())
        self.assertEqual(self.repository.list_claim_retirements(), [])

    def test_pending_and_rejected_statements_are_ineligible_for_retirement(self) -> None:
        for key, decision in (("fictional-pending", None), ("fictional-rejected", ApprovalStatus.REJECTED)):
            with self.subTest(decision=decision):
                claim_id, _, _ = self.imported(key, decision=decision)
                before = Path(self.repository.database).read_bytes()
                with self.assertRaisesRegex(ValueError, "approved imported claim"):
                    self.lifecycle.retire(claim_id, actor_id="fictional-reviewer", idempotency_key=key + "-retire", now=REVIEW_NOW)
                self.assertEqual(before, Path(self.repository.database).read_bytes())
        self.assertEqual(self.repository.list_claim_retirements(), [])

    def test_missing_association_and_erased_markers_cannot_launder_retirement(self) -> None:
        for already_retired in (False, True):
            with self.subTest(already_retired=already_retired):
                if already_retired:
                    self.reset_repository()
                claim_id, _, _ = self.imported("fictional-owned")
                preview = self.lifecycle.retire(claim_id, actor_id="fictional-reviewer", idempotency_key="fictional-retire", now=REVIEW_NOW)
                if already_retired:
                    self.lifecycle.retire(claim_id, actor_id="fictional-reviewer", idempotency_key="fictional-retire", confirm=True, preview_token=preview.preview_token, now=REVIEW_NOW)
                # Deliberate corruption of disposable synthetic storage removes
                # every mutable origin marker, retaining import workflow ownership.
                with closing(sqlite3.connect(self.repository.database)) as connection, connection:
                    connection.execute("DELETE FROM profile_import_review_items WHERE claim_id = ?", (claim_id,))
                    connection.execute("UPDATE claims SET source_ref = 'fictional direct answer' WHERE id = ?", (claim_id,))
                    connection.execute("UPDATE evidence SET source_ref = 'fictional direct answer', artifact_id = NULL, extraction_method = 'manual'")
                before = Path(self.repository.database).read_bytes()
                with self.assertRaises(RepositoryError):
                    self.lifecycle.retire(claim_id, actor_id="fictional-reviewer", idempotency_key="fictional-retire", confirm=True, preview_token=preview.preview_token, now=REVIEW_NOW)
                with self.assertRaises(RepositoryError):
                    self.resolve_skill()
                self.assertEqual(before, Path(self.repository.database).read_bytes())
                self.assertEqual(len(self.repository.list_claim_retirements()), int(already_retired))


if __name__ == "__main__":
    unittest.main()
