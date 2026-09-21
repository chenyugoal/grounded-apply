from __future__ import annotations

import sqlite3
import unittest
from datetime import timedelta
from unittest.mock import patch

from grounded_apply.repositories import RepositoryError, SQLiteRepository
from grounded_apply.services.materials import MaterialBlocked, MaterialHistoryIntegrityError, MaterialService
from grounded_apply.services.matching import job_policy
from grounded_apply.services.profile import ProfileService
from grounded_apply.services.questionnaires import QuestionnaireService
from tests import test_material_approval_facts
from tests.test_material_approval_facts import APPROVAL_TIME, APPROVED_AT, BETWEEN
from tests.test_materials import SyntheticRenderer


ERROR = "Material historical approval eligibility failed integrity checks"


class MaterialApprovalEligibilityTests(unittest.TestCase):
    # Reuse fixture methods only; the existing fact tests remain separate cases.
    setUp = test_material_approval_facts.MaterialApprovalFactsTests.setUp
    _build = test_material_approval_facts.MaterialApprovalFactsTests._build
    build = test_material_approval_facts.MaterialApprovalFactsTests.build
    record_approval = test_material_approval_facts.MaterialApprovalFactsTests.record_approval
    approve = test_material_approval_facts.MaterialApprovalFactsTests.approve
    retire = test_material_approval_facts.MaterialApprovalFactsTests.retire
    generic_claim = test_material_approval_facts.MaterialApprovalFactsTests.generic_claim
    mutate_approval = test_material_approval_facts.MaterialApprovalFactsTests.mutate_approval
    rewrite_material = test_material_approval_facts.MaterialApprovalFactsTests.rewrite_material
    refresh_packet_hashes = test_material_approval_facts.MaterialApprovalFactsTests.refresh_packet_hashes

    def career(self, *, identifier: str = "fictional-career", required: bool = True,
               claim_id: str | None = None) -> dict:
        return {"id": identifier, "text": "Describe your project experience.",
                "claim_ids": [claim_id or self.claim_ids[0]], "required": required}

    def unanswered(self, *, required: bool = True) -> dict:
        return {"id": "fictional-human-answer", "text": "Please sign this statement about your experience.",
                "claim_ids": [self.claim_ids[0]], "required": required}

    def assert_rejected(self, material_id: str) -> None:
        before = self.repository._connection.serialize()
        with self.assertRaisesRegex(MaterialHistoryIntegrityError, f"^{ERROR}$"):
            self.service.validate_historical_approval_eligibility(material_id)
        self.assertFalse(self.repository._connection.in_transaction)
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_real_v1_v2_approvals_with_no_questions_or_required_drafts_remain_valid(self) -> None:
        for transformation in ("approved_text_selection@1", "approved_text_selection@2"):
            for questions in ((), [self.career()]):
                with self.subTest(transformation=transformation, questions=bool(questions)):
                    material_id = self.build(transformation=transformation, questions=questions)
                    self.approve(material_id)
                    original = self.repository.get_material_version(material_id)
                    before = self.repository._connection.serialize()
                    self.assertIsNone(self.service.validate_historical_approval_eligibility(material_id))
                    self.assertEqual(self.repository.get_material_version(material_id), original)
                    self.assertEqual(self.repository._connection.serialize(), before)

    def test_mixed_required_draft_and_optional_need_info_are_valid_but_required_need_info_is_not(self) -> None:
        for required in (False, True):
            with self.subTest(unanswered_required=required):
                material_id = self.build(questions=[self.career(), self.unanswered(required=required)])
                self.record_approval(material_id)
                answers = self.service.get(material_id, require_current=False)["manifest"]["answers"]
                self.assertEqual([answer["status"] for answer in answers], ["draft", "need_info"])
                before = self.repository._connection.serialize()
                self.assertIsNone(self.service.validate_historical_approval_record(material_id))
                self.assertIsNone(self.service.validate_historical_approval_facts(material_id))
                if required:
                    self.assert_rejected(material_id)
                else:
                    self.assertIsNone(self.service.validate_historical_approval_eligibility(material_id))
                self.assertIsNone(answers[1]["answer"])
                self.assertEqual(answers[1]["factual_units"], [])
                self.assertEqual(self.repository._connection.serialize(), before)

    def test_later_fact_approval_does_not_fill_recorded_required_need_info(self) -> None:
        for required in (False, True):
            with self.subTest(required=required):
                claim = self.generic_claim(f"pending-answer-{required}", status="needs_review", approval_status="pending",
                    verified_at=None, verified_by=None)
                question = self.career(claim_id=claim, required=required)
                material_id = self.build(questions=[question])
                self.record_approval(material_id)
                original = self.service.get(material_id, require_current=False)["manifest"]["answers"]
                self.assertEqual(original[0]["status"], "need_info")
                self.repository._connection.execute("""UPDATE claims SET status = 'verified', approval_status = 'approved',
                    verified_at = ?, verified_by = 'fictional-reviewer', updated_at = ? WHERE id = ?""",
                    (BETWEEN.isoformat(), BETWEEN.isoformat(), claim))
                with patch("grounded_apply.services.questionnaires.job_policy", return_value=job_policy(self.job_id, now=APPROVAL_TIME)):
                    self.assertEqual(QuestionnaireService(self.repository).prepare(self.job_id, [question])[0]["status"], "draft")
                self.assertIsNone(self.service.validate_historical_approval_facts(material_id))
                before = self.repository._connection.serialize()
                with patch.object(QuestionnaireService, "prepare", side_effect=AssertionError("Do not replace saved answers")):
                    if required:
                        self.assert_rejected(material_id)
                    else:
                        self.assertIsNone(self.service.validate_historical_approval_eligibility(material_id))
                self.assertEqual(self.service.get(material_id, require_current=False)["manifest"]["answers"], original)
                self.assertEqual(self.repository._connection.serialize(), before)

    def test_absent_approval_for_partial_material_is_valid_history_without_profile_or_approval_creation(self) -> None:
        material_id = self.build(questions=[self.unanswered()])
        before = self.repository._connection.serialize()
        with patch.object(ProfileService, "validated_profile", side_effect=AssertionError("No approval to evaluate")):
            self.assertIsNone(self.service.validate_historical_approval_eligibility(material_id))
        self.assertIsNone(self.repository.get_material_approval(material_id))
        self.assertEqual(self.repository._connection.serialize(), before)
        with patch.object(self.service, "get", side_effect=ValueError("fictional-corrupt-bundle")):
            self.assert_rejected(material_id)

    def test_required_need_info_is_checked_after_closed_fact_validation(self) -> None:
        material_id = self.build(questions=[self.unanswered()])
        self.record_approval(material_id)
        before = self.repository._connection.serialize()
        interrupt = KeyboardInterrupt("fictional interruption during fact audit")
        with patch.object(ProfileService, "validated_profile", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt) as raised:
                self.service.validate_historical_approval_eligibility(material_id)
        self.assertIs(raised.exception, interrupt)
        self.assertFalse(self.repository._connection.in_transaction)
        self.assertEqual(self.repository._connection.serialize(), before)
        self.assert_rejected(material_id)

    def test_optional_unanswered_rows_still_require_closed_answer_shapes(self) -> None:
        material_id = self.build(questions=[self.unanswered(required=False)])
        self.rewrite_material(material_id, edit_manifest=lambda manifest: manifest["answers"][0].__setitem__("status", "approved"))
        self.record_approval(material_id)
        self.assert_rejected(material_id)

    def test_combined_gate_keeps_creation_and_approval_time_factual_checks(self) -> None:
        for mode in ("late-authority", "expired", "retired"):
            with self.subTest(mode=mode):
                if mode == "retired":
                    claim = self.claim_ids[0]
                else:
                    claim = self.generic_claim(f"clock-{mode}", **({"effective_to": APPROVED_AT} if mode == "expired" else {}))
                material_id = self.build((claim,))
                if mode == "late-authority":
                    self.repository._connection.execute("UPDATE claims SET verified_at = ? WHERE id = ?", (BETWEEN.isoformat(), claim))
                    self.rewrite_material(material_id, edit_structure=self.refresh_packet_hashes)
                self.record_approval(material_id)
                if mode == "retired":
                    self.retire(claim, at=APPROVAL_TIME)
                self.assertIsNone(self.service.validate_historical_approval_record(material_id))
                self.assert_rejected(material_id)

    def test_later_retired_snapshot_uses_one_bundle_pdf_profile_and_transaction_without_public_audit_chaining(self) -> None:
        material_id = self.build(questions=[self.career(), self.unanswered(required=False)])
        self.approve(material_id)
        self.retire(self.claim_ids[0], at=APPROVAL_TIME + timedelta(seconds=1))
        with self.assertRaises(MaterialBlocked):
            self.service.get(material_id)
        snapshot = self.repository._connection.serialize()
        with SQLiteRepository.from_snapshot(snapshot) as repository:
            renderer = SyntheticRenderer()
            service = MaterialService(repository, renderer)
            original = ProfileService.validated_profile
            profiles = []

            def profile_read(profile: ProfileService, *, apply_retirements: bool = True):
                profiles.append((apply_retirements, repository._connection.in_transaction))
                return original(profile, apply_retirements=apply_retirements)

            statements = []
            repository._connection.set_trace_callback(statements.append)
            try:
                with (
                    patch.object(service, "get", wraps=service.get) as bundle,
                    patch.object(renderer, "validate", wraps=renderer.validate) as pdf,
                    patch.object(ProfileService, "validated_profile", autospec=True, side_effect=profile_read),
                    patch.object(service, "validate_historical_facts", side_effect=AssertionError("No public audit chaining")),
                    patch.object(service, "validate_historical_approval_record", side_effect=AssertionError("No public audit chaining")),
                    patch.object(service, "validate_historical_approval_facts", side_effect=AssertionError("No public audit chaining")),
                    patch.object(service, "plan", side_effect=AssertionError("No current plan")),
                    patch.object(service, "approve", side_effect=AssertionError("No approval creation")),
                    patch.object(service, "is_approved", side_effect=AssertionError("No current readiness")),
                    patch.object(QuestionnaireService, "prepare", side_effect=AssertionError("No current answers")),
                ):
                    self.assertIsNone(service.validate_historical_approval_eligibility(material_id))
                bundle.assert_called_once_with(material_id, require_current=False)
                self.assertEqual(pdf.call_count, 1)
            finally:
                repository._connection.set_trace_callback(None)
            self.assertEqual(profiles, [(False, True)])
            self.assertEqual(sum(sql == "BEGIN" for sql in statements), 1)
            self.assertEqual(sum(sql == "ROLLBACK" for sql in statements), 1)
            self.assertFalse(repository._connection.in_transaction)
        self.assertEqual(self.repository._connection.serialize(), snapshot)

    def test_corrupt_present_approval_stops_before_fact_validation_even_with_required_need_info(self) -> None:
        material_id = self.build(questions=[self.unanswered()])
        self.record_approval(material_id)
        self.mutate_approval(material_id, lambda approval, workflow, payload: payload.__setitem__("version", True), rehash=False)
        with patch.object(ProfileService, "validated_profile", side_effect=AssertionError("Invalid approval must stop first")):
            self.assert_rejected(material_id)

    def test_success_and_incomplete_answer_failure_preserve_borrowed_caller_work(self) -> None:
        complete = self.build(questions=[self.career()])
        self.record_approval(complete)
        partial = self.build(questions=[self.unanswered()])
        self.record_approval(partial)
        before = self.repository._connection.serialize()
        abort = RuntimeError("fictional caller rollback")
        with self.assertRaises(RuntimeError) as raised:
            with self.repository.transaction():
                self.repository.add_workflow_run(run_id="fictional-caller-work", workflow_type="fictional-caller-work", status="queued")
                self.assertIsNone(self.service.validate_historical_approval_eligibility(complete))
                with self.assertRaisesRegex(MaterialHistoryIntegrityError, f"^{ERROR}$"):
                    self.service.validate_historical_approval_eligibility(partial)
                self.assertTrue(self.repository._connection.in_transaction)
                self.assertIsNotNone(self.repository.get_workflow_run("fictional-caller-work"))
                raise abort
        self.assertIs(raised.exception, abort)
        self.assertIsNone(self.repository.get_workflow_run("fictional-caller-work"))
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_fixed_fatal_errors_hide_record_and_storage_details(self) -> None:
        self.assertTrue(issubclass(MaterialHistoryIntegrityError, RepositoryError))
        self.assertFalse(issubclass(MaterialHistoryIntegrityError, MaterialBlocked))
        self.assert_rejected("fictional-missing-material")
        material_id = self.build(questions=[self.career()])
        self.record_approval(material_id)
        for method in ("get_material_version", "get_material_approval", "get_evidence"):
            with self.subTest(method=method), patch.object(self.repository, method,
                side_effect=sqlite3.OperationalError("fictional-private-storage-detail")):
                self.assert_rejected(material_id)

    def test_interrupt_identity_and_owned_read_transaction_cleanup_are_preserved(self) -> None:
        material_id = self.build(questions=[self.career()])
        self.record_approval(material_id)
        before = self.repository._connection.serialize()
        interrupt = KeyboardInterrupt("fictional cancellation")
        with patch.object(self.repository, "get_material_approval", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt) as raised:
                self.service.validate_historical_approval_eligibility(material_id)
        self.assertIs(raised.exception, interrupt)
        self.assertFalse(self.repository._connection.in_transaction)
        self.assertEqual(self.repository._connection.serialize(), before)


if __name__ == "__main__":
    unittest.main()
