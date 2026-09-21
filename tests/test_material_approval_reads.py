from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import patch

from grounded_apply.repositories import RepositoryError, SQLiteRepository
from grounded_apply.services.materials import MaterialApprovalIntegrityError, MaterialBlocked, MaterialService
from grounded_apply.services.workflow import canonical
from tests import test_material_approval_history
from tests.test_materials import SyntheticRenderer


ERROR = "Material approval failed integrity checks"


class MaterialApprovalReadTests(unittest.TestCase):
    # Reuse only fixtures; do not inherit or rediscover historical test cases.
    setUp = test_material_approval_history.MaterialApprovalHistoryTests.setUp
    build = test_material_approval_history.MaterialApprovalHistoryTests.build
    question = test_material_approval_history.MaterialApprovalHistoryTests.question
    approve = test_material_approval_history.MaterialApprovalHistoryTests.approve
    mutate = test_material_approval_history.MaterialApprovalHistoryTests.mutate
    retire = test_material_approval_history.MaterialApprovalHistoryTests.retire
    insert_record_only_approval = test_material_approval_history.MaterialApprovalHistoryTests.insert_record_only_approval

    def assert_corrupt(self, material_id: str) -> None:
        before = self.repository._connection.serialize()
        for require_current in (True, False):
            with self.subTest(require_current=require_current), self.assertRaisesRegex(
                MaterialApprovalIntegrityError, f"^{ERROR}$",
            ):
                self.service.is_approved(material_id, require_current=require_current)
            self.assertFalse(self.repository._connection.in_transaction)
            self.assertEqual(self.repository._connection.serialize(), before)

    def test_absent_approval_is_false_and_does_not_skip_material_validation(self) -> None:
        material_id = self.build(questions=self.question(required=True))
        before = self.repository._connection.serialize()
        for require_current in (True, False):
            self.assertFalse(self.service.is_approved(material_id, require_current=require_current))
        self.assertEqual(self.repository._connection.serialize(), before)
        with patch.object(self.service, "get", side_effect=ValueError("fictional-corrupt-material")):
            self.assert_corrupt(material_id)
        self.assert_corrupt("fictional-missing-material")

    def test_valid_v1_v2_and_optional_partial_approvals_remain_true_in_both_modes(self) -> None:
        for transformation, questions in (("approved_text_selection@1", ()), ("approved_text_selection@2", ()),
                                          ("approved_text_selection@2", self.question(required=False))):
            with self.subTest(transformation=transformation, partial=bool(questions)):
                material_id = self.build(transformation=transformation, questions=questions)
                self.approve(material_id)
                before = self.repository._connection.serialize()
                for require_current in (True, False):
                    self.assertTrue(self.service.is_approved(material_id, require_current=require_current))
                self.assertEqual(self.repository._connection.serialize(), before)

    def test_required_partial_record_is_false_but_corrupt_present_record_is_fatal(self) -> None:
        material_id = self.build(questions=self.question(required=True))
        self.insert_record_only_approval(material_id)
        before = self.repository._connection.serialize()
        for require_current in (True, False):
            self.assertFalse(self.service.is_approved(material_id, require_current=require_current))
        self.assertEqual(self.repository._connection.serialize(), before)
        self.mutate(material_id, lambda approval, workflow, payload: workflow.__setitem__("failure_code", "fictional-failure"))
        self.assert_corrupt(material_id)

    def test_boolean_float_and_duplicate_json_versions_fail_both_read_modes(self) -> None:
        for version in (True, 1.0):
            with self.subTest(version_type=type(version).__name__):
                material_id = self.build()
                self.approve(material_id)
                self.mutate(material_id, lambda approval, workflow, payload: payload.__setitem__("version", version), rehash=False)
                self.assert_corrupt(material_id)
        material_id = self.build()
        self.approve(material_id)
        self.mutate(material_id, lambda approval, workflow, payload: None,
                    raw_input=lambda payload: canonical(payload)[:-1] + ',"version":1}')
        self.assert_corrupt(material_id)

    def test_malformed_workflow_metadata_and_times_fail_both_read_modes(self) -> None:
        for field, value in (("outstanding_need_info_json", canonical([{"kind": "need_info"}])),
                             ("retry_policy_json", "[]"), ("model_name", "fictional-model"),
                             ("updated_at", "2030-01-02T01:00:00+02:00")):
            with self.subTest(field=field):
                material_id = self.build()
                self.approve(material_id)
                self.mutate(material_id, lambda approval, workflow, payload: workflow.__setitem__(field, value))
                self.assert_corrupt(material_id)

    def test_material_approval_and_workflow_row_identities_are_bound(self) -> None:
        material_id = self.build()
        approval = self.approve(material_id)
        material = self.service.get(material_id, require_current=False)
        with patch.object(self.service, "get", return_value={**material, "id": "fictional-other-material"}), patch.object(
            self.repository, "get_material_approval", side_effect=AssertionError("Wrong material must stop before approval read"),
        ):
            self.assert_corrupt(material_id)
        for changed in ({**approval, "material_id": "fictional-other-material"},
                        {**approval, "actor_id": True}, {**approval, "fictional_extra": None}):
            with self.subTest(approval_keys=sorted(changed)), patch.object(self.repository, "get_material_approval", return_value=changed):
                self.assert_corrupt(material_id)
        original = self.repository.get_workflow_run
        workflow = original(approval["workflow_run_id"])
        for changed in (None, {**workflow, "id": "fictional-other-workflow"}):
            with self.subTest(missing_workflow=changed is None):
                def read_workflow(identifier: str):
                    return changed if identifier == approval["workflow_run_id"] else original(identifier)
                with patch.object(self.repository, "get_workflow_run", side_effect=read_workflow):
                    self.assert_corrupt(material_id)

    def test_each_read_uses_one_material_pdf_validation_and_one_pinned_transaction(self) -> None:
        material_id = self.build()
        self.approve(material_id)
        original = self.repository.get_material_approval
        before = self.repository._connection.serialize()

        def read_approval(identifier: str):
            self.assertTrue(self.repository._connection.in_transaction)
            return original(identifier)

        for require_current in (True, False):
            statements = []
            self.repository._connection.set_trace_callback(statements.append)
            try:
                with patch.object(self.service, "get", wraps=self.service.get) as material_read, patch.object(
                    self.renderer, "validate", wraps=self.renderer.validate,
                ) as pdf_check, patch.object(self.repository, "get_material_approval", side_effect=read_approval), patch.object(
                    self.service, "validate_historical_approval_record", side_effect=AssertionError("Do not audit and reread"),
                ):
                    self.assertTrue(self.service.is_approved(material_id, require_current=require_current))
                material_read.assert_called_once_with(material_id, require_current=require_current)
                self.assertEqual(pdf_check.call_count, 1)
            finally:
                self.repository._connection.set_trace_callback(None)
            self.assertEqual(sum(sql == "BEGIN" for sql in statements), 1)
            self.assertEqual(sum(sql == "ROLLBACK" for sql in statements), 1)
            self.assertFalse(self.repository._connection.in_transaction)
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_current_fact_blocker_precedes_approval_inspection_but_historical_mode_checks_corruption(self) -> None:
        material_id = self.build()
        self.approve(material_id)
        self.retire(self.claim_ids[0])
        self.mutate(material_id, lambda approval, workflow, payload: workflow.__setitem__("failure_code", "fictional-failure"))
        before = self.repository._connection.serialize()
        with patch.object(self.repository, "get_material_approval", side_effect=AssertionError("Current facts must stop first")):
            with self.assertRaises(MaterialBlocked):
                self.service.is_approved(material_id)
        with self.assertRaisesRegex(MaterialApprovalIntegrityError, f"^{ERROR}$"):
            self.service.is_approved(material_id, require_current=False)
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_read_transaction_is_borrowed_without_ending_the_callers_snapshot(self) -> None:
        material_id = self.build()
        self.approve(material_id)
        before = self.repository._connection.serialize()
        with self.repository.read_transaction():
            for require_current in (True, False):
                self.assertTrue(self.service.is_approved(material_id, require_current=require_current))
                self.assertTrue(self.repository._connection.in_transaction)
        self.assertFalse(self.repository._connection.in_transaction)
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_borrowed_write_work_survives_success_corruption_and_interrupt_until_caller_rollback(self) -> None:
        material_id = self.build()
        approval = self.approve(material_id)
        before = self.repository._connection.serialize()
        abort = RuntimeError("fictional caller rollback")
        with self.assertRaises(RuntimeError) as aborted:
            with self.repository.transaction():
                self.repository.add_workflow_run(run_id="fictional-caller-work", workflow_type="fictional-caller-work", status="queued")
                self.assertTrue(self.service.is_approved(material_id))
                with patch.object(self.repository, "get_material_approval", return_value={**approval, "actor_id": True}):
                    with self.assertRaises(MaterialApprovalIntegrityError):
                        self.service.is_approved(material_id)
                interrupt = KeyboardInterrupt("fictional interruption")
                with patch.object(self.repository, "get_material_approval", side_effect=interrupt):
                    with self.assertRaises(KeyboardInterrupt) as interrupted:
                        self.service.is_approved(material_id)
                self.assertIs(interrupted.exception, interrupt)
                self.assertTrue(self.repository._connection.in_transaction)
                self.assertIsNotNone(self.repository.get_workflow_run("fictional-caller-work"))
                raise abort
        self.assertIs(aborted.exception, abort)
        self.assertIsNone(self.repository.get_workflow_run("fictional-caller-work"))
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_approval_commit_and_exact_replay_retain_owned_write_transaction(self) -> None:
        material_id = self.build()
        approval = self.approve(material_id)
        before = self.repository._connection.serialize()
        result = self.service.approve(material_id, bundle_sha256=approval["bundle_sha256"], actor_id="fictional-reviewer",
            idempotency_key=f"fictional-approval-{self.build_index}", confirm=True)
        self.assertTrue(result["ready"])
        self.assertTrue(result["replayed"])
        self.assertEqual(self.repository.get_material_approval(material_id), approval)
        self.assertFalse(self.repository._connection.in_transaction)
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_read_only_snapshot_supports_current_and_historical_approval_reads(self) -> None:
        approved = self.build()
        self.approve(approved)
        absent = self.build()
        snapshot = self.repository._connection.serialize()
        with SQLiteRepository.from_snapshot(snapshot) as repository:
            service = MaterialService(repository, SyntheticRenderer())
            for require_current in (True, False):
                self.assertTrue(service.is_approved(approved, require_current=require_current))
                self.assertFalse(service.is_approved(absent, require_current=require_current))
                self.assertFalse(repository._connection.in_transaction)
        self.assertEqual(self.repository._connection.serialize(), snapshot)

    def test_parser_repository_and_sqlite_errors_are_fixed_fatal_while_material_blocker_is_preserved(self) -> None:
        self.assertTrue(issubclass(MaterialApprovalIntegrityError, RepositoryError))
        self.assertFalse(issubclass(MaterialApprovalIntegrityError, ValueError))
        material_id = self.build()
        self.approve(material_id)
        for error in (sqlite3.OperationalError("fictional-private-sql"), RepositoryError("fictional-private-record"),
                      ValueError("fictional-private-json"), RecursionError("fictional-private-depth")):
            with self.subTest(error_type=type(error).__name__), patch.object(self.repository, "get_material_approval", side_effect=error):
                self.assert_corrupt(material_id)
        blocker = MaterialBlocked([{"kind": "need_info", "reason": "fictional-blocker"}])
        with patch.object(self.service, "get", side_effect=blocker):
            with self.assertRaises(MaterialBlocked) as raised:
                self.service.is_approved(material_id)
        self.assertIs(raised.exception, blocker)
        self.assertFalse(self.repository._connection.in_transaction)

    def test_interrupt_identity_is_preserved_and_owned_read_transaction_released(self) -> None:
        material_id = self.build()
        self.approve(material_id)
        before = self.repository._connection.serialize()
        for require_current in (True, False):
            interrupt = KeyboardInterrupt("fictional cancellation")
            with patch.object(self.repository, "get_material_approval", side_effect=interrupt):
                with self.assertRaises(KeyboardInterrupt) as raised:
                    self.service.is_approved(material_id, require_current=require_current)
            self.assertIs(raised.exception, interrupt)
            self.assertFalse(self.repository._connection.in_transaction)
            self.assertEqual(self.repository._connection.serialize(), before)


if __name__ == "__main__":
    unittest.main()
