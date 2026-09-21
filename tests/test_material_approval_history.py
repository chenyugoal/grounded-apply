from __future__ import annotations

import json
import sqlite3
import unittest
from collections.abc import Callable
from datetime import timedelta
from unittest.mock import patch

from grounded_apply.repositories import RepositoryError, SQLiteRepository
from grounded_apply.services.materials import MaterialApprovalIntegrityError, MaterialBlocked, MaterialHistoryIntegrityError, MaterialService
from grounded_apply.services.questionnaires import QuestionnaireService
from grounded_apply.services.workflow import canonical, digest, finish_workflow, request_input, start_workflow
from tests import test_material_history
from tests.test_material_history import LATER, MATERIAL_AT
from tests.test_materials import SyntheticRenderer, approved_fixture


ERROR = "Material historical approval record failed integrity checks"
APPROVED_AT = (MATERIAL_AT + timedelta(days=1)).isoformat()


class MaterialApprovalHistoryTests(unittest.TestCase):
    build = test_material_history.MaterialHistoryTests.build
    retire = test_material_history.MaterialHistoryTests.retire

    def setUp(self) -> None:
        self.repository = SQLiteRepository(":memory:").initialize()
        self.addCleanup(self.repository.close)
        self.job_id, self.claim_ids = approved_fixture(self.repository)
        self.renderer = SyntheticRenderer()
        self.service = MaterialService(self.repository, self.renderer)
        self.build_index = 0

    def question(self, *, required: bool) -> list[dict]:
        return [{"id": "fictional-unanswered", "text": "Please sign this statement about your experience.",
                 "claim_ids": list(self.claim_ids[:1]), "required": required}]

    def approve(self, material_id: str) -> dict:
        material = self.service.get(material_id)
        with patch("grounded_apply.services.materials.timestamp", return_value=APPROVED_AT):
            self.service.approve(material_id, bundle_sha256=material["bundle_sha256"],
                actor_id="fictional-reviewer", idempotency_key=f"fictional-approval-{self.build_index}", confirm=True)
        return self.repository.get_material_approval(material_id)

    def insert_record_only_approval(self, material_id: str) -> None:
        """Create isolated synthetic custody without invoking readiness approval."""
        material = self.service.get(material_id, require_current=False)
        payload = request_input("fictional-record-only", {"material_id": material_id,
            "bundle_sha256": material["bundle_sha256"], "actor_id": "fictional-reviewer"})
        with self.repository.transaction():
            workflow = start_workflow(self.repository, "material_approval", payload, APPROVED_AT)
            self.repository.insert_material_approval(material_id=material_id, bundle_sha256=material["bundle_sha256"],
                actor_id="fictional-reviewer", approved_at=APPROVED_AT, workflow_run_id=workflow["id"])
            finish_workflow(self.repository, workflow["id"], [material_id], APPROVED_AT)

    def mutate(
        self, material_id: str, edit: Callable[[dict, dict, dict], None], *, rehash: bool = True,
        raw_input: Callable[[dict], str] | None = None,
    ) -> None:
        """Rebind synthetic approval/workflow rows while retaining exact schema SQL."""
        approval = self.repository.get_material_approval(material_id)
        workflow = self.repository.get_workflow_run(approval["workflow_run_id"])
        workflow_id = workflow["id"]
        payload = json.loads(workflow["input_json"])
        edit(approval, workflow, payload)
        workflow["input_json"] = canonical(payload) if raw_input is None else raw_input(payload)
        if rehash:
            workflow["input_hash_sha256"] = digest(payload)
        trigger = self.repository._connection.execute(
            "SELECT sql FROM sqlite_schema WHERE name = 'approvals_no_update'",
        ).fetchone()[0]
        with self.repository.transaction():
            self.repository._connection.execute("DROP TRIGGER approvals_no_update")
            self.repository._connection.execute("""UPDATE material_approvals SET bundle_sha256 = ?, actor_id = ?,
                approved_at = ?, workflow_run_id = ? WHERE material_id = ?""",
                (approval["bundle_sha256"], approval["actor_id"], approval["approved_at"], approval["workflow_run_id"], material_id))
            changes = {key: value for key, value in workflow.items() if key != "id"}
            self.repository._connection.execute(
                "UPDATE workflow_runs SET " + ", ".join(f"{key} = ?" for key in changes) + " WHERE id = ?",
                (*changes.values(), workflow_id),
            )
            self.repository._connection.execute(trigger)

    def assert_rejected(self, material_id: str) -> None:
        before = self.repository._connection.serialize()
        with self.assertRaisesRegex(MaterialHistoryIntegrityError, f"^{ERROR}$"):
            self.service.validate_historical_approval_record(material_id)
        self.assertEqual(self.repository._connection.serialize(), before)
        self.assertFalse(self.repository._connection.in_transaction)

    def test_missing_approval_is_valid_unapproved_history_including_required_partial_material(self) -> None:
        for questions in ((), self.question(required=True), self.question(required=False)):
            with self.subTest(required=questions[0]["required"] if questions else None):
                material_id = self.build(questions=questions)
                before = self.repository._connection.serialize()
                self.assertIsNone(self.service.validate_historical_approval_record(material_id))
                self.assertIsNone(self.repository.get_material_approval(material_id))
                self.assertFalse(self.service.is_approved(material_id, require_current=False))
                self.assertEqual(self.repository._connection.serialize(), before)

    def test_normal_legacy_modern_and_optional_partial_approvals_validate_without_writes(self) -> None:
        variants = (("approved_text_selection@1", ()), ("approved_text_selection@2", ()),
                    ("approved_text_selection@2", self.question(required=False)))
        for transformation, questions in variants:
            with self.subTest(transformation=transformation, partial=bool(questions)):
                material_id = self.build(transformation=transformation, questions=questions)
                original = self.approve(material_id)
                before = self.repository._connection.serialize()
                self.assertIsNone(self.service.validate_historical_approval_record(material_id))
                self.assertTrue(self.service.is_approved(material_id, require_current=False))
                self.assertEqual(self.repository.get_material_approval(material_id), original)
                self.assertEqual(self.repository._connection.serialize(), before)

    def test_structurally_intact_required_partial_record_is_custody_without_readiness(self) -> None:
        material_id = self.build(questions=self.question(required=True))
        with self.assertRaises(MaterialBlocked):
            self.approve(material_id)
        self.insert_record_only_approval(material_id)
        before = self.repository._connection.serialize()
        self.assertFalse(self.service.is_approved(material_id, require_current=False))
        self.assertIsNone(self.service.validate_historical_approval_record(material_id))
        self.assertIsNotNone(self.repository.get_material_approval(material_id))
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_retired_approval_in_read_only_snapshot_does_not_replay_current_authority(self) -> None:
        material_id = self.build()
        original = self.approve(material_id)
        self.retire(self.claim_ids[0], at=LATER)
        with self.assertRaises(MaterialBlocked):
            self.service.get(material_id)
        snapshot = self.repository._connection.serialize()
        with SQLiteRepository.from_snapshot(snapshot) as repository:
            service = MaterialService(repository, SyntheticRenderer())
            with patch.object(service, "plan", side_effect=AssertionError("No current selection")), patch.object(
                service, "approve", side_effect=AssertionError("No approval"),
            ), patch.object(service, "is_approved", side_effect=AssertionError("No readiness replay")), patch.object(
                service, "validate_historical_facts", side_effect=AssertionError("Record custody is a separate audit"),
            ), patch.object(QuestionnaireService, "prepare", side_effect=AssertionError("No current answers")):
                self.assertIsNone(service.validate_historical_approval_record(material_id))
            self.assertEqual(repository.get_material_approval(material_id), original)
            self.assertFalse(repository._connection.in_transaction)
        self.assertEqual(self.repository._connection.serialize(), snapshot)

    def test_approval_and_material_reads_share_one_pinned_transaction(self) -> None:
        material_id = self.build()
        approval = self.approve(material_id)
        original_approval = self.repository.get_material_approval
        original_workflow = self.repository.get_workflow_run
        observations = []

        def read_approval(identifier: str):
            observations.append(("approval", self.repository._connection.in_transaction))
            return original_approval(identifier)

        def read_workflow(identifier: str):
            observations.append(("workflow", self.repository._connection.in_transaction))
            return original_workflow(identifier)

        statements = []
        before = self.repository._connection.serialize()
        self.repository._connection.set_trace_callback(statements.append)
        try:
            with patch.object(self.repository, "get_material_approval", side_effect=read_approval), patch.object(
                self.repository, "get_workflow_run", side_effect=read_workflow,
            ), patch.object(self.service, "get", wraps=self.service.get) as material_read:
                self.assertIsNone(self.service.validate_historical_approval_record(material_id))
            material_read.assert_called_once_with(material_id, require_current=False)
        finally:
            self.repository._connection.set_trace_callback(None)
        self.assertTrue(observations)
        self.assertTrue(all(active for _, active in observations))
        self.assertEqual(sum(sql == "BEGIN" for sql in statements), 1)
        self.assertEqual(sum(sql == "ROLLBACK" for sql in statements), 1)
        self.assertEqual(self.repository._connection.serialize(), before)
        self.assertEqual(self.repository.get_material_approval(material_id), approval)

    def test_later_workflow_update_and_timezone_equivalent_material_boundary_are_valid(self) -> None:
        material_id = self.build()
        self.approve(material_id)
        equivalent = "2029-12-31T19:00:00-05:00"

        def set_times(approval: dict, workflow: dict, payload: dict) -> None:
            approval["approved_at"] = equivalent
            for field in ("created_at", "started_at", "finished_at"):
                workflow[field] = equivalent
            workflow["updated_at"] = "2030-01-01T00:00:01+00:00"

        self.mutate(material_id, set_times)
        before = self.repository._connection.serialize()
        self.assertIsNone(self.service.validate_historical_approval_record(material_id))
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_lexically_later_but_chronologically_earlier_approval_or_update_is_rejected(self) -> None:
        for field in ("approval", "updated_at"):
            with self.subTest(field=field):
                material_id = self.build()
                self.approve(material_id)

                def forge(approval: dict, workflow: dict, payload: dict) -> None:
                    if field == "approval":
                        at = "2030-01-01T01:00:00+02:00"
                        approval["approved_at"] = at
                        for key in ("created_at", "started_at", "finished_at", "updated_at"):
                            workflow[key] = at
                    else:
                        workflow["updated_at"] = "2030-01-02T01:00:00+02:00"

                self.mutate(material_id, forge)
                self.assert_rejected(material_id)

    def test_boolean_and_float_version_aliases_cannot_use_the_integer_workflow_hash(self) -> None:
        for version in (True, 1.0):
            with self.subTest(version_type=type(version).__name__):
                material_id = self.build()
                self.approve(material_id)
                self.mutate(material_id, lambda approval, workflow, payload: payload.__setitem__("version", version), rehash=False)
                # Ordinary reads must reject decoded-dict numeric aliases too.
                with self.assertRaises(MaterialApprovalIntegrityError):
                    self.service.is_approved(material_id, require_current=False)
                self.assert_rejected(material_id)

    def test_payload_requires_exact_fields_version_and_unambiguous_json(self) -> None:
        changes = (
            lambda payload: payload.__setitem__("version", 2),
            lambda payload: payload.pop("actor_id"),
            lambda payload: payload.__setitem__("extra", None),
        )
        for index, change in enumerate(changes):
            with self.subTest(change=index):
                material_id = self.build()
                self.approve(material_id)
                self.mutate(material_id, lambda approval, workflow, payload: change(payload))
                self.assert_rejected(material_id)
        for raw in (lambda payload: "{", lambda payload: "[]",
                    lambda payload: canonical(payload)[:-1] + ',"version":1}'):
            material_id = self.build()
            self.approve(material_id)
            self.mutate(material_id, lambda approval, workflow, payload: None, raw_input=raw)
            self.assert_rejected(material_id)

    def test_rehashed_invalid_actor_or_bundle_still_fails_record_binding(self) -> None:
        for field, value in (("actor_id", "fictional actor with spaces"), ("actor_id", ""),
                             ("bundle_sha256", "0" * 64), ("bundle_sha256", "not-a-digest")):
            with self.subTest(field=field, value=value):
                material_id = self.build()
                self.approve(material_id)

                def forge(approval: dict, workflow: dict, payload: dict) -> None:
                    approval[field] = payload[field] = value

                self.mutate(material_id, forge)
                self.assert_rejected(material_id)

    def test_rehashed_payload_cannot_point_to_another_material_or_actor(self) -> None:
        other = self.build()
        for field, value in (("material_id", other), ("actor_id", "fictional-other-reviewer"),
                             ("idempotency_sha256", "0" * 64)):
            with self.subTest(field=field):
                material_id = self.build()
                self.approve(material_id)
                self.mutate(material_id, lambda approval, workflow, payload: payload.__setitem__(field, value))
                self.assert_rejected(material_id)

    def test_workflow_status_steps_artifacts_and_hash_must_match_approval(self) -> None:
        fields = (("workflow_type", "material_build"), ("status", "failed"), ("current_step", "persist"),
                  ("completed_steps_json", canonical(["persist", "validate"])),
                  ("generated_artifacts_json", canonical([])),
                  ("generated_artifacts_json", canonical(["fictional-other-material"])),
                  ("input_hash_sha256", "0" * 64))
        for field, value in fields:
            with self.subTest(field=field, value=value):
                material_id = self.build()
                self.approve(material_id)
                self.mutate(material_id, lambda approval, workflow, payload: workflow.__setitem__(field, value), rehash=False)
                self.assert_rejected(material_id)

    def test_unsupported_pending_retry_model_and_failure_metadata_is_rejected(self) -> None:
        fields = (("outstanding_need_info_json", canonical([{"kind": "need_info"}])),
                  ("retry_policy_json", canonical({"attempts": 1})),
                  ("model_name", "fictional-model"), ("prompt_version", "fictional-prompt"),
                  ("failure_code", "fictional-failure"), ("failure_reason", "fictional-private-detail"))
        for field, value in fields:
            with self.subTest(field=field):
                material_id = self.build()
                self.approve(material_id)
                self.mutate(material_id, lambda approval, workflow, payload: workflow.__setitem__(field, value))
                with self.assertRaises(MaterialApprovalIntegrityError):
                    self.service.is_approved(material_id, require_current=False)
                self.assert_rejected(material_id)

    def test_malformed_naive_and_inconsistent_approval_times_are_rejected(self) -> None:
        for at in ("fictional-invalid-time", "2030-01-02T00:00:00", "2029-01-01T00:00:00+00:00"):
            with self.subTest(at=at):
                material_id = self.build()
                self.approve(material_id)

                def forge(approval: dict, workflow: dict, payload: dict) -> None:
                    approval["approved_at"] = at
                    for field in ("created_at", "started_at", "finished_at", "updated_at"):
                        workflow[field] = at

                self.mutate(material_id, forge)
                self.assert_rejected(material_id)
        for field in ("approved_at", "started_at", "finished_at"):
            with self.subTest(inconsistent=field):
                material_id = self.build()
                self.approve(material_id)

                def change_one(approval: dict, workflow: dict, payload: dict) -> None:
                    if field == "approved_at":
                        approval[field] = MATERIAL_AT.isoformat()
                    elif field == "started_at":
                        workflow[field] = MATERIAL_AT.isoformat()
                    else:
                        workflow[field] = LATER.isoformat()

                self.mutate(material_id, change_one)
                self.assert_rejected(material_id)

    def test_missing_or_mismatched_fetched_approval_and_workflow_rows_are_rejected(self) -> None:
        material_id = self.build()
        approval = self.approve(material_id)
        original_workflow = self.repository.get_workflow_run
        workflow = original_workflow(approval["workflow_run_id"])
        replacements = (
            ("absent", None),
            ("wrong-id", {**workflow, "id": "fictional-other-workflow"}),
            ("extra-key", {**workflow, "fictional_extra": None}),
            ("missing-key", {key: value for key, value in workflow.items() if key != "retry_policy_json"}),
        )
        for label, replacement in replacements:
            with self.subTest(workflow=label):
                def read_workflow(identifier: str):
                    return replacement if identifier == approval["workflow_run_id"] else original_workflow(identifier)
                with patch.object(self.repository, "get_workflow_run", side_effect=read_workflow):
                    self.assert_rejected(material_id)
        for field, value in (("material_id", "fictional-other-material"), ("workflow_run_id", "not an opaque id"),
                             ("actor_id", True), ("approved_at", 123)):
            with self.subTest(field=field), patch.object(self.repository, "get_material_approval", return_value={**approval, field: value}):
                self.assert_rejected(material_id)
        for replacement in ({**approval, "fictional_extra": None},
                            {key: value for key, value in approval.items() if key != "approved_at"},
                            {key: value for key, value in approval.items() if key != "workflow_run_id"}):
            with self.subTest(approval_keys=sorted(replacement)), patch.object(
                self.repository, "get_material_approval", return_value=replacement,
            ):
                self.assert_rejected(material_id)

    def test_absence_does_not_skip_material_custody_and_errors_are_fixed(self) -> None:
        self.assertTrue(issubclass(MaterialHistoryIntegrityError, RepositoryError))
        self.assertFalse(issubclass(MaterialHistoryIntegrityError, MaterialBlocked))
        self.assert_rejected("fictional-missing-material")
        material_id = self.build()
        with patch.object(self.service, "get", side_effect=ValueError("fictional-private-material-detail")):
            self.assert_rejected(material_id)
        for method in ("get_material_version", "get_material_approval"):
            with self.subTest(method=method), patch.object(self.repository, method,
                side_effect=sqlite3.OperationalError("fictional-private-sql-detail")):
                self.assert_rejected(material_id)
        self.approve(material_id)
        self.mutate(material_id, lambda approval, workflow, payload: None,
                    raw_input=lambda payload: "[" * 2000 + "0" + "]" * 2000)
        self.assert_rejected(material_id)

    def test_interrupt_identity_is_preserved_and_read_transaction_is_released(self) -> None:
        material_id = self.build()
        self.approve(material_id)
        before = self.repository._connection.serialize()
        interrupt = KeyboardInterrupt("fictional cancellation")
        with patch.object(self.repository, "get_material_approval", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt) as raised:
                self.service.validate_historical_approval_record(material_id)
        self.assertIs(raised.exception, interrupt)
        self.assertFalse(self.repository._connection.in_transaction)
        self.assertEqual(self.repository._connection.serialize(), before)


if __name__ == "__main__":
    unittest.main()
