"""Correction/contradiction resolution retains original approved history."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import timedelta
from pathlib import Path

from grounded_apply.domain import ApprovalStatus, ClaimStatus, ClaimUsePolicy, Contradiction, NeedInfo, Resolved, Sensitivity
from grounded_apply.repositories import SQLiteRepository
from grounded_apply.services import CreateProfileReviewDecision, ProfileService
from grounded_apply.services.profile_lifecycle import ProfileLifecycleService
from tests.test_profile_service import NOW, REVIEW_NOW, reviewable_import_request


class ProfileLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repository = SQLiteRepository(Path(directory.name) / "synthetic.db").initialize()
        self.addCleanup(self.repository.close)
        self.service = ProfileService(self.repository)
        self.lifecycle = ProfileLifecycleService(self.repository)

    def approved(self, value: str, key: str):
        request = reviewable_import_request(value=value, idempotency_key=key)
        result = self.service.create_import_proposal(request, now=NOW)
        item = next(i for i in self.service.list_review_items() if i.claim.id == result.claims[0].id)
        decision = CreateProfileReviewDecision(claim_id=item.claim.id, review_token=item.review_token,
            decision=ApprovalStatus.APPROVED, actor_id="synthetic-actor", idempotency_key=key + "-approval")
        self.service.decide_review_item(decision, now=REVIEW_NOW)
        return item.claim.id, request, decision

    def resolve_skill(self):
        return self.service.resolve(intent="skill_use", policy=ClaimUsePolicy(
            as_of=REVIEW_NOW + timedelta(days=1), allowed_sensitivities=frozenset({Sensitivity.PERSONAL}),
            require_confirmed_evidence=True))

    def test_withdrawal_blocks_use_and_preserves_import_and_approval_replay(self) -> None:
        claim_id, request, decision = self.approved("Python", "synthetic-old")
        original = self.repository.get_claim(claim_id)
        preview = self.lifecycle.retire(claim_id, actor_id="synthetic-actor", idempotency_key="retire-1")
        self.assertTrue(preview.dry_run)
        self.assertIsInstance(self.resolve_skill(), Resolved)
        result = self.lifecycle.retire(claim_id, actor_id="synthetic-actor", idempotency_key="retire-1", confirm=True, preview_token=preview.preview_token)
        self.assertTrue(result.recorded)
        self.assertIsInstance(self.resolve_skill(), NeedInfo)
        self.assertEqual(self.repository.get_claim(claim_id), original)
        self.assertEqual(self.service.validated_profile()[0][0].status, ClaimStatus.WITHDRAWN)
        self.service.create_import_proposal(request)
        self.service.decide_review_item(decision)
        self.assertTrue(self.lifecycle.retire(claim_id, actor_id="synthetic-actor", idempotency_key="retire-1", confirm=True, preview_token=preview.preview_token).replayed)

    def test_human_replacement_resolves_conflict_without_rewriting_values(self) -> None:
        old, _, _ = self.approved("Python", "synthetic-old")
        new, _, _ = self.approved("SQL", "synthetic-new")
        self.assertIsInstance(self.resolve_skill(), Contradiction)
        preview = self.lifecycle.retire(old, replacement_claim_id=new, actor_id="synthetic-actor", idempotency_key="retire-1")
        self.lifecycle.retire(old, replacement_claim_id=new, actor_id="synthetic-actor", idempotency_key="retire-1", confirm=True, preview_token=preview.preview_token)
        outcome = self.resolve_skill()
        self.assertIsInstance(outcome, Resolved)
        self.assertEqual(outcome.packet.value_json, "SQL")

    def test_changed_actor_or_replacement_or_key_cannot_reuse_preview(self) -> None:
        old, _, _ = self.approved("Python", "synthetic-old")
        preview = self.lifecycle.retire(old, actor_id="synthetic-actor", idempotency_key="retire-1")
        with self.assertRaises(ValueError):
            self.lifecycle.retire(old, actor_id="different", idempotency_key="retire-1", confirm=True, preview_token=preview.preview_token)
        self.assertEqual(self.repository.list_claim_retirements(), [])

    def test_retirement_audit_tampering_fails_resolution_closed(self) -> None:
        old, _, _ = self.approved("Python", "synthetic-old")
        preview = self.lifecycle.retire(old, actor_id="synthetic-actor", idempotency_key="retire-1")
        self.lifecycle.retire(old, actor_id="synthetic-actor", idempotency_key="retire-1", confirm=True, preview_token=preview.preview_token)
        record = self.repository.get_claim_retirement(old)
        # Deliberate corruption of disposable synthetic storage for adversarial QA.
        with closing(sqlite3.connect(self.repository.database)) as connection, connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM claim_retirements")
            connection.execute("UPDATE workflow_runs SET input_json = '{}' WHERE id = ?", (record["workflow_run_id"],))
        with self.assertRaisesRegex(Exception, "integrity"):
            self.resolve_skill()


if __name__ == "__main__":
    unittest.main()
