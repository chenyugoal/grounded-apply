from __future__ import annotations

import json
import sqlite3
import unittest
from copy import deepcopy
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

from grounded_apply.repositories import SQLiteRepository
from grounded_apply.repositories.snapshots import validate_profile_snapshot
from grounded_apply.services.batch_history import validate_batch_workflow
from grounded_apply.services.batches import BatchIntegrityError, BatchService, validate_batch_manifest
from grounded_apply.services.materials import MaterialService
from grounded_apply.services.profile import ProfileService
from grounded_apply.services.questionnaires import QuestionnaireService
from grounded_apply.services.workflow import digest, finish_workflow, request_input, start_workflow
from tests import test_batch_material_history, test_batches
from tests.test_batches import RecordingRenderer


ERROR = "Batch historical inventory failed integrity checks"


class BatchInventoryTests(unittest.TestCase):
    setUp = test_batches.BatchTests.setUp
    manifest = test_batches.BatchTests.manifest
    create = test_batches.BatchTests.create
    material_id = test_batch_material_history.BatchMaterialHistoryTests.material_id
    retire = test_batch_material_history.BatchMaterialHistoryTests.retire
    rewrite_events = test_batch_material_history.BatchMaterialHistoryTests.rewrite_events

    def completed(self, key: str = "fictional-inventory-batch", *, partial: bool = False, count: int = 1) -> str:
        manifest = self.manifest(count)
        if partial:
            manifest["questions"] = [{"id": "fictional-sponsorship", "text": "Do you need sponsorship?",
                "claim_ids": [], "required": True}]
        batch_id = self.create(manifest, key=key)
        self.service.run(batch_id)
        return batch_id

    def assert_rejected(self) -> None:
        before = self.repository._connection.serialize()
        with self.assertRaisesRegex(BatchIntegrityError, f"^{ERROR}$"):
            self.service.validate_historical_inventory()
        self.assertEqual(self.repository._connection.serialize(), before)
        self.assertFalse(self.repository._connection.in_transaction)

    def orphan(self, repository: SQLiteRepository, suffix: str, *, completed: bool = True) -> str:
        manifest = validate_batch_manifest(self.manifest())
        payload = request_input(f"fictional-orphan-{suffix}", {"manifest_sha256": digest(manifest), "batch_schema_version": 1})
        batch_id = str(uuid5(NAMESPACE_URL, "grounded-apply.batch@1/" + payload["idempotency_sha256"]))
        at = datetime.now(UTC).isoformat(timespec="microseconds")
        with repository.transaction():
            workflow = start_workflow(repository, "batch_create", payload, at)
            if completed:
                finish_workflow(repository, workflow["id"], [batch_id], at)
        if completed:
            # A strict valid completed record can still lack its owning batch.
            validate_batch_workflow(repository.get_workflow_run(workflow["id"]), payload,
                batch_id=batch_id, created_at=at, workflow_run_id=workflow["id"])
        return workflow["id"]

    def test_empty_and_profile_only_inventories_need_no_batch_or_material_reads(self) -> None:
        with SQLiteRepository(":memory:").initialize() as repository:
            service = BatchService(repository, MaterialService(repository, RecordingRenderer()))
            before = repository._connection.serialize()
            with patch.object(service, "_validated_historical_materials", side_effect=AssertionError("No batch to audit")):
                self.assertIsNone(service.validate_historical_inventory())
            self.assertEqual(repository._connection.serialize(), before)
        before = self.database.read_bytes()
        with patch.object(self.service, "_validated_historical_materials", side_effect=AssertionError("No batch to audit")), \
             patch.object(self.materials, "get", side_effect=AssertionError("No batch material")), \
             patch.object(ProfileService, "validated_profile", side_effect=AssertionError("No batch facts")):
            self.assertIsNone(self.service.validate_historical_inventory())
        self.assertEqual(self.database.read_bytes(), before)

    def test_mixed_read_only_inventory_audits_each_batch_once_without_cross_batch_authority_cache(self) -> None:
        draft = self.completed("fictional-draft")
        reused = self.completed("fictional-reused")
        partial = self.completed("fictional-partial", partial=True)
        self.service.run(partial)
        queued = self.create(key="fictional-queued")
        interrupted_spec = self.manifest()
        interrupted_spec["claim_ids"] = list(reversed(self.claim_ids))
        interrupted = self.create(interrupted_spec, key="fictional-interrupted")
        original_checkpoint = self.service._checkpoint
        def crash_after_child(batch, item, state, owner, epoch):
            if state["stage"] == "draft":
                raise RuntimeError("fictional interrupted parent")
            return original_checkpoint(batch, item, state, owner, epoch)
        with patch.object(self.service, "_checkpoint", side_effect=crash_after_child), self.assertRaises(RuntimeError):
            self.service.run(interrupted)
        material_id = self.material_id(draft)
        self.assertEqual(self.material_id(reused), material_id)
        self.assertEqual(len(self.repository.list_material_ids()), 3)
        self.retire()
        self.assertEqual(self.service.run(draft)["counts"]["blocked"], 1)
        batch_ids = (draft, reused, partial, queued, interrupted)
        image = self.repository._connection.serialize()
        before = (self.database.read_bytes(), self.database.stat().st_mtime_ns,
            sorted(path.name for path in self.root.iterdir()))
        with SQLiteRepository.from_snapshot(image) as repository:
            renderer = RecordingRenderer()
            materials = MaterialService(repository, renderer)
            service = BatchService(repository, materials)
            trace = []
            repository._connection.set_trace_callback(trace.append)
            with patch.object(repository, "batch_history_inventory", wraps=repository.batch_history_inventory) as inventory, \
                 patch.object(service, "_validated_historical_materials", wraps=service._validated_historical_materials) as histories, \
                 patch.object(materials, "get", wraps=materials.get) as bundles, \
                 patch.object(materials, "_validate_historical_fact_snapshot", wraps=materials._validate_historical_fact_snapshot) as facts, \
                 patch.object(renderer, "validate", wraps=renderer.validate) as pdfs, \
                 patch.object(renderer, "render", side_effect=AssertionError("No render")), \
                 patch.object(service, "get", side_effect=AssertionError("No current batch review")), \
                 patch.object(service, "validate_historical_materials", side_effect=AssertionError("No repeated public audit")), \
                 patch.object(materials, "validate_historical_facts", side_effect=AssertionError("No repeated bundle audit")), \
                 patch.object(materials, "plan", side_effect=AssertionError("No current planning")), \
                 patch.object(materials, "is_approved", side_effect=AssertionError("No current authority")), \
                 patch.object(QuestionnaireService, "prepare", side_effect=AssertionError("No current answers")):
                self.assertIsNone(service.validate_historical_inventory())
            inventory.assert_called_once_with()
            self.assertEqual(sorted(call.args[0] for call in histories.call_args_list), sorted(batch_ids))
            self.assertEqual(histories.call_count, 5)
            self.assertEqual(sorted(call.args[0] for call in bundles.call_args_list),
                sorted([material_id, material_id, self.material_id(partial)]))
            self.assertTrue(all(call.kwargs == {"require_current": False} for call in bundles.call_args_list))
            self.assertEqual(facts.call_count, 3)
            self.assertEqual(pdfs.call_count, 3)
            self.assertEqual([sql for sql in trace if sql in {"BEGIN", "ROLLBACK"}], ["BEGIN", "ROLLBACK"])
            self.assertFalse(repository._connection.in_transaction)
            self.assertEqual(repository._connection.serialize(), image)
        self.assertEqual((self.database.read_bytes(), self.database.stat().st_mtime_ns,
            sorted(path.name for path in self.root.iterdir())), before)

    def test_strict_completed_and_incomplete_orphans_fail_without_any_batch(self) -> None:
        for completed in (True, False):
            with self.subTest(completed=completed), SQLiteRepository(":memory:").initialize() as repository:
                service = BatchService(repository, MaterialService(repository, RecordingRenderer()))
                self.orphan(repository, str(completed), completed=completed)
                self.assertEqual(repository.list_preparation_batches(), [])
                image = repository._connection.serialize()
                validate_profile_snapshot(image)
                with self.assertRaisesRegex(BatchIntegrityError, f"^{ERROR}$"):
                    service.validate_historical_inventory()
                self.assertEqual(repository._connection.serialize(), image)

    def test_valid_per_batch_history_does_not_conceal_complete_orphan_workflow(self) -> None:
        batch_id = self.create()
        self.orphan(self.repository, "alongside-valid")
        self.assertIsNone(self.service.validate_historical_materials(batch_id))
        self.assert_rejected()

    def test_missing_extra_and_duplicate_metadata_rows_fail_for_each_component(self) -> None:
        self.create()
        inventory = self.repository.batch_history_inventory()
        for component in ("batches", "items", "events", "leases", "workflows"):
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
                        extra["batch_id" if component == "leases" else "id"] = "fictional-unvisited-record"
                        changed[component] = rows + (extra,)
                    with patch.object(self.repository, "batch_history_inventory", return_value=changed):
                        self.assert_rejected()

    def test_equal_count_wrong_ownership_or_workflow_kind_is_not_accepted(self) -> None:
        self.create(key="fictional-first")
        self.create(key="fictional-second")
        inventory = self.repository.batch_history_inventory()
        changes = []
        for component, field, value in (
            ("batches", "workflow_run_id", inventory["batches"][1]["workflow_run_id"]),
            ("items", "batch_id", next(row["id"] for row in inventory["batches"] if row["id"] != inventory["items"][0]["batch_id"])),
            ("events", "item_id", next(row["id"] for row in inventory["items"] if row["id"] != inventory["events"][0]["item_id"])),
            ("leases", "batch_id", "fictional-foreign-batch"),
            ("workflows", "workflow_type", "material_build"),
        ):
            changed = deepcopy(inventory)
            changed[component][0][field] = value
            changes.append(changed)
        for index, changed in enumerate(changes):
            with self.subTest(ownership=index), patch.object(self.repository, "batch_history_inventory", return_value=changed):
                self.assertEqual({key: len(rows) for key, rows in changed.items()}, {key: len(rows) for key, rows in inventory.items()})
                self.assert_rejected()

    def test_closed_metadata_contract_accepts_only_harmless_order_changes(self) -> None:
        self.create(key="fictional-first")
        self.create(key="fictional-second")
        inventory = self.repository.batch_history_inventory()
        before = self.database.read_bytes()
        with patch.object(self.repository, "batch_history_inventory", return_value={key: tuple(reversed(rows)) for key, rows in inventory.items()}):
            self.assertIsNone(self.service.validate_historical_inventory())
        self.assertEqual(self.database.read_bytes(), before)
        changes = [None, {key: rows for key, rows in inventory.items() if key != "leases"},
            {**inventory, "extra": ()}, {**inventory, "events": list(inventory["events"])}]
        for component, field, value in (("batches", "id", True), ("items", "batch_id", "invalid/id"),
                                        ("events", "extra", None), ("workflows", "workflow_type", None)):
            changed = deepcopy(inventory)
            changed[component][0][field] = value
            changes.append(changed)
        missing = deepcopy(inventory)
        del missing["batches"][0]["workflow_run_id"]
        changes.append(missing)
        for index, changed in enumerate(changes):
            with self.subTest(shape=index), patch.object(self.repository, "batch_history_inventory", return_value=changed):
                self.assert_rejected()

    def test_earlier_invalid_draft_in_one_batch_is_not_hidden_by_other_valid_batches(self) -> None:
        valid = self.create(key="fictional-valid-queued")
        corrupt = self.completed("fictional-corrupt-partial", partial=True)
        self.assertIsNone(self.service.validate_historical_inventory())
        item = self.repository.list_preparation_items(corrupt)[0]
        events = self.repository.list_preparation_events(item["id"])
        blocked = json.loads(events[-1]["state_json"])
        def conceal(position, event, state):
            if position == len(events) - 1:
                state.update(stage="draft", blockers=[])
        self.rewrite_events(corrupt, conceal)
        with self.repository.transaction():
            self.service._append_event(item["id"], blocked, at=events[-1]["at"])
        self.assertIsNone(self.service.validate_historical_materials(valid))
        self.assertEqual(self.service.get(corrupt)["counts"]["blocked"], 1)
        self.assert_rejected()

    def test_corrupt_material_in_one_batch_refuses_the_whole_inventory(self) -> None:
        self.create(key="fictional-valid-queued")
        completed = self.completed(count=2)
        material_id = self.material_id(completed, 1)
        original = self.repository.get_material_version
        material = original(material_id)
        with patch.object(self.repository, "get_material_version", side_effect=lambda identifier:
            {**material, "pdf_bytes": b"fictional-corrupt-pdf"} if identifier == material_id else original(identifier)):
            self.assert_rejected()

    def test_borrowed_caller_work_survives_success_and_orphan_failure(self) -> None:
        self.create()
        before = self.repository._connection.serialize()
        abort = RuntimeError("fictional caller rollback")
        with self.assertRaises(RuntimeError) as raised:
            with self.repository.transaction():
                marker = self.repository.add_workflow_run(workflow_type="fictional-unrelated-work", status="queued")
                self.assertIsNone(self.service.validate_historical_inventory())
                orphan_id = self.orphan(self.repository, "caller-orphan")
                with self.assertRaisesRegex(BatchIntegrityError, f"^{ERROR}$"):
                    self.service.validate_historical_inventory()
                self.assertTrue(self.repository._connection.in_transaction)
                self.assertIsNotNone(self.repository.get_workflow_run(marker["id"]))
                self.assertIsNotNone(self.repository.get_workflow_run(orphan_id))
                raise abort
        self.assertIs(raised.exception, abort)
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_fixed_errors_hide_inventory_and_batch_storage_details(self) -> None:
        self.create()
        for method in ("batch_history_inventory", "get_preparation_batch", "list_preparation_items",
                       "list_preparation_events", "get_preparation_lease", "get_workflow_run"):
            with self.subTest(method=method), patch.object(self.repository, method,
                side_effect=sqlite3.OperationalError("fictional private storage detail")):
                self.assert_rejected()
        with patch.object(self.service, "_validated_historical_materials", side_effect=ValueError("fictional private audit detail")):
            self.assert_rejected()

    def test_interrupt_identity_and_owned_read_transaction_cleanup_are_preserved(self) -> None:
        self.create()
        before = self.repository._connection.serialize()
        for target, method in ((self.repository, "batch_history_inventory"), (self.service, "_validated_historical_materials")):
            sentinel = KeyboardInterrupt("fictional inventory interruption")
            with self.subTest(method=method), patch.object(target, method, side_effect=sentinel), self.assertRaises(KeyboardInterrupt) as raised:
                self.service.validate_historical_inventory()
            self.assertIs(raised.exception, sentinel)
            self.assertFalse(self.repository._connection.in_transaction)
            self.assertEqual(self.repository._connection.serialize(), before)


if __name__ == "__main__":
    unittest.main()
