from __future__ import annotations

import json
import sqlite3
import unittest
from datetime import timedelta, timezone
from unittest.mock import patch

from grounded_apply.repositories import RepositoryError, SQLiteRepository
from grounded_apply.services.applications import ApplicationService
from grounded_apply.services.materials import MaterialBlocked, MaterialService
from grounded_apply.services.matching import job_policy
from grounded_apply.services.profile import ProfileService
from grounded_apply.services.questionnaires import QuestionnaireService
from tests import test_material_approval_facts
from tests.test_material_approval_facts import APPROVAL_TIME
from tests.test_material_history import MATERIAL_AT
from tests.test_materials import SyntheticRenderer


READY_AT = APPROVAL_TIME + timedelta(days=1)
APPLIED_AT = READY_AT + timedelta(days=1)
ERROR = "Application historical material use failed integrity checks"


class ApplicationMaterialHistoryTests(unittest.TestCase):
    _build = test_material_approval_facts.MaterialApprovalFactsTests._build
    build = test_material_approval_facts.MaterialApprovalFactsTests.build
    record_approval = test_material_approval_facts.MaterialApprovalFactsTests.record_approval
    approve = test_material_approval_facts.MaterialApprovalFactsTests.approve
    retire = test_material_approval_facts.MaterialApprovalFactsTests.retire
    generic_claim = test_material_approval_facts.MaterialApprovalFactsTests.generic_claim
    imported_title = test_material_approval_facts.MaterialApprovalFactsTests.imported_title

    def setUp(self) -> None:
        test_material_approval_facts.MaterialApprovalFactsTests.setUp(self)
        self.applications = ApplicationService(self.repository, self.service)
        self.application_index = 0
        self.transition_index = 0

    def move(self, application_id: str, state: str, *, at=READY_AT, **fields):
        self.transition_index += 1
        options = {"actor_id": "fictional-user", "idempotency_key": f"fictional-transition-{self.transition_index}", **fields}
        with (
            patch("grounded_apply.services.applications.timestamp", return_value=at.isoformat()),
            patch("grounded_apply.services.materials.job_policy", return_value=job_policy(self.job_id, now=MATERIAL_AT)),
            patch("grounded_apply.services.questionnaires.job_policy", return_value=job_policy(self.job_id, now=MATERIAL_AT)),
        ):
            preview = self.applications.transition(application_id, state, **options)
            return self.applications.transition(application_id, state, **options,
                confirm=True, preview_token=preview["preview_token"])

    def preparing(self) -> str:
        self.application_index += 1
        with patch("grounded_apply.services.applications.timestamp", return_value=(MATERIAL_AT - timedelta(hours=3)).isoformat()):
            application_id = self.applications.add(self.job_id, actor_id="fictional-user",
                idempotency_key=f"fictional-application-{self.application_index}")["application_id"]
        self.move(application_id, "shortlisted", at=MATERIAL_AT - timedelta(hours=2))
        self.move(application_id, "preparing", at=MATERIAL_AT - timedelta(hours=1))
        return application_id

    def history(self, material_id: str, *, applied: bool = True) -> str:
        application_id = self.preparing()
        self.move(application_id, "ready_for_review", material_id=material_id)
        if applied:
            self.move(application_id, "applied", at=APPLIED_AT, material_id=material_id, confirm_submitted=True)
        return application_id

    def assert_rejected(self, application_id: str) -> None:
        before = self.repository._connection.serialize()
        with self.assertRaisesRegex(RepositoryError, f"^{ERROR}$"):
            self.applications.validate_historical_material_use(application_id)
        self.assertEqual(self.repository._connection.serialize(), before)
        self.assertFalse(self.repository._connection.in_transaction)

    def test_application_without_material_use_has_valid_unchanged_history(self) -> None:
        application_id = self.preparing()
        before = self.repository._connection.serialize()
        with (
            patch.object(self.service, "get", side_effect=AssertionError("No linked material")),
            patch.object(self.service, "is_approved", side_effect=AssertionError("No current readiness")),
            patch.object(ProfileService, "validated_profile", side_effect=AssertionError("No factual use")),
        ):
            self.assertIsNone(self.applications.validate_historical_material_use(application_id))
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_real_legacy_and_modern_material_use_preserves_exact_events_and_submission(self) -> None:
        for transformation in ("approved_text_selection@1", "approved_text_selection@2"):
            with self.subTest(transformation=transformation):
                material_id = self.build(transformation=transformation)
                self.approve(material_id)
                application_id = self.history(material_id)
                events = self.repository.list_application_events(application_id)
                submission = self.repository.get_submission_snapshot(application_id)
                before = self.repository._connection.serialize()
                self.assertIsNone(self.applications.validate_historical_material_use(application_id))
                self.assertEqual(self.repository.list_application_events(application_id), events)
                self.assertEqual(self.repository.get_submission_snapshot(application_id), submission)
                self.assertEqual(self.repository._connection.serialize(), before)

    def test_retirement_before_or_exactly_at_ready_refuses_historical_use(self) -> None:
        for index, retired_at in enumerate((APPROVAL_TIME + timedelta(hours=1), READY_AT)):
            with self.subTest(retired_at=retired_at):
                claim_id = self.imported_title(f"retired-ready-{index}", subject=f"fictional-ready-role-{index}")
                material_id = self.build((claim_id,))
                self.record_approval(material_id)
                application_id = self.history(material_id, applied=False)
                self.assertIsNone(self.applications.validate_historical_material_use(application_id))
                self.retire(claim_id, at=retired_at)
                self.assertIsNone(self.service.validate_historical_approval_eligibility(material_id))
                self.assertFalse(self.applications.get(application_id)["currently_ready"])
                self.assert_rejected(application_id)

    def test_expiry_is_half_open_at_ready_and_later_expiry_keeps_ready_history_valid(self) -> None:
        for index, expiry in enumerate((READY_AT - timedelta(seconds=1), READY_AT, READY_AT + timedelta(seconds=1))):
            with self.subTest(expiry=expiry):
                claim_id = self.generic_claim(f"expiry-ready-{index}", effective_to=expiry.isoformat())
                material_id = self.build((claim_id,))
                self.record_approval(material_id)
                application_id = self.history(material_id, applied=False)
                self.assertIsNone(self.service.validate_historical_approval_eligibility(material_id))
                if expiry <= READY_AT:
                    self.assert_rejected(application_id)
                else:
                    before = self.repository._connection.serialize()
                    self.assertIsNone(self.applications.validate_historical_material_use(application_id))
                    self.assertEqual(self.repository._connection.serialize(), before)

    def test_retirement_or_expiry_between_ready_and_applied_refuses_submission_use(self) -> None:
        for mode in ("retired", "expired"):
            with self.subTest(mode=mode):
                cutoff = READY_AT + timedelta(hours=12)
                claim_id = (self.imported_title("retired-submission", subject="fictional-submission-role")
                    if mode == "retired" else self.generic_claim("expiry-submission", effective_to=cutoff.isoformat()))
                material_id = self.build((claim_id,))
                self.record_approval(material_id)
                application_id = self.history(material_id)
                if mode == "retired":
                    self.retire(claim_id, at=cutoff)
                self.assertIsNone(self.service.validate_historical_use(material_id, used_at=READY_AT.isoformat()))
                self.assertEqual(self.applications.get(application_id)["state"], "applied")
                self.assert_rejected(application_id)

    def test_later_retirement_in_snapshot_uses_one_bundle_per_event_and_no_current_readiness(self) -> None:
        material_id = self.build()
        self.approve(material_id)
        application_id = self.preparing()
        ready_offset = READY_AT.astimezone(timezone(timedelta(hours=2)))
        applied_offset = APPLIED_AT.astimezone(timezone(timedelta(hours=-2)))
        self.move(application_id, "ready_for_review", at=ready_offset, material_id=material_id)
        self.move(application_id, "applied", at=applied_offset, material_id=material_id, confirm_submitted=True)
        self.retire(self.claim_ids[0], at=APPLIED_AT + timedelta(seconds=1))
        with self.assertRaises(MaterialBlocked):
            self.service.get(material_id)
        submission = self.repository.get_submission_snapshot(application_id)
        source = self.repository._connection.serialize()
        with SQLiteRepository.from_snapshot(source) as repository:
            renderer = SyntheticRenderer()
            materials = MaterialService(repository, renderer)
            service = ApplicationService(repository, materials)
            original_profile = ProfileService.validated_profile
            profiles = []

            def profile_read(profile: ProfileService, *, apply_retirements: bool = True):
                profiles.append((apply_retirements, repository._connection.in_transaction))
                return original_profile(profile, apply_retirements=apply_retirements)

            statements = []
            repository._connection.set_trace_callback(statements.append)
            try:
                with (
                    patch.object(materials, "get", wraps=materials.get) as bundles,
                    patch.object(renderer, "validate", wraps=renderer.validate) as pdfs,
                    patch.object(ProfileService, "validated_profile", autospec=True, side_effect=profile_read),
                    patch.object(service, "get", side_effect=AssertionError("No current application readiness")),
                    patch.object(materials, "is_approved", side_effect=AssertionError("No current approval query")),
                    patch.object(materials, "plan", side_effect=AssertionError("No current material plan")),
                    patch.object(materials, "validate_historical_use", side_effect=AssertionError("Do not chain public material reads")),
                    patch.object(QuestionnaireService, "prepare", side_effect=AssertionError("No current answers")),
                    # This test renderer internally rebuilds its comparison in
                    # validate; an extra render beyond those calls is forbidden.
                    patch.object(renderer, "render", wraps=renderer.render) as render_checks,
                ):
                    self.assertIsNone(service.validate_historical_material_use(application_id))
                self.assertEqual(bundles.call_count, 2)
                self.assertEqual(pdfs.call_count, 2)
                self.assertEqual(render_checks.call_count, pdfs.call_count)
                self.assertTrue(all(call.args == (material_id,) and call.kwargs == {"require_current": False}
                    for call in bundles.call_args_list))
            finally:
                repository._connection.set_trace_callback(None)
            self.assertEqual(profiles, [(False, True), (False, True)])
            self.assertEqual(sum(sql == "BEGIN" for sql in statements), 1)
            self.assertEqual(sum(sql == "ROLLBACK" for sql in statements), 1)
            self.assertEqual(repository.get_submission_snapshot(application_id), submission)
            self.assertFalse(repository._connection.in_transaction)
        self.assertEqual(self.repository._connection.serialize(), source)

    def test_all_old_ready_events_are_audited_when_a_new_material_is_later_submitted(self) -> None:
        old_claim = self.generic_claim("old-expired-material", effective_to=READY_AT.isoformat())
        old_material = self.build((old_claim,))
        self.record_approval(old_material)
        new_material = self.build()
        self.record_approval(new_material)
        application_id = self.history(old_material, applied=False)
        self.move(application_id, "preparing", at=READY_AT + timedelta(hours=1))
        self.move(application_id, "ready_for_review", at=READY_AT + timedelta(hours=2), material_id=new_material)
        self.move(application_id, "applied", at=APPLIED_AT, material_id=new_material, confirm_submitted=True)
        self.assertIsNone(self.service.validate_historical_use(new_material, used_at=APPLIED_AT.isoformat()))
        self.assertEqual(self.applications.get(application_id)["state"], "applied")
        self.assert_rejected(application_id)

    def test_old_material_retired_after_its_only_use_keeps_full_repreparation_history(self) -> None:
        claim_id = self.imported_title("older-valid-material", subject="fictional-older-role")
        old_material = self.build((claim_id,))
        self.record_approval(old_material)
        new_material = self.build()
        self.record_approval(new_material)
        application_id = self.history(old_material, applied=False)
        self.move(application_id, "preparing", at=READY_AT + timedelta(hours=1))
        self.move(application_id, "ready_for_review", at=READY_AT + timedelta(hours=2), material_id=new_material)
        self.move(application_id, "applied", at=APPLIED_AT, material_id=new_material, confirm_submitted=True)
        self.retire(claim_id, at=READY_AT + timedelta(hours=1))
        before = self.repository._connection.serialize()
        with patch.object(self.service, "get", wraps=self.service.get) as bundles:
            self.assertIsNone(self.applications.validate_historical_material_use(application_id))
        self.assertEqual([call.args[0] for call in bundles.call_args_list], [old_material, new_material, new_material])
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_required_unanswered_material_use_fails_while_optional_need_info_remains_history(self) -> None:
        for required in (False, True):
            with self.subTest(required=required):
                material_id = self.build(questions=[{"id": "fictional-signature", "text": "Please sign this experience statement.",
                    "claim_ids": [self.claim_ids[0]], "required": required}])
                self.record_approval(material_id)
                # Only the isolated fixture admits a record that normal readiness
                # correctly refuses, preserving the rest of the real event chain.
                with patch.object(self.service, "is_approved", return_value=True):
                    application_id = self.history(material_id, applied=False)
                if required:
                    self.assert_rejected(application_id)
                else:
                    before = self.repository._connection.serialize()
                    self.assertIsNone(self.applications.validate_historical_material_use(application_id))
                    self.assertEqual(self.repository._connection.serialize(), before)

    def test_linked_job_bundle_and_submission_bindings_are_not_bypassed(self) -> None:
        material_id = self.build()
        self.approve(material_id)
        application_id = self.history(material_id)
        material = self.service.get(material_id, require_current=False)
        for field, value in (("job_id", "fictional-foreign-job"), ("bundle_sha256", "0" * 64)):
            with self.subTest(field=field), patch.object(self.service, "get", return_value={**material, field: value}):
                self.assert_rejected(application_id)
        snapshot = self.repository.get_submission_snapshot(application_id)
        content = json.loads(snapshot["snapshot_json"])
        content["material_id"] = "fictional-foreign-material"
        for changed in ({**snapshot, "submitted_at": READY_AT.isoformat()},
                        {**snapshot, "snapshot_json": json.dumps(content)},
                        {**snapshot, "snapshot_sha256": "0" * 64}):
            with self.subTest(snapshot_fields=tuple(key for key in changed if changed[key] != snapshot[key])), \
                patch.object(self.repository, "get_submission_snapshot", return_value=changed):
                self.assert_rejected(application_id)

    def test_fixed_failures_hide_missing_records_storage_details_and_invalid_use_approval(self) -> None:
        self.assert_rejected("fictional-missing-application")
        material_id = self.build()
        self.approve(material_id)
        application_id = self.history(material_id)
        for method in ("get_application", "list_application_events", "get_material_approval", "get_evidence"):
            with self.subTest(method=method), patch.object(self.repository, method,
                side_effect=sqlite3.OperationalError("fictional-private-storage-detail")):
                self.assert_rejected(application_id)
        with patch.object(self.repository, "get_material_approval", return_value=None):
            self.assert_rejected(application_id)

    def test_borrowed_transaction_preserves_caller_writes_on_success_and_failure(self) -> None:
        material_id = self.build()
        self.approve(material_id)
        application_id = self.history(material_id)
        before = self.repository._connection.serialize()
        abort = RuntimeError("fictional caller rollback")
        with self.assertRaises(RuntimeError) as raised:
            with self.repository.transaction():
                self.repository.add_workflow_run(run_id="fictional-caller-work", workflow_type="fictional-caller-work", status="queued")
                self.assertIsNone(self.applications.validate_historical_material_use(application_id))
                with patch.object(self.repository, "get_material_approval", return_value=None):
                    with self.assertRaisesRegex(RepositoryError, f"^{ERROR}$"):
                        self.applications.validate_historical_material_use(application_id)
                self.assertTrue(self.repository._connection.in_transaction)
                self.assertIsNotNone(self.repository.get_workflow_run("fictional-caller-work"))
                raise abort
        self.assertIs(raised.exception, abort)
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_interrupt_identity_is_preserved_and_owned_read_transaction_is_released(self) -> None:
        material_id = self.build()
        self.approve(material_id)
        application_id = self.history(material_id)
        before = self.repository._connection.serialize()
        interrupt = KeyboardInterrupt("fictional cancellation")
        with patch.object(ProfileService, "validated_profile", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt) as raised:
                self.applications.validate_historical_material_use(application_id)
        self.assertIs(raised.exception, interrupt)
        self.assertFalse(self.repository._connection.in_transaction)
        self.assertEqual(self.repository._connection.serialize(), before)


if __name__ == "__main__":
    unittest.main()
