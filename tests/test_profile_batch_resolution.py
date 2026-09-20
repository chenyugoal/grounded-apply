from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from grounded_apply.domain import ApprovalStatus, Contradiction, NeedInfo, Resolved
from grounded_apply.repositories import RepositoryError, SQLiteRepository
from grounded_apply.services.matching import MatchingService, job_policy
from grounded_apply.services.profile import CreateProfileReviewDecision, ProfileService
from grounded_apply.services.profile_lifecycle import ProfileLifecycleService
from grounded_apply.services.resume_extraction import extract_resume
from tests.test_materials import approved_fixture
from tests.test_resume_extraction import RESUME


class BatchClaimResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "fictional.db"
        self.repository = SQLiteRepository(self.database).initialize()
        self.addCleanup(self.repository.close)
        self.job_id, self.claim_ids = approved_fixture(self.repository)
        self.service = ProfileService(self.repository)
        self.policy = job_policy(self.job_id)

    def test_bulk_preserves_order_unknowns_and_individual_resolution(self) -> None:
        selected = (*reversed(self.claim_ids), "missing-fictional-claim")
        expected = tuple(self.service.packet_for_claim(i, policy=self.policy) for i in selected)
        before = self.database.read_bytes()
        with patch.object(self.service, "validated_profile", wraps=self.service.validated_profile) as validate:
            result = self.service.packets_for_claims(selected, policy=self.policy)
        self.assertEqual(result, expected)
        self.assertEqual(validate.call_count, 1)
        self.assertIsInstance(result[-1], NeedInfo)
        self.assertEqual(self.database.read_bytes(), before)

    def test_same_service_does_not_reuse_snapshot_after_retirement(self) -> None:
        selected = (self.claim_ids[0],)
        self.assertIsInstance(self.service.packets_for_claims(selected, policy=self.policy)[0], Resolved)
        lifecycle = ProfileLifecycleService(self.repository)
        options = {"actor_id": "fictional-reviewer", "idempotency_key": "fictional-retirement"}
        preview = lifecycle.retire(selected[0], **options)
        lifecycle.retire(selected[0], **options, confirm=True, preview_token=preview.preview_token)
        self.assertIsInstance(self.service.packets_for_claims(selected, policy=self.policy)[0], NeedInfo)

    def test_same_service_rechecks_provenance_after_prior_success(self) -> None:
        self.service.packets_for_claims(self.claim_ids, policy=self.policy)
        association = self.repository.get_profile_import_review_item(self.claim_ids[0])
        # Deliberate corruption of fictional state exercises revalidation.
        self.repository._connection.execute(
            "UPDATE workflow_runs SET input_hash_sha256=? WHERE id=?",
            ("0" * 64, association["import_workflow_run_id"]))
        with self.assertRaises((RepositoryError, ValueError)):
            self.service.packets_for_claims(self.claim_ids, policy=self.policy)

    def test_bulk_does_not_hide_a_conflicting_singular_fact(self) -> None:
        original_name = next(c.id for c in self.service.validated_profile()[0]
                             if c.claim_type == "candidate_name")
        source = RESUME.replace("Name: Avery Quill", "Name: Bailey Fable")
        self.service.create_import_proposal(extract_resume(source).selected_request(
            (0,), source, "fictional-conflict"))
        item = self.service.list_review_items()[0]
        self.service.decide_review_item(CreateProfileReviewDecision(
            claim_id=item.claim.id, review_token=item.review_token,
            decision=ApprovalStatus.APPROVED, actor_id="fictional-reviewer",
            idempotency_key="fictional-conflict-approval"))
        result = self.service.packets_for_claims((original_name, *self.claim_ids), policy=self.policy)
        self.assertIsInstance(result[0], Contradiction)
        self.assertTrue(all(isinstance(item, Resolved) for item in result[1:]))

    def test_shape_and_budget_fail_before_profile_read(self) -> None:
        for selected in (["fictional"], ("bad id",), (None,), ("fictional",) * 501):
            with self.subTest(selected=type(selected)), patch.object(self.service, "validated_profile") as validate:
                with self.assertRaises(ValueError):
                    self.service.packets_for_claims(selected, policy=self.policy)
                validate.assert_not_called()

    def test_read_only_adapter_supports_the_same_bulk_resolution(self) -> None:
        expected = self.service.packets_for_claims(self.claim_ids, policy=self.policy)
        with SQLiteRepository(self.database, read_only=True).initialize() as repository:
            actual = ProfileService(repository).packets_for_claims(self.claim_ids, policy=self.policy)
        self.assertEqual(actual, expected)

    def test_matching_retrieves_same_evidence_with_bounded_profile_validation(self) -> None:
        service = MatchingService(self.repository)
        expected = service.assess(self.job_id)
        original = ProfileService.validated_profile
        with patch.object(ProfileService, "validated_profile", autospec=True, side_effect=original) as validate:
            actual = service.assess(self.job_id)
        self.assertEqual(actual, expected)
        self.assertEqual(validate.call_count, 2)
        self.assertIsNone(actual["hiring_probability"])


if __name__ == "__main__":
    unittest.main()
