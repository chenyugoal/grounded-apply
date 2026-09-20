from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from grounded_apply.domain import (
    ApprovalStatus, ClaimStatus, EvidenceConfirmationStatus, Scope, ScopeType, SourceType,
)
from grounded_apply.repositories import RepositoryError, SQLiteRepository
from grounded_apply.services.matching import job_policy
from grounded_apply.services.materials import MaterialBlocked, MaterialService
from grounded_apply.services.profile import CreateClaim, CreateEvidence, ProfileService
from grounded_apply.services.profile_lifecycle import ProfileLifecycleService
from tests.test_materials import SyntheticRenderer, approved_fixture


class MaterialProfileResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="gapply-synthetic-material-resolution-")
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "fictional.db"
        self.repository = SQLiteRepository(self.database).initialize()
        self.addCleanup(self.repository.close)
        self.job_id, self.claim_ids = approved_fixture(self.repository)
        self.profile = ProfileService(self.repository)
        self.service = MaterialService(self.repository, SyntheticRenderer())
        claims, _ = self.profile.validated_profile()
        self.by_id = {claim.id: claim for claim in claims}
        self.name = next(claim.id for claim in claims if claim.claim_type == "candidate_name")
        self.email = next(claim.id for claim in claims if claim.claim_type == "contact_email")

    def claim(self, *, claim_type: str = "project_description", text: str = "Contributed to a fictional parser.", **changes) -> str:
        created = self.profile.create_claim(CreateClaim(claim_type=claim_type, value=text,
            canonical_text=text, source_type=SourceType.USER_STATEMENT, source_ref="synthetic-review",
            approval_status=ApprovalStatus.APPROVED, verified_by="fictional-reviewer",
            **{"status": ClaimStatus.VERIFIED, **changes}))
        self.profile.create_evidence(CreateEvidence(claim_id=created.id, source_type=SourceType.USER_STATEMENT,
            source_ref="synthetic-review", source_text=text,
            confirmation_status=EvidenceConfirmationStatus.CONFIRMED, confirmed_by="fictional-reviewer"))
        return created.id

    def retire(self, claim_id: str) -> None:
        lifecycle = ProfileLifecycleService(self.repository)
        options = {"actor_id": "fictional-reviewer", "idempotency_key": "retire-" + claim_id}
        preview = lifecycle.retire(claim_id, **options)
        lifecycle.retire(claim_id, **options, confirm=True, preview_token=preview.preview_token)

    def test_auto_and_explicit_contacts_preserve_order_from_one_fresh_snapshot(self) -> None:
        cases = ((self.claim_ids, (*self.claim_ids, self.name, self.email)),
            ((self.email, *reversed(self.claim_ids)), (self.email, *reversed(self.claim_ids), self.name)),
            ((self.name, self.email, *self.claim_ids), (self.name, self.email, *self.claim_ids)))
        original = ProfileService.validated_profile
        before = self.database.read_bytes()
        for selected, expected in cases:
            with self.subTest(selected=selected), patch.object(ProfileService, "validated_profile", autospec=True, side_effect=original) as validate:
                structure = self.service.plan(self.job_id, selected)
                self.assertEqual(validate.call_count, 1)
            self.assertEqual(tuple(unit.claim_id for unit in structure.units), expected)
            self.assertEqual(tuple(unit.text for unit in structure.units), tuple(self.by_id[identifier].canonical_text for identifier in expected))
            self.assertTrue(all(unit.claim_id in unit.packet_claim_ids and unit.evidence_ids for unit in structure.units))
        self.assertEqual(self.database.read_bytes(), before)

    def test_unknown_selection_has_structured_need_info_without_inventing_evidence(self) -> None:
        original = ProfileService.validated_profile
        with patch.object(ProfileService, "validated_profile", autospec=True, side_effect=original) as validate:
            with self.assertRaises(MaterialBlocked) as blocked:
                self.service.plan(self.job_id, (*self.claim_ids, "fictional-missing-claim"))
        self.assertEqual(validate.call_count, 1)
        self.assertTrue(any(outcome["kind"] == "need_info" and outcome["intent"] == "selected_claim"
            and outcome["reason"] == "missing" for outcome in blocked.exception.outcomes))

    def assert_contact_conflict(self, original_id: str, text: str) -> None:
        original = self.by_id[original_id]
        other = self.claim(claim_type=original.claim_type, text=text,
            subject_type=original.subject_type, subject_id=original.subject_id)
        for selected in (self.claim_ids, (original_id, *self.claim_ids), (other, *self.claim_ids)):
            with self.subTest(kind=original.claim_type, selected=selected), self.assertRaises(MaterialBlocked) as blocked:
                self.service.plan(self.job_id, selected)
            self.assertTrue(any(outcome["kind"] == "contradiction" and outcome["intent"] == original.claim_type
                for outcome in blocked.exception.outcomes))

    def test_same_subject_name_conflicts_block_automatic_and_explicit_selection(self) -> None:
        self.assert_contact_conflict(self.name, "Bailey Fable")

    def test_same_subject_email_conflicts_block_automatic_and_explicit_selection(self) -> None:
        self.assert_contact_conflict(self.email, "bailey.fable@example.com")

    def test_independent_career_facts_remain_usable_but_contradicted_peer_blocks(self) -> None:
        first = self.claim(subject_type="project", subject_id="fictional-project")
        second = self.claim(text="Tested the same fictional parser.", subject_type="project", subject_id="fictional-project")
        self.assertEqual(tuple(unit.claim_id for unit in self.service.plan(self.job_id, (first, second)).units[:2]), (first, second))
        self.claim(text="Did not contribute to this fictional parser.", subject_type="project",
            subject_id="fictional-project", status=ClaimStatus.CONTRADICTED)
        with self.assertRaises(MaterialBlocked) as blocked:
            self.service.plan(self.job_id, (first,))
        self.assertTrue(any(outcome["kind"] == "contradiction" for outcome in blocked.exception.outcomes))

    def test_scope_remains_exact_and_automatic_contacts_do_not_expand_it(self) -> None:
        allowed = self.claim(scope=Scope(type=ScopeType.JOB, id=self.job_id))
        other = self.claim(text="Contributed to a different fictional parser.",
            scope=Scope(type=ScopeType.JOB, id="fictional-other-job"))
        self.assertEqual(self.service.plan(self.job_id, (allowed,)).units[0].claim_id, allowed)
        with self.assertRaises(MaterialBlocked) as blocked:
            self.service.plan(self.job_id, (other,))
        self.assertTrue(any(outcome.get("reason") == "out_of_scope" for outcome in blocked.exception.outcomes))

    def test_time_boundaries_are_rechecked_on_each_plan_call(self) -> None:
        moment = datetime.now(UTC)
        selected = self.claim(effective_from=moment - timedelta(hours=1), effective_to=moment + timedelta(hours=1))
        with patch("grounded_apply.services.materials.job_policy", return_value=job_policy(self.job_id, now=moment)):
            self.assertEqual(self.service.plan(self.job_id, (selected,)).units[0].claim_id, selected)
        for delta, reason in ((timedelta(hours=1), "stale"), (timedelta(hours=-2), "not_yet_effective")):
            with self.subTest(reason=reason), patch("grounded_apply.services.materials.job_policy", return_value=job_policy(self.job_id, now=moment + delta)):
                with self.assertRaises(MaterialBlocked) as blocked:
                    self.service.plan(self.job_id, (selected,))
                self.assertTrue(any(outcome.get("reason") == reason for outcome in blocked.exception.outcomes))

    def test_later_plan_does_not_reuse_retired_contact_authority(self) -> None:
        self.service.plan(self.job_id, self.claim_ids)
        self.retire(self.email)
        with self.assertRaises(MaterialBlocked) as blocked:
            self.service.plan(self.job_id, self.claim_ids)
        self.assertTrue(any(outcome.get("intent") == "contact_email" for outcome in blocked.exception.outcomes))

    def test_later_plan_revalidates_changed_import_provenance(self) -> None:
        self.service.plan(self.job_id, self.claim_ids)
        association = self.repository.get_profile_import_review_item(self.name)
        # Deliberate corruption of an isolated fictional fixture must not be
        # hidden by a previously successful plan or automatic contact lookup.
        self.repository._connection.execute("UPDATE workflow_runs SET input_hash_sha256=? WHERE id=?",
            ("0" * 64, association["import_workflow_run_id"]))
        with self.assertRaises(RepositoryError):
            self.service.plan(self.job_id, self.claim_ids)

    def test_contact_retired_while_rendering_blocks_commit_from_new_snapshot(self) -> None:
        base = SyntheticRenderer()
        fixture = self

        class RetiringRenderer:
            def render(self, structure):
                rendered = base.render(structure)
                fixture.retire(fixture.email)
                return rendered

            def validate(self, structure, rendered):
                base.validate(structure, rendered)

        with self.assertRaises(MaterialBlocked):
            MaterialService(self.repository, RetiringRenderer()).build(self.job_id, self.claim_ids,
                idempotency_key="fictional-render-retirement")
        self.assertEqual(self.repository.list_material_ids(), ())

    def test_read_only_plan_preserves_database_and_sidecars(self) -> None:
        expected = self.service.plan(self.job_id, self.claim_ids)
        before = self.database.read_bytes()
        with SQLiteRepository(self.database, read_only=True, existing_only=True).initialize() as repository:
            actual = MaterialService(repository, SyntheticRenderer()).plan(self.job_id, self.claim_ids)
        self.assertEqual(actual, expected)
        self.assertEqual(self.database.read_bytes(), before)
        self.assertFalse(any(Path(str(self.database) + suffix).exists() for suffix in ("-journal", "-wal", "-shm")))


if __name__ == "__main__":
    unittest.main()
