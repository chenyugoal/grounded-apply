from __future__ import annotations

import json
import sqlite3
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from grounded_apply.domain import ApprovalStatus
from grounded_apply.repositories import RepositoryError, SQLiteRepository
from grounded_apply.services.jobs import JobService
from grounded_apply.services.material_history import validate_material_history
from grounded_apply.services.materials import MaterialBlocked, MaterialHistoryIntegrityError, MaterialService
from grounded_apply.services.matching import job_policy
from grounded_apply.services.profile import CreateProfileReviewDecision, ProfileService
from grounded_apply.services.questionnaires import QuestionnaireService
from grounded_apply.services.workflow import finish_workflow, request_input, start_workflow
from tests import test_material_approval_history, test_material_history
from tests.test_material_approval_history import APPROVED_AT
from tests.test_material_history import CLAIM_AT, MATERIAL_AT
from tests.test_materials import SyntheticRenderer
from tests.test_profile_service import reviewable_import_request


APPROVAL_TIME = datetime.fromisoformat(APPROVED_AT)
BETWEEN = MATERIAL_AT + timedelta(hours=12)
ERROR = "Material historical approval facts failed integrity checks"


class MaterialApprovalFactsTests(unittest.TestCase):
    setUp = test_material_approval_history.MaterialApprovalHistoryTests.setUp
    _build = test_material_history.MaterialHistoryTests.build
    generic_claim = test_material_history.MaterialHistoryTests.generic_claim
    rewrite_material = test_material_history.MaterialHistoryTests.rewrite_material
    refresh_packet_hashes = test_material_history.MaterialHistoryTests.refresh_packet_hashes
    retire = test_material_history.MaterialHistoryTests.retire
    approve = test_material_approval_history.MaterialApprovalHistoryTests.approve
    mutate_approval = test_material_approval_history.MaterialApprovalHistoryTests.mutate

    def build(self, claim_ids: tuple[str, ...] | None = None, **options: object) -> str:
        with patch("grounded_apply.services.questionnaires.job_policy", return_value=job_policy(self.job_id, now=MATERIAL_AT)):
            return self._build(claim_ids, **options)

    def record_approval(self, material_id: str, *, at: str = APPROVED_AT) -> None:
        """Only the isolated fixture may create custody without readiness policy."""
        material = self.service.get(material_id, require_current=False)
        payload = request_input(f"fictional-approval-facts-{self.build_index}", {"material_id": material_id,
            "bundle_sha256": material["bundle_sha256"], "actor_id": "fictional-reviewer"})
        with self.repository.transaction():
            workflow = start_workflow(self.repository, "material_approval", payload, at)
            self.repository.insert_material_approval(material_id=material_id, bundle_sha256=material["bundle_sha256"],
                actor_id="fictional-reviewer", approved_at=at, workflow_run_id=workflow["id"])
            finish_workflow(self.repository, workflow["id"], [material_id], at)
        self.assertIsNone(self.service.validate_historical_approval_record(material_id))

    def imported_title(self, suffix: str, *, value: str = "Engineer", subject: str = "fictional-shared-role",
                       at: datetime = datetime.fromisoformat(CLAIM_AT)) -> str:
        service = ProfileService(self.repository)
        result = service.create_import_proposal(reviewable_import_request(
            idempotency_key=f"fictional-title-{suffix}", claim_type="employment_title",
            value={"employer": "Fictional Laboratory", "title": value},
            canonical_text=f"Fictional {value}", subject_type="person", subject_id=subject,
        ), now=at)
        claim_id = result.claims[0].id
        item = next(item for item in service.list_review_items() if item.claim.id == claim_id)
        service.decide_review_item(CreateProfileReviewDecision(claim_id=claim_id, review_token=item.review_token,
            decision=ApprovalStatus.APPROVED, actor_id="fictional-reviewer", idempotency_key=f"fictional-title-approve-{suffix}"), now=at)
        return claim_id

    def answer_question(self, claim_id: str) -> list[dict]:
        return [{"id": "fictional-answer-only", "text": "Describe your project experience.",
                 "claim_ids": [claim_id], "required": True}]

    def assert_rejected(self, material_id: str) -> None:
        before = self.repository._connection.serialize()
        with self.assertRaisesRegex(MaterialHistoryIntegrityError, f"^{ERROR}$"):
            self.service.validate_historical_approval_facts(material_id)
        self.assertEqual(self.repository._connection.serialize(), before)
        self.assertFalse(self.repository._connection.in_transaction)

    def test_absent_approval_validates_bundle_but_does_not_invent_an_approval_time(self) -> None:
        material_id = self.build()
        before = self.repository._connection.serialize()
        with patch.object(ProfileService, "validated_profile", side_effect=AssertionError("No approval-time facts without approval")):
            self.assertIsNone(self.service.validate_historical_approval_facts(material_id))
        self.assertIsNone(self.repository.get_material_approval(material_id))
        self.assertEqual(self.repository._connection.serialize(), before)
        with patch.object(self.service, "get", side_effect=ValueError("fictional-invalid-bundle")):
            self.assert_rejected(material_id)

    def test_real_legacy_and_modern_approvals_keep_original_material_and_packet_digests(self) -> None:
        for transformation in ("approved_text_selection@1", "approved_text_selection@2"):
            with self.subTest(transformation=transformation):
                material_id = self.build(transformation=transformation)
                self.approve(material_id)
                before = self.repository._connection.serialize()
                material = self.repository.get_material_version(material_id)
                self.assertIsNone(self.service.validate_historical_approval_facts(material_id))
                self.assertEqual(self.repository.get_material_version(material_id), material)
                self.assertEqual(self.repository._connection.serialize(), before)

    def test_claim_expiry_is_half_open_at_approval_while_later_expiry_remains_valid(self) -> None:
        for index, expiry in enumerate((BETWEEN, APPROVAL_TIME, APPROVAL_TIME + timedelta(seconds=1))):
            with self.subTest(expiry=expiry):
                claim = self.generic_claim(f"expiry-{index}", effective_from=MATERIAL_AT.isoformat(), effective_to=expiry.isoformat())
                material_id = self.build((claim,))
                self.record_approval(material_id)
                self.assertIsNone(self.service.validate_historical_facts(material_id))
                if expiry <= APPROVAL_TIME:
                    self.assert_rejected(material_id)
                else:
                    before = self.repository._connection.serialize()
                    self.assertIsNone(self.service.validate_historical_approval_facts(material_id))
                    self.assertEqual(self.repository._connection.serialize(), before)

    def test_a_claim_becoming_effective_after_rendering_does_not_gain_creation_authority(self) -> None:
        claim = self.generic_claim("newly-effective")
        material_id = self.build((claim,))
        self.repository._connection.execute("UPDATE claims SET effective_from = ? WHERE id = ?", (BETWEEN.isoformat(), claim))
        self.rewrite_material(material_id, edit_structure=self.refresh_packet_hashes)
        self.record_approval(material_id)
        self.assert_rejected(material_id)

    def test_recorded_claim_evidence_and_link_authority_cannot_move_between_render_and_approval(self) -> None:
        later = BETWEEN.isoformat()
        changes = (
            ("claims", "created_at = ?, updated_at = ?", (later, later)),
            ("claims", "verified_at = ?", (later,)),
            ("claims", "updated_at = ?", (later,)),
            ("evidence", "captured_at = ?", (later,)),
            ("evidence", "confirmed_at = ?", (later,)),
            ("evidence", "created_at = ?, updated_at = ?", (later, later)),
            ("evidence", "updated_at = ?", (later,)),
            ("claim_evidence", "created_at = ?", (later,)),
        )
        for index, (table, assignment, values) in enumerate(changes):
            with self.subTest(table=table, assignment=assignment):
                suffix = f"late-authority-{index}"
                claim = self.generic_claim(suffix)
                material_id = self.build((claim,))
                self.assertIsNone(self.service.validate_historical_facts(material_id))
                identifier = f"fictional-history-evidence-{suffix}" if table == "evidence" else claim
                key = "claim_id" if table == "claim_evidence" else "id"
                self.repository._connection.execute(f"UPDATE {table} SET {assignment} WHERE {key} = ?", (*values, identifier))
                self.rewrite_material(material_id, edit_structure=self.refresh_packet_hashes)
                self.record_approval(material_id)
                self.assert_rejected(material_id)

    def test_selected_retirement_before_or_at_approval_blocks_but_later_retirement_does_not(self) -> None:
        for index, at in enumerate((BETWEEN, APPROVAL_TIME, APPROVAL_TIME + timedelta(seconds=1))):
            with self.subTest(retired_at=at):
                claim = self.claim_ids[index]
                material_id = self.build((claim,))
                self.record_approval(material_id)
                self.retire(claim, at=at)
                self.assertIsNone(self.service.validate_historical_facts(material_id))
                if at <= APPROVAL_TIME:
                    self.assert_rejected(material_id)
                else:
                    self.assertIsNone(self.service.validate_historical_approval_facts(material_id))

    def test_equivalent_non_selected_packet_member_retirement_at_approval_also_blocks(self) -> None:
        first = self.imported_title("packet-first")
        peer = self.imported_title("packet-peer")
        material_id = self.build((first,))
        unit = self.service.get(material_id, require_current=False)["structure"]["units"][0]
        self.assertEqual(set(unit["packet_claim_ids"]), {first, peer})
        self.record_approval(material_id)
        self.assertIsNone(self.service.validate_historical_approval_facts(material_id))
        self.retire(peer, at=APPROVAL_TIME)
        self.assertIsNone(self.service.validate_historical_facts(material_id))
        self.assert_rejected(material_id)

    def test_timezone_equivalent_approval_and_retirement_use_instants(self) -> None:
        material_id = self.build((self.claim_ids[0],))
        self.record_approval(material_id, at="2030-01-01T19:00:00-05:00")
        self.retire(self.claim_ids[0], at=APPROVAL_TIME)
        self.assert_rejected(material_id)

    def test_intervening_same_value_or_conflicting_singular_peer_blocks_original_packet(self) -> None:
        for value in ("Engineer", "Manager"):
            with self.subTest(value=value):
                subject = f"fictional-intervening-{value}"
                selected = self.imported_title(f"selected-{value}", subject=subject)
                material_id = self.build((selected,))
                self.record_approval(material_id)
                self.assertIsNone(self.service.validate_historical_approval_facts(material_id))
                self.imported_title(f"intervening-{value}", value=value, subject=subject, at=BETWEEN)
                self.assertIsNone(self.service.validate_historical_facts(material_id))
                self.assert_rejected(material_id)

    def test_later_unrelated_or_retired_contextual_peers_do_not_reinterpret_approval(self) -> None:
        for mode in ("later", "unrelated", "retired"):
            with self.subTest(mode=mode):
                subject = f"fictional-excluded-{mode}"
                selected = self.imported_title(f"kept-{mode}", subject=subject)
                material_id = self.build((selected,))
                self.record_approval(material_id)
                peer = self.imported_title(f"excluded-{mode}", value="Manager",
                    subject="fictional-unrelated-role" if mode == "unrelated" else subject,
                    at=APPROVAL_TIME + timedelta(seconds=1) if mode == "later" else BETWEEN)
                if mode == "retired":
                    self.retire(peer, at=BETWEEN + timedelta(hours=1))
                before = self.repository._connection.serialize()
                self.assertIsNone(self.service.validate_historical_approval_facts(material_id))
                self.assertEqual(self.repository._connection.serialize(), before)

    def test_non_singular_later_career_fact_does_not_expand_selected_output(self) -> None:
        selected = self.generic_claim("independent-selected", subject_id="fictional-career-subject")
        material_id = self.build((selected,))
        self.record_approval(material_id)
        self.generic_claim("independent-added", value="Rust", text="Used Rust in a fictional project",
            subject_id="fictional-career-subject", created_at=BETWEEN.isoformat(), verified_at=BETWEEN.isoformat())
        self.assertIsNone(self.service.validate_historical_approval_facts(material_id))

    def test_answer_only_facts_obey_expiry_retirement_and_original_authority_cutoffs(self) -> None:
        for mode in ("expired", "retired", "late-approval"):
            with self.subTest(mode=mode):
                if mode == "retired":
                    claim = self.claim_ids[0]
                else:
                    fields = {"effective_to": APPROVED_AT} if mode == "expired" else {}
                    claim = self.generic_claim(f"answer-only-{mode}", **fields)
                material_id = self.build(self.claim_ids[1:], questions=self.answer_question(claim))
                self.record_approval(material_id)
                self.assertNotIn(claim, [unit["claim_id"] for unit in self.service.get(material_id, require_current=False)["structure"]["units"]])
                if mode == "retired":
                    self.retire(claim, at=APPROVAL_TIME)
                elif mode == "late-approval":
                    self.repository._connection.execute("UPDATE claims SET verified_at = ? WHERE id = ?", (BETWEEN.isoformat(), claim))
                self.assert_rejected(material_id)

    def test_required_need_info_can_have_valid_emitted_facts_without_granting_approval_authority(self) -> None:
        questions = [{"id": "fictional-human-answer", "text": "Please sign this statement about your experience.",
                      "claim_ids": [self.claim_ids[0]], "required": True}]
        material_id = self.build(questions=questions)
        self.record_approval(material_id)
        before = self.repository._connection.serialize()
        self.assertIsNone(self.service.validate_historical_approval_facts(material_id))
        self.assertFalse(self.service.is_approved(material_id, require_current=False))
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_snapshot_audit_uses_one_bundle_profile_and_transaction_without_current_resolution(self) -> None:
        material_id = self.build()
        self.approve(material_id)
        self.retire(self.claim_ids[0], at=APPROVAL_TIME + timedelta(seconds=1))
        snapshot = self.repository._connection.serialize()
        with SQLiteRepository.from_snapshot(snapshot) as repository:
            renderer = SyntheticRenderer()
            service = MaterialService(repository, renderer)
            original = ProfileService.validated_profile
            reads = []

            def validated(profile: ProfileService, *, apply_retirements: bool = True):
                reads.append((apply_retirements, repository._connection.in_transaction))
                return original(profile, apply_retirements=apply_retirements)

            statements = []
            repository._connection.set_trace_callback(statements.append)
            try:
                with patch.object(service, "get", wraps=service.get) as read, patch.object(
                    renderer, "validate", wraps=renderer.validate,
                ) as pdf, patch.object(ProfileService, "validated_profile", autospec=True, side_effect=validated), patch.object(
                    service, "plan", side_effect=AssertionError("No current plan"),
                ), patch.object(service, "approve", side_effect=AssertionError("No approval")), patch.object(
                    service, "is_approved", side_effect=AssertionError("No readiness"),
                ), patch.object(QuestionnaireService, "prepare", side_effect=AssertionError("No current answers")):
                    self.assertIsNone(service.validate_historical_approval_facts(material_id))
                read.assert_called_once_with(material_id, require_current=False)
                self.assertEqual(pdf.call_count, 1)
            finally:
                repository._connection.set_trace_callback(None)
            self.assertEqual(reads, [(False, True)])
            self.assertEqual(sum(sql == "BEGIN" for sql in statements), 1)
            self.assertEqual(sum(sql == "ROLLBACK" for sql in statements), 1)
            self.assertFalse(repository._connection.in_transaction)
        self.assertEqual(self.repository._connection.serialize(), snapshot)

    def test_corrupt_approval_record_is_rejected_before_profile_facts_are_read(self) -> None:
        material_id = self.build()
        self.record_approval(material_id)
        self.mutate_approval(material_id, lambda approval, workflow, payload: payload.__setitem__("version", True), rehash=False)
        with patch.object(ProfileService, "validated_profile", side_effect=AssertionError("Bad approval must stop before fact audit")):
            self.assert_rejected(material_id)

    def test_pure_history_helper_rejects_empty_mistyped_naive_and_earlier_approval_clocks(self) -> None:
        material_id = self.build()
        material = self.service.get(material_id, require_current=False)
        claims, evidence = ProfileService(self.repository).validated_profile(apply_retirements=False)
        workflow = self.repository.get_workflow_run(material["workflow_run_id"])
        job = JobService(self.repository).get(self.job_id)
        records = {item.id: self.repository.get_evidence(item.id) for item in evidence}
        links = {(row["evidence_id"], row["claim_id"]): row
                 for row in self.repository.list_claim_evidence(relationship="supports")}
        before = self.repository._connection.serialize()
        for at in ("", True, "2030-01-02T00:00:00", (MATERIAL_AT - timedelta(seconds=1)).isoformat()):
            with self.subTest(approval_at=at), self.assertRaises(ValueError):
                validate_material_history(material, json.loads(workflow["input_json"]), job, claims, evidence,
                    evidence_records=records, support_links=links, retirements={}, approval_at=at)
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_success_and_failure_borrow_the_callers_transaction_without_rolling_back_work(self) -> None:
        material_id = self.build()
        self.record_approval(material_id)
        before = self.repository._connection.serialize()
        abort = RuntimeError("fictional caller rollback")
        with self.assertRaises(RuntimeError) as raised:
            with self.repository.transaction():
                self.repository.add_workflow_run(run_id="fictional-caller-work", workflow_type="fictional-caller-work", status="queued")
                self.assertIsNone(self.service.validate_historical_approval_facts(material_id))
                with patch.object(self.repository, "get_evidence", side_effect=sqlite3.OperationalError("fictional-storage-error")):
                    with self.assertRaisesRegex(MaterialHistoryIntegrityError, f"^{ERROR}$"):
                        self.service.validate_historical_approval_facts(material_id)
                self.assertTrue(self.repository._connection.in_transaction)
                self.assertIsNotNone(self.repository.get_workflow_run("fictional-caller-work"))
                raise abort
        self.assertIs(raised.exception, abort)
        self.assertIsNone(self.repository.get_workflow_run("fictional-caller-work"))
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_storage_errors_are_fixed_fatal_and_interrupt_identity_is_preserved(self) -> None:
        self.assertTrue(issubclass(MaterialHistoryIntegrityError, RepositoryError))
        self.assertFalse(issubclass(MaterialHistoryIntegrityError, MaterialBlocked))
        material_id = self.build()
        self.record_approval(material_id)
        before = self.repository._connection.serialize()
        for method in ("get_material_version", "get_material_approval", "get_evidence"):
            with self.subTest(method=method), patch.object(self.repository, method,
                side_effect=sqlite3.OperationalError("fictional-private-storage-detail")):
                self.assert_rejected(material_id)
        interrupt = KeyboardInterrupt("fictional cancellation")
        with patch.object(ProfileService, "validated_profile", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt) as raised:
                self.service.validate_historical_approval_facts(material_id)
        self.assertIs(raised.exception, interrupt)
        self.assertFalse(self.repository._connection.in_transaction)
        self.assertEqual(self.repository._connection.serialize(), before)


if __name__ == "__main__":
    unittest.main()
