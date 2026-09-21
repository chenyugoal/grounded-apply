from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from collections import Counter
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from grounded_apply.repositories import SQLiteRepository
from grounded_apply.repositories.snapshots import validate_profile_snapshot
from grounded_apply.services.batches import BatchService
from grounded_apply.services.material_approval_history import validate_approval_record
from grounded_apply.services.material_build_history import validate_material_build_record
from grounded_apply.services.materials import MaterialBlocked, MaterialHistoryIntegrityError, MaterialService
from grounded_apply.services.profile import ProfileService
from grounded_apply.services.questionnaires import QuestionnaireService
from grounded_apply.services.workflow import finish_workflow, request_input, start_workflow
from tests import test_material_approval_eligibility
from tests.test_batches import RecordingRenderer
from tests.test_material_approval_facts import APPROVAL_TIME, APPROVED_AT
from tests.test_material_history import MATERIAL_AT


ERROR = "Material historical inventory failed integrity checks"


class MaterialInventoryTests(unittest.TestCase):
    _build = test_material_approval_eligibility.MaterialApprovalEligibilityTests._build
    build = test_material_approval_eligibility.MaterialApprovalEligibilityTests.build
    approve = test_material_approval_eligibility.MaterialApprovalEligibilityTests.approve
    record_approval = test_material_approval_eligibility.MaterialApprovalEligibilityTests.record_approval
    retire = test_material_approval_eligibility.MaterialApprovalEligibilityTests.retire
    generic_claim = test_material_approval_eligibility.MaterialApprovalEligibilityTests.generic_claim
    career = test_material_approval_eligibility.MaterialApprovalEligibilityTests.career
    unanswered = test_material_approval_eligibility.MaterialApprovalEligibilityTests.unanswered

    def setUp(self) -> None:
        test_material_approval_eligibility.MaterialApprovalEligibilityTests.setUp(self)
        self.renderer = RecordingRenderer()
        self.service = MaterialService(self.repository, self.renderer)

    def assert_rejected(self) -> None:
        before = self.repository._connection.serialize()
        with self.assertRaisesRegex(MaterialHistoryIntegrityError, f"^{ERROR}$"):
            self.service.validate_historical_inventory()
        self.assertEqual(self.repository._connection.serialize(), before)
        self.assertFalse(self.repository._connection.in_transaction)

    def orphan(self, repository: SQLiteRepository, material: dict, kind: str, suffix: str, *, completed: bool = True) -> str:
        if kind == "material_build":
            original = self.repository.get_workflow_run(material["workflow_run_id"])
            fields = {key: value for key, value in json.loads(original["input_json"]).items()
                if key not in {"version", "idempotency_sha256"}}
            at = material["created_at"]
        else:
            fields = {"material_id": material["id"], "bundle_sha256": material["bundle_sha256"], "actor_id": "fictional-reviewer"}
            at = APPROVED_AT
        payload = request_input(f"fictional-orphan-{suffix}", fields)
        with repository.transaction():
            workflow = start_workflow(repository, kind, payload, at)
            if completed:
                finish_workflow(repository, workflow["id"], [material["id"]], at)
        if completed:
            # These completed rows satisfy their strict record contracts with
            # hypothetical bindings, but no real owning row is changed or added.
            workflow = repository.get_workflow_run(workflow["id"])
            if kind == "material_build":
                hypothetical = {**material, "workflow_run_id": workflow["id"]}
                self.assertEqual(validate_material_build_record(hypothetical, workflow), payload)
            else:
                hypothetical = {"material_id": material["id"], "bundle_sha256": material["bundle_sha256"],
                    "actor_id": "fictional-reviewer", "approved_at": at, "workflow_run_id": workflow["id"]}
                validate_approval_record(material, hypothetical, workflow)
        return workflow["id"]

    def test_empty_and_profile_only_inventory_require_no_material_or_profile_traversal(self) -> None:
        with SQLiteRepository(":memory:").initialize() as repository:
            service = MaterialService(repository, RecordingRenderer())
            before = repository._connection.serialize()
            with patch.object(service, "_validated_historical_material", side_effect=AssertionError("No material to load")):
                self.assertIsNone(service.validate_historical_inventory())
            self.assertEqual(repository._connection.serialize(), before)
        before = self.repository._connection.serialize()
        with patch.object(self.service, "get", side_effect=AssertionError("No material to load")), \
             patch.object(ProfileService, "validated_profile", side_effect=AssertionError("No material facts")):
            self.assertIsNone(self.service.validate_historical_inventory())
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_mixed_read_only_inventory_includes_interrupted_child_and_checks_each_material_once(self) -> None:
        legacy = self.build(transformation="approved_text_selection@1")
        self.approve(legacy)
        modern = self.build()
        partial = self.build(questions=[self.unanswered()])
        optional = self.build(questions=[self.unanswered(required=False)])
        self.approve(optional)
        batch = BatchService(self.repository, self.service, clock=lambda: MATERIAL_AT + timedelta(days=3))
        batch_id = batch.create({"schema_version": 1, "claim_ids": list(self.claim_ids),
            "jobs": [{"job_id": self.job_id}]}, idempotency_key="fictional-interrupted-material-inventory")["batch_id"]
        original_checkpoint = batch._checkpoint
        def crash_after_child(identifier, item, state, owner, epoch):
            if state["stage"] == "draft":
                raise RuntimeError("fictional parent interruption")
            return original_checkpoint(identifier, item, state, owner, epoch)
        with patch.object(batch, "_checkpoint", side_effect=crash_after_child), \
             patch("grounded_apply.services.materials.timestamp", return_value=MATERIAL_AT.isoformat()), self.assertRaises(RuntimeError):
            batch.run(batch_id)
        material_ids = self.repository.list_material_ids()
        self.assertEqual(len(material_ids), 5)
        interrupted = next(identifier for identifier in material_ids if identifier not in {legacy, modern, partial, optional})
        item = self.repository.list_preparation_items(batch_id)[0]
        self.assertTrue(all(json.loads(event["state_json"])["material_id"] is None
            for event in self.repository.list_preparation_events(item["id"])))
        self.assertIsNone(self.repository.get_material_approval(interrupted))
        self.retire(self.claim_ids[0], at=APPROVAL_TIME + timedelta(seconds=1))
        with self.assertRaises(MaterialBlocked):
            self.service.get(legacy)
        image = self.repository._connection.serialize()
        with tempfile.TemporaryDirectory(prefix="gapply-fictional-material-inventory-") as temporary:
            database = Path(temporary) / "synthetic.db"
            database.write_bytes(image)
            database.chmod(0o600)
            before = (database.read_bytes(), database.stat().st_mtime_ns, sorted(path.name for path in database.parent.iterdir()))
            with SQLiteRepository(database, read_only=True) as repository:
                renderer = RecordingRenderer()
                service = MaterialService(repository, renderer)
                statements, profiles = [], []
                original_profile = ProfileService.validated_profile
                def profile_read(profile, *, apply_retirements=True):
                    profiles.append((apply_retirements, repository._connection.in_transaction))
                    return original_profile(profile, apply_retirements=apply_retirements)
                repository._connection.set_trace_callback(statements.append)
                with patch.object(repository, "material_history_inventory", wraps=repository.material_history_inventory) as inventory, \
                     patch.object(service, "_validated_historical_material", wraps=service._validated_historical_material) as checked, \
                     patch.object(service, "get", wraps=service.get) as bundles, \
                     patch.object(renderer, "validate", wraps=renderer.validate) as pdfs, \
                     patch.object(repository, "get_material_approval", wraps=repository.get_material_approval) as approvals, \
                     patch.object(ProfileService, "validated_profile", autospec=True, side_effect=profile_read), \
                     patch.object(renderer, "render", side_effect=AssertionError("No rendering")), \
                     patch.object(service, "validate_historical_facts", side_effect=AssertionError("No public audit chaining")), \
                     patch.object(service, "validate_historical_approval_record", side_effect=AssertionError("No public audit chaining")), \
                     patch.object(service, "validate_historical_approval_eligibility", side_effect=AssertionError("No public audit chaining")), \
                     patch.object(service, "is_approved", side_effect=AssertionError("No current authority")), \
                     patch.object(service, "plan", side_effect=AssertionError("No current planning")), \
                     patch.object(QuestionnaireService, "prepare", side_effect=AssertionError("No current answers")):
                    self.assertIsNone(service.validate_historical_inventory())
                inventory.assert_called_once_with()
                self.assertEqual(Counter(call.args[0] for call in checked.call_args_list), Counter(material_ids))
                self.assertEqual(Counter(call.args[0] for call in bundles.call_args_list), Counter(material_ids))
                self.assertTrue(all(call.kwargs == {"require_current": False} for call in bundles.call_args_list))
                self.assertEqual(Counter(call.args[0] for call in approvals.call_args_list), Counter(material_ids))
                self.assertEqual(pdfs.call_count, 5)
                self.assertEqual(profiles, [(False, True)] * 5)
                self.assertEqual([sql for sql in statements if sql in {"BEGIN", "ROLLBACK"}], ["BEGIN", "ROLLBACK"])
                self.assertFalse(repository._connection.in_transaction)
            self.assertEqual((database.read_bytes(), database.stat().st_mtime_ns,
                sorted(path.name for path in database.parent.iterdir())), before)
        self.assertEqual(self.repository._connection.serialize(), image)

    def test_claim_composite_identity_preserves_many_claims_shared_claims_and_answer_only_links(self) -> None:
        answer_claim = self.generic_claim("inventory-answer")
        first = self.build(questions=[self.career(identifier="fictional-shared"),
            self.career(identifier="fictional-answer-only", claim_id=answer_claim)])
        second = self.build()
        inventory = self.repository.material_history_inventory()
        first_claims = {row["claim_id"] for row in inventory["claims"] if row["material_id"] == first}
        second_claims = {row["claim_id"] for row in inventory["claims"] if row["material_id"] == second}
        self.assertEqual(first_claims, second_claims | {answer_claim})
        self.assertGreater(len(first_claims), 1)
        self.assertEqual(len(inventory["claims"]), len(first_claims) + len(second_claims))
        self.assertIsNone(self.service.validate_historical_inventory())

    def test_complete_and_incomplete_build_and_approval_orphans_fail_without_materials(self) -> None:
        material_id = self.build()
        material = self.service.get(material_id, require_current=False)
        for kind in ("material_build", "material_approval"):
            for completed in (True, False):
                with self.subTest(kind=kind, completed=completed), SQLiteRepository(":memory:").initialize() as repository:
                    self.orphan(repository, material, kind, f"{kind}-{completed}", completed=completed)
                    self.assertEqual(repository.list_material_ids(), ())
                    image = repository._connection.serialize()
                    validate_profile_snapshot(image)
                    service = MaterialService(repository, RecordingRenderer())
                    with self.assertRaisesRegex(MaterialHistoryIntegrityError, f"^{ERROR}$"):
                        service.validate_historical_inventory()
                    self.assertEqual(repository._connection.serialize(), image)

    def test_valid_linked_material_cannot_conceal_complete_orphans_of_either_kind(self) -> None:
        material_id = self.build()
        material = self.service.get(material_id, require_current=False)
        original = self.repository._connection.serialize()
        class RollbackOrphan(Exception):
            pass
        for kind in ("material_build", "material_approval"):
            with self.subTest(kind=kind), self.assertRaises(RollbackOrphan):
                with self.repository.transaction():
                    self.orphan(self.repository, material, kind, "linked-" + kind)
                    self.assertEqual(self.repository.get_material_version(material_id)["workflow_run_id"], material["workflow_run_id"])
                    self.assertIsNone(self.repository.get_material_approval(material_id))
                    self.assertIsNone(self.service.validate_historical_facts(material_id))
                    self.assertIsNone(self.service.validate_historical_approval_record(material_id))
                    before = self.repository._connection.serialize()
                    with self.assertRaisesRegex(MaterialHistoryIntegrityError, f"^{ERROR}$"):
                        self.service.validate_historical_inventory()
                    self.assertEqual(self.repository._connection.serialize(), before)
                    raise RollbackOrphan
            self.assertEqual(self.repository._connection.serialize(), original)

    def test_missing_extra_and_duplicate_rows_fail_in_every_component(self) -> None:
        material_id = self.build()
        self.approve(material_id)
        inventory = self.repository.material_history_inventory()
        for component in ("materials", "claims", "approvals", "workflows"):
            for mode in ("missing", "extra", "duplicate"):
                with self.subTest(component=component, mode=mode):
                    changed = deepcopy(inventory)
                    rows = changed[component]
                    if mode == "missing":
                        changed[component] = rows[1:]
                    elif mode == "duplicate":
                        changed[component] = rows + (deepcopy(rows[0]),)
                    else:
                        extra = deepcopy(rows[0])
                        extra["claim_id" if component == "claims" else "material_id" if component == "approvals" else "id"] = "fictional-extra-record"
                        changed[component] = rows + (extra,)
                    with patch.object(self.repository, "material_history_inventory", return_value=changed):
                        self.assert_rejected()

    def test_equal_count_wrong_ownership_and_workflow_kind_fail(self) -> None:
        first = self.build()
        self.approve(first)
        second = self.build()
        inventory = self.repository.material_history_inventory()
        second_build = self.repository.get_material_version(second)["workflow_run_id"]
        changes = []
        for component, field, value in (("materials", "workflow_run_id", "fictional-foreign-workflow"),
            ("claims", "claim_id", "fictional-foreign-claim"), ("claims", "material_id", "fictional-foreign-material"),
            ("approvals", "material_id", second), ("approvals", "workflow_run_id", second_build),
            ("workflows", "workflow_type", "batch_create")):
            changed = deepcopy(inventory)
            changed[component][0][field] = value
            changes.append(changed)
        for index, changed in enumerate(changes):
            with self.subTest(ownership=index), patch.object(self.repository, "material_history_inventory", return_value=changed):
                self.assertEqual({key: len(rows) for key, rows in changed.items()}, {key: len(rows) for key, rows in inventory.items()})
                self.assert_rejected()

    def test_closed_inventory_shapes_accept_harmless_row_reordering(self) -> None:
        self.build()
        approved = self.build()
        self.approve(approved)
        inventory = self.repository.material_history_inventory()
        with patch.object(self.repository, "material_history_inventory", return_value={key: tuple(reversed(rows)) for key, rows in inventory.items()}):
            self.assertIsNone(self.service.validate_historical_inventory())
        variants = [None, {key: rows for key, rows in inventory.items() if key != "claims"}, {**inventory, "extra": ()},
            {**inventory, "claims": list(inventory["claims"])}]
        for component, field, value in (("materials", "id", True), ("claims", "claim_id", "invalid/id"),
            ("approvals", "extra", None), ("workflows", "workflow_type", None)):
            changed = deepcopy(inventory)
            changed[component][0][field] = value
            variants.append(changed)
        missing = deepcopy(inventory)
        del missing["materials"][0]["workflow_run_id"]
        variants.append(missing)
        for index, changed in enumerate(variants):
            with self.subTest(shape=index), patch.object(self.repository, "material_history_inventory", return_value=changed):
                self.assert_rejected()

    def test_record_only_approval_over_required_need_info_fails_aggregate_eligibility(self) -> None:
        self.build()
        partial = self.build(questions=[self.unanswered()])
        self.assertIsNone(self.service.validate_historical_inventory())
        self.record_approval(partial)
        self.assertIsNone(self.service.validate_historical_approval_record(partial))
        self.assertIsNone(self.service.validate_historical_approval_facts(partial))
        self.assert_rejected()

    def test_corrupt_material_or_approval_among_other_valid_materials_is_not_skipped(self) -> None:
        self.build()
        material_id = self.build()
        self.approve(material_id)
        material = self.repository.get_material_version(material_id)
        get = self.repository.get_material_version
        with patch.object(self.repository, "get_material_version", side_effect=lambda identifier:
            {**material, "pdf_bytes": b"fictional-corrupt-pdf"} if identifier == material_id else get(identifier)):
            self.assert_rejected()
        approval = self.repository.get_material_approval(material_id)
        with self.repository.transaction():
            self.repository.update_workflow_run(approval["workflow_run_id"], generated_artifacts=["fictional-wrong-material"])
        self.assert_rejected()

    def test_per_material_private_audit_binds_requested_id_before_profile(self) -> None:
        first = self.build()
        second = self.build()
        wrong = self.service.get(second, require_current=False)
        get = self.service.get
        with patch.object(self.service, "get", side_effect=lambda identifier, **kwargs:
            wrong if identifier == first else get(identifier, **kwargs)):
            with self.repository.read_transaction(), \
                 patch.object(ProfileService, "validated_profile", side_effect=AssertionError("Reject identity before profile")), \
                 self.assertRaises(ValueError):
                self.service._validated_historical_material(first)
            self.assert_rejected()

    def test_borrowed_caller_work_survives_success_and_orphan_failure(self) -> None:
        material_id = self.build()
        material = self.service.get(material_id, require_current=False)
        before = self.repository._connection.serialize()
        abort = RuntimeError("fictional caller rollback")
        with self.assertRaises(RuntimeError) as raised:
            with self.repository.transaction():
                marker = self.repository.add_workflow_run(workflow_type="fictional-unrelated", status="queued")
                self.assertIsNone(self.service.validate_historical_inventory())
                orphan_id = self.orphan(self.repository, material, "material_approval", "caller-orphan")
                with self.assertRaisesRegex(MaterialHistoryIntegrityError, f"^{ERROR}$"):
                    self.service.validate_historical_inventory()
                self.assertTrue(self.repository._connection.in_transaction)
                self.assertIsNotNone(self.repository.get_workflow_run(marker["id"]))
                self.assertIsNotNone(self.repository.get_workflow_run(orphan_id))
                raise abort
        self.assertIs(raised.exception, abort)
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_fixed_failures_hide_inventory_and_material_storage_details(self) -> None:
        self.build()
        for method in ("material_history_inventory", "get_material_version", "list_material_claim_ids",
                       "get_material_approval", "get_workflow_run"):
            with self.subTest(method=method), patch.object(self.repository, method,
                side_effect=sqlite3.OperationalError("fictional private SQL detail")):
                self.assert_rejected()
        with patch.object(self.service, "_validated_historical_material", side_effect=ValueError("fictional private audit detail")):
            self.assert_rejected()

    def test_interrupt_identity_and_owned_transaction_cleanup_are_preserved(self) -> None:
        self.build()
        before = self.repository._connection.serialize()
        for target, method in ((self.repository, "material_history_inventory"), (self.service, "_validated_historical_material")):
            sentinel = KeyboardInterrupt("fictional inventory interruption")
            with self.subTest(method=method), patch.object(target, method, side_effect=sentinel), self.assertRaises(KeyboardInterrupt) as raised:
                self.service.validate_historical_inventory()
            self.assertIs(raised.exception, sentinel)
            self.assertFalse(self.repository._connection.in_transaction)
            self.assertEqual(self.repository._connection.serialize(), before)


if __name__ == "__main__":
    unittest.main()
