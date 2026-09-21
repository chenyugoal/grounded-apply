from __future__ import annotations

import json
import sqlite3
import unittest
from datetime import timedelta
from unittest.mock import patch

from grounded_apply.repositories import RepositoryError, SQLiteRepository
from grounded_apply.services.jobs import JobService
from grounded_apply.services.material_history import validate_material_history
from grounded_apply.services.materials import MaterialBlocked, MaterialHistoryIntegrityError, MaterialService
from grounded_apply.services.profile import ProfileService
from grounded_apply.services.questionnaires import QuestionnaireService
from tests import test_material_approval_facts, test_material_approval_eligibility
from tests.test_material_approval_facts import APPROVAL_TIME, APPROVED_AT, BETWEEN
from tests.test_materials import SyntheticRenderer


USE_TIME = APPROVAL_TIME + timedelta(days=1)
USED_AT = USE_TIME.isoformat()
BEFORE_USE = APPROVAL_TIME + timedelta(hours=12)
ERROR = "Material historical use failed integrity checks"


class MaterialUseHistoryTests(unittest.TestCase):
    # Reuse fixture methods without inheriting and rerunning their test cases.
    setUp = test_material_approval_facts.MaterialApprovalFactsTests.setUp
    _build = test_material_approval_facts.MaterialApprovalFactsTests._build
    build = test_material_approval_facts.MaterialApprovalFactsTests.build
    record_approval = test_material_approval_facts.MaterialApprovalFactsTests.record_approval
    approve = test_material_approval_facts.MaterialApprovalFactsTests.approve
    retire = test_material_approval_facts.MaterialApprovalFactsTests.retire
    generic_claim = test_material_approval_facts.MaterialApprovalFactsTests.generic_claim
    imported_title = test_material_approval_facts.MaterialApprovalFactsTests.imported_title
    answer_question = test_material_approval_facts.MaterialApprovalFactsTests.answer_question
    rewrite_material = test_material_approval_facts.MaterialApprovalFactsTests.rewrite_material
    refresh_packet_hashes = test_material_approval_facts.MaterialApprovalFactsTests.refresh_packet_hashes
    mutate_approval = test_material_approval_facts.MaterialApprovalFactsTests.mutate_approval
    unanswered = test_material_approval_eligibility.MaterialApprovalEligibilityTests.unanswered

    def assert_rejected(self, material_id: str, *, used_at: object = USED_AT) -> None:
        before = self.repository._connection.serialize()
        with self.assertRaisesRegex(MaterialHistoryIntegrityError, f"^{ERROR}$"):
            self.service.validate_historical_use(material_id, used_at=used_at)
        self.assertEqual(self.repository._connection.serialize(), before)
        self.assertFalse(self.repository._connection.in_transaction)

    def test_real_legacy_and_modern_approvals_keep_exact_packets_and_answers(self) -> None:
        for transformation in ("approved_text_selection@1", "approved_text_selection@2"):
            with self.subTest(transformation=transformation):
                questions = self.answer_question(self.claim_ids[0]) + [self.unanswered(required=False)]
                material_id = self.build(transformation=transformation, questions=questions)
                self.approve(material_id)
                original = self.repository.get_material_version(material_id)
                before = self.repository._connection.serialize()
                self.assertIsNone(self.service.validate_historical_use(material_id, used_at=USED_AT))
                # Use may equal approval as an instant, without normalizing bytes.
                self.assertIsNone(self.service.validate_historical_use(material_id, used_at="2030-01-01T19:00:00-05:00"))
                self.assertEqual(self.repository.get_material_version(material_id), original)
                self.assertEqual(self.repository._connection.serialize(), before)

    def test_absent_approval_is_fatal_without_reading_profile_or_creating_approval(self) -> None:
        material_id = self.build()
        with patch.object(ProfileService, "validated_profile", side_effect=AssertionError("No authority without approval")):
            self.assert_rejected(material_id)
            self.assertIsNone(self.service.validate_historical_approval_eligibility(material_id))
        self.assertIsNone(self.repository.get_material_approval(material_id))

    def test_public_use_clock_rejects_missing_mistyped_naive_and_preapproval_times(self) -> None:
        material_id = self.build()
        self.record_approval(material_id)
        for at in (None, False, 1, 1.0, "", "fictional-invalid-time", "2030-01-03T00:00:00",
                   BETWEEN.isoformat(), "2030-01-02T00:30:00+01:00"):
            with self.subTest(used_at=at):
                self.assert_rejected(material_id, used_at=at)

    def test_pure_use_clock_requires_approval_and_validates_instants(self) -> None:
        material_id = self.build()
        material = self.service.get(material_id, require_current=False)
        claims, evidence = ProfileService(self.repository).validated_profile(apply_retirements=False)
        workflow = self.repository.get_workflow_run(material["workflow_run_id"])
        job = JobService(self.repository).get(self.job_id)
        records = {item.id: self.repository.get_evidence(item.id) for item in evidence}
        links = {(row["evidence_id"], row["claim_id"]): row
                 for row in self.repository.list_claim_evidence(relationship="supports")}
        for approval_at, use_at in ((None, USED_AT), (APPROVED_AT, True), (APPROVED_AT, ""),
                                   (APPROVED_AT, "2030-01-03T00:00:00"), (APPROVED_AT, BETWEEN.isoformat())):
            with self.subTest(approval_at=approval_at, use_at=use_at), self.assertRaises(ValueError):
                validate_material_history(material, json.loads(workflow["input_json"]), job, claims, evidence,
                    evidence_records=records, support_links=links, retirements={}, approval_at=approval_at, use_at=use_at)

    def test_selected_expiry_and_retirement_fail_before_or_at_use_but_not_after(self) -> None:
        for mode in ("expiry", "retirement"):
            for offset in (-1, 0, 1):
                with self.subTest(mode=mode, seconds_from_use=offset):
                    boundary = USE_TIME + timedelta(seconds=offset)
                    claim = (self.generic_claim(f"use-expiry-{offset}", effective_to=boundary.isoformat())
                             if mode == "expiry" else self.imported_title(f"use-retirement-{offset}",
                                 subject=f"fictional-use-retirement-{offset}"))
                    material_id = self.build((claim,))
                    self.record_approval(material_id)
                    if mode == "retirement":
                        self.retire(claim, at=boundary)
                    self.assertIsNone(self.service.validate_historical_approval_eligibility(material_id))
                    if offset <= 0:
                        self.assert_rejected(material_id)
                    else:
                        self.assertIsNone(self.service.validate_historical_use(material_id, used_at=USED_AT))

    def test_nonselected_packet_member_retired_at_equivalent_offset_use_is_rejected(self) -> None:
        selected = self.imported_title("use-packet-selected")
        peer = self.imported_title("use-packet-peer")
        material_id = self.build((selected,))
        unit = self.service.get(material_id, require_current=False)["structure"]["units"][0]
        self.assertEqual(set(unit["packet_claim_ids"]), {selected, peer})
        self.record_approval(material_id)
        self.retire(peer, at=USE_TIME)
        self.assertIsNone(self.service.validate_historical_approval_eligibility(material_id))
        self.assert_rejected(material_id, used_at="2030-01-02T19:00:00-05:00")

    def test_intervening_same_value_conflicting_and_contradicted_peers_block_use(self) -> None:
        for mode in ("same", "conflicting", "contradicted"):
            with self.subTest(mode=mode):
                subject = f"fictional-use-context-{mode}"
                selected = self.generic_claim(f"selected-{mode}", claim_type="employment_title",
                    value="Engineer", subject_id=subject)
                material_id = self.build((selected,))
                self.record_approval(material_id)
                self.generic_claim(f"peer-{mode}", claim_type="employment_title", subject_id=subject,
                    value="Engineer" if mode == "same" else "Manager",
                    status="contradicted" if mode == "contradicted" else "verified",
                    created_at=BEFORE_USE.isoformat(), verified_at=BEFORE_USE.isoformat())
                self.assertIsNone(self.service.validate_historical_approval_eligibility(material_id))
                self.assert_rejected(material_id)

    def test_later_unrelated_and_retired_before_use_peers_do_not_change_saved_packet(self) -> None:
        for mode in ("later", "unrelated", "retired"):
            with self.subTest(mode=mode):
                subject = f"fictional-use-excluded-{mode}"
                selected = self.imported_title(f"use-selected-{mode}", subject=subject)
                material_id = self.build((selected,))
                self.record_approval(material_id)
                peer = self.imported_title(f"use-peer-{mode}", value="Manager",
                    subject="fictional-use-unrelated" if mode == "unrelated" else subject,
                    at=USE_TIME + timedelta(seconds=1) if mode == "later" else BEFORE_USE)
                if mode == "retired":
                    self.retire(peer, at=USE_TIME)
                self.assertIsNone(self.service.validate_historical_use(material_id, used_at=USED_AT))

    def test_answer_only_facts_receive_use_time_expiry_and_retirement_checks(self) -> None:
        for mode in ("expired", "retired"):
            with self.subTest(mode=mode):
                claim = (self.generic_claim("use-answer-expired", effective_to=USED_AT) if mode == "expired"
                         else self.imported_title("use-answer-retired"))
                material_id = self.build(questions=self.answer_question(claim))
                self.record_approval(material_id)
                self.assertNotIn(claim, [unit["claim_id"] for unit in self.service.get(material_id, require_current=False)["structure"]["units"]])
                if mode == "retired":
                    self.retire(claim, at=USE_TIME)
                self.assertIsNone(self.service.validate_historical_approval_eligibility(material_id))
                self.assert_rejected(material_id)

    def test_use_time_does_not_replace_creation_authority_or_approval_context(self) -> None:
        for mode in ("claim", "evidence", "link"):
            with self.subTest(authority=mode):
                suffix = f"use-late-{mode}"
                claim = self.generic_claim(suffix)
                material_id = self.build((claim,))
                if mode == "claim":
                    self.repository._connection.execute("UPDATE claims SET verified_at = ? WHERE id = ?", (BETWEEN.isoformat(), claim))
                elif mode == "evidence":
                    self.repository._connection.execute("UPDATE evidence SET confirmed_at = ? WHERE id = ?",
                        (BETWEEN.isoformat(), f"fictional-history-evidence-{suffix}"))
                else:
                    self.repository._connection.execute("UPDATE claim_evidence SET created_at = ? WHERE claim_id = ?", (BETWEEN.isoformat(), claim))
                self.rewrite_material(material_id, edit_structure=self.refresh_packet_hashes)
                self.record_approval(material_id)
                self.assert_rejected(material_id)
        selected = self.imported_title("approval-context-selected")
        material_id = self.build((selected,))
        self.record_approval(material_id)
        peer = self.imported_title("approval-context-conflict", value="Manager", at=BETWEEN)
        self.retire(peer, at=BEFORE_USE)
        # The conflicting peer is gone at use but still invalidated approval.
        self.assert_rejected(material_id)

    def test_required_unanswered_history_is_not_promoted_at_use(self) -> None:
        for required in (False, True):
            with self.subTest(required=required):
                material_id = self.build(questions=[self.unanswered(required=required)])
                self.record_approval(material_id)
                before = self.repository._connection.serialize()
                self.assertIsNone(self.service.validate_historical_approval_facts(material_id))
                with patch.object(QuestionnaireService, "prepare", side_effect=AssertionError("Keep saved answers")):
                    if required:
                        self.assert_rejected(material_id)
                    else:
                        self.assertIsNone(self.service.validate_historical_use(material_id, used_at=USED_AT))
                self.assertEqual(self.repository._connection.serialize(), before)

    def test_retired_history_uses_one_bundle_pdf_profile_and_read_snapshot(self) -> None:
        material_id = self.build(questions=self.answer_question(self.claim_ids[0]))
        self.approve(material_id)
        self.retire(self.claim_ids[0], at=USE_TIME + timedelta(seconds=1))
        with self.assertRaises(MaterialBlocked):
            self.service.get(material_id)
        snapshot = self.repository._connection.serialize()
        with SQLiteRepository.from_snapshot(snapshot) as repository:
            renderer = SyntheticRenderer()
            service = MaterialService(repository, renderer)
            original = ProfileService.validated_profile
            reads = []

            def profile_read(profile: ProfileService, *, apply_retirements: bool = True):
                reads.append((apply_retirements, repository._connection.in_transaction))
                return original(profile, apply_retirements=apply_retirements)

            statements = []
            repository._connection.set_trace_callback(statements.append)
            try:
                with (
                    patch.object(service, "get", wraps=service.get) as bundle,
                    patch.object(repository, "get_material_approval", wraps=repository.get_material_approval) as approval,
                    patch.object(renderer, "validate", wraps=renderer.validate) as pdf,
                    patch.object(ProfileService, "validated_profile", autospec=True, side_effect=profile_read),
                    patch.object(service, "validate_historical_approval_eligibility", side_effect=AssertionError("No public audit chaining")),
                    patch.object(service, "plan", side_effect=AssertionError("No current plan")),
                    patch.object(service, "approve", side_effect=AssertionError("No approval creation")),
                    patch.object(service, "is_approved", side_effect=AssertionError("No readiness")),
                    patch.object(QuestionnaireService, "prepare", side_effect=AssertionError("No current answers")),
                ):
                    self.assertIsNone(service.validate_historical_use(material_id, used_at=USED_AT))
                bundle.assert_called_once_with(material_id, require_current=False)
                approval.assert_called_once_with(material_id)
                self.assertEqual(pdf.call_count, 1)
            finally:
                repository._connection.set_trace_callback(None)
            self.assertEqual(reads, [(False, True)])
            self.assertEqual(sum(sql == "BEGIN" for sql in statements), 1)
            self.assertEqual(sum(sql == "ROLLBACK" for sql in statements), 1)
            self.assertFalse(repository._connection.in_transaction)
        self.assertEqual(self.repository._connection.serialize(), snapshot)

    def test_bad_approval_stops_before_profile_and_failures_hide_storage_details(self) -> None:
        self.assertTrue(issubclass(MaterialHistoryIntegrityError, RepositoryError))
        self.assertFalse(issubclass(MaterialHistoryIntegrityError, MaterialBlocked))
        self.assert_rejected("fictional-missing-material")
        material_id = self.build()
        self.record_approval(material_id)
        for method in ("get_material_version", "get_material_approval", "get_evidence"):
            with self.subTest(method=method), patch.object(self.repository, method,
                side_effect=sqlite3.OperationalError("fictional-private-storage-detail")):
                self.assert_rejected(material_id)
        self.mutate_approval(material_id, lambda approval, workflow, payload: payload.__setitem__("version", True), rehash=False)
        with patch.object(ProfileService, "validated_profile", side_effect=AssertionError("Bad approval first")):
            self.assert_rejected(material_id)

    def test_borrowed_transaction_and_owned_interrupt_cleanup_preserve_caller_state(self) -> None:
        material_id = self.build()
        self.record_approval(material_id)
        before = self.repository._connection.serialize()
        abort = RuntimeError("fictional caller rollback")
        with self.assertRaises(RuntimeError) as raised:
            with self.repository.transaction():
                self.repository.add_workflow_run(run_id="fictional-caller-work", workflow_type="fictional-caller-work", status="queued")
                self.assertIsNone(self.service.validate_historical_use(material_id, used_at=USED_AT))
                with self.assertRaisesRegex(MaterialHistoryIntegrityError, f"^{ERROR}$"):
                    self.service.validate_historical_use(material_id, used_at=BETWEEN.isoformat())
                self.assertTrue(self.repository._connection.in_transaction)
                self.assertIsNotNone(self.repository.get_workflow_run("fictional-caller-work"))
                raise abort
        self.assertIs(raised.exception, abort)
        self.assertEqual(self.repository._connection.serialize(), before)
        interrupt = KeyboardInterrupt("fictional cancellation")
        with patch.object(ProfileService, "validated_profile", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt) as raised:
                self.service.validate_historical_use(material_id, used_at=USED_AT)
        self.assertIs(raised.exception, interrupt)
        self.assertFalse(self.repository._connection.in_transaction)
        self.assertEqual(self.repository._connection.serialize(), before)


if __name__ == "__main__":
    unittest.main()
