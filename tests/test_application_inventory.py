from __future__ import annotations

import sqlite3
import unittest
from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

from grounded_apply.repositories import RepositoryError, SQLiteRepository
from grounded_apply.services.application_history import validate_application_workflow
from grounded_apply.services.applications import ApplicationService
from grounded_apply.services.materials import MaterialService
from grounded_apply.services.profile import ProfileService
from grounded_apply.services.questionnaires import QuestionnaireService
from grounded_apply.services.workflow import finish_workflow, request_input, start_workflow
from tests import test_application_material_history
from tests.test_application_material_history import APPLIED_AT, READY_AT
from tests.test_material_history import MATERIAL_AT
from tests.test_materials import SyntheticRenderer


ERROR = "Application historical inventory failed integrity checks"


class ApplicationInventoryTests(unittest.TestCase):
    setUp = test_application_material_history.ApplicationMaterialHistoryTests.setUp
    _build = test_application_material_history.ApplicationMaterialHistoryTests._build
    build = test_application_material_history.ApplicationMaterialHistoryTests.build
    approve = test_application_material_history.ApplicationMaterialHistoryTests.approve
    move = test_application_material_history.ApplicationMaterialHistoryTests.move
    preparing = test_application_material_history.ApplicationMaterialHistoryTests.preparing
    history = test_application_material_history.ApplicationMaterialHistoryTests.history
    retire = test_application_material_history.ApplicationMaterialHistoryTests.retire
    imported_title = test_application_material_history.ApplicationMaterialHistoryTests.imported_title
    record_approval = test_application_material_history.ApplicationMaterialHistoryTests.record_approval

    def assert_rejected(self) -> None:
        before = self.repository._connection.serialize()
        with self.assertRaisesRegex(RepositoryError, f"^{ERROR}$"):
            self.applications.validate_historical_inventory()
        self.assertEqual(self.repository._connection.serialize(), before)
        self.assertFalse(self.repository._connection.in_transaction)

    def submitted(self) -> str:
        material_id = self.build()
        self.approve(material_id)
        return self.history(material_id)

    def orphan(self, repository: SQLiteRepository, kind: str, suffix: str) -> str:
        application_id, event_id = f"fictional-orphan-application-{suffix}", f"fictional-orphan-event-{suffix}"
        fields = {"actor_id": "fictional-user"}
        if kind == "application_create":
            fields["job_id"] = self.job_id
        else:
            fields.update(application_id=application_id, previous_sha256="0" * 64, target_state="archived",
                material_id=None, bundle_sha256=None, human_confirmed_submission=False)
        payload = request_input(f"fictional-orphan-key-{suffix}", fields)
        at = MATERIAL_AT.isoformat()
        with repository.transaction():
            workflow = start_workflow(repository, kind, payload, at)
            finish_workflow(repository, workflow["id"], [application_id, event_id], at)
        # These rows are complete valid workflow records, not malformed noise.
        validate_application_workflow(repository.get_workflow_run(workflow["id"]), kind, payload,
            application_id=application_id, event_id=event_id, event_at=at)
        return workflow["id"]

    def test_empty_database_and_profile_only_history_need_no_application_or_material_reads(self) -> None:
        with SQLiteRepository(":memory:").initialize() as repository:
            service = ApplicationService(repository, MaterialService(repository, SyntheticRenderer()))
            before = repository._connection.serialize()
            with patch.object(service, "_validated_history", side_effect=AssertionError("No application to load")):
                self.assertIsNone(service.validate_historical_inventory())
            self.assertEqual(repository._connection.serialize(), before)
        before = self.repository._connection.serialize()
        with (
            patch.object(self.applications, "_validated_history", side_effect=AssertionError("No application to load")),
            patch.object(self.service, "get", side_effect=AssertionError("Unrelated material reads")),
            patch.object(ProfileService, "validated_profile", side_effect=AssertionError("Unrelated profile reads")),
        ):
            self.assertIsNone(self.applications.validate_historical_inventory())
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_mixed_retired_snapshot_loads_each_application_once_in_one_read_transaction(self) -> None:
        material_id = self.build()
        self.approve(material_id)
        application_ids = (self.preparing(), self.history(material_id, applied=False), self.history(material_id))
        self.retire(self.claim_ids[0], at=APPLIED_AT + timedelta(seconds=1))
        source = self.repository._connection.serialize()
        original_submissions = {identifier: self.repository.get_submission_snapshot(identifier) for identifier in application_ids}
        with SQLiteRepository.from_snapshot(source) as repository:
            renderer = SyntheticRenderer()
            materials = MaterialService(repository, renderer)
            service = ApplicationService(repository, materials)
            states = []
            original_profile = ProfileService.validated_profile

            def profile_read(profile: ProfileService, *, apply_retirements: bool = True):
                states.append((apply_retirements, repository._connection.in_transaction))
                return original_profile(profile, apply_retirements=apply_retirements)

            statements = []
            repository._connection.set_trace_callback(statements.append)
            try:
                with (
                    patch.object(repository, "application_history_inventory", wraps=repository.application_history_inventory) as inventory,
                    patch.object(service, "_validated_history", wraps=service._validated_history) as histories,
                    patch.object(materials, "get", wraps=materials.get) as bundles,
                    patch.object(renderer, "validate", wraps=renderer.validate) as pdfs,
                    patch.object(ProfileService, "validated_profile", autospec=True, side_effect=profile_read),
                    patch.object(service, "get", side_effect=AssertionError("No current readiness")),
                    patch.object(service, "validate_historical_material_use", side_effect=AssertionError("No repeated public audits")),
                    patch.object(materials, "is_approved", side_effect=AssertionError("No current approval")),
                    patch.object(materials, "plan", side_effect=AssertionError("No current planning")),
                    patch.object(QuestionnaireService, "prepare", side_effect=AssertionError("No current answers")),
                ):
                    self.assertIsNone(service.validate_historical_inventory())
                inventory.assert_called_once_with()
                self.assertEqual(sorted(call.args[0] for call in histories.call_args_list), sorted(application_ids))
                self.assertTrue(all(call.kwargs == {"historical_use": True} for call in histories.call_args_list))
                self.assertEqual(histories.call_count, 3)
                self.assertEqual(bundles.call_count, 3)
                self.assertEqual(pdfs.call_count, 3)
                self.assertEqual(states, [(False, True)] * 3)
            finally:
                repository._connection.set_trace_callback(None)
            self.assertEqual(sum(sql == "BEGIN" for sql in statements), 1)
            self.assertEqual(sum(sql == "ROLLBACK" for sql in statements), 1)
            self.assertFalse(repository._connection.in_transaction)
            self.assertEqual({identifier: repository.get_submission_snapshot(identifier) for identifier in application_ids}, original_submissions)
        self.assertEqual(self.repository._connection.serialize(), source)

    def test_all_earlier_ready_replacement_rounds_preserve_shared_origin_workflow_ownership(self) -> None:
        claim_id = self.imported_title("inventory-old-material", subject="fictional-inventory-role")
        old_material = self.build((claim_id,))
        self.record_approval(old_material)
        new_material = self.build()
        self.record_approval(new_material)
        application_id = self.history(old_material, applied=False)
        self.move(application_id, "preparing", at=READY_AT + timedelta(hours=1))
        self.move(application_id, "ready_for_review", at=READY_AT + timedelta(hours=2), material_id=new_material)
        self.move(application_id, "applied", at=APPLIED_AT, material_id=new_material, confirm_submitted=True)
        self.retire(claim_id, at=READY_AT + timedelta(hours=1))
        inventory = self.repository.application_history_inventory()
        self.assertEqual(len(inventory["workflows"]), len(inventory["events"]))
        self.assertEqual(sum(row["workflow_type"] == "application_create" for row in inventory["workflows"]), 1)
        before = self.repository._connection.serialize()
        with patch.object(self.service, "get", wraps=self.service.get) as bundles:
            self.assertIsNone(self.applications.validate_historical_inventory())
        self.assertEqual([call.args[0] for call in bundles.call_args_list], [old_material, new_material, new_material])
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_complete_orphan_create_and_transition_workflows_fail_even_without_applications(self) -> None:
        for kind in ("application_create", "application_transition"):
            with self.subTest(kind=kind), SQLiteRepository(":memory:").initialize() as repository:
                service = ApplicationService(repository, MaterialService(repository, SyntheticRenderer()))
                self.orphan(repository, kind, kind)
                self.assertEqual(repository.list_applications(), [])
                before = repository._connection.serialize()
                with self.assertRaisesRegex(RepositoryError, f"^{ERROR}$"):
                    service.validate_historical_inventory()
                self.assertEqual(repository._connection.serialize(), before)

    def test_valid_per_application_audits_do_not_hide_extra_completed_workflows(self) -> None:
        application_id = self.preparing()
        self.orphan(self.repository, "application_create", "extra-create")
        self.orphan(self.repository, "application_transition", "extra-transition")
        self.assertIsNone(self.applications.validate_historical_material_use(application_id))
        self.assert_rejected()

    def test_missing_extra_and_duplicate_inventory_rows_fail_in_every_component(self) -> None:
        self.submitted()
        inventory = self.repository.application_history_inventory()
        for component in ("applications", "events", "submissions", "workflows"):
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
                        extra["application_id" if component == "submissions" else "id"] = "fictional-unvisited-record"
                        changed[component] = rows + (extra,)
                    with patch.object(self.repository, "application_history_inventory", return_value=changed):
                        self.assert_rejected()

    def test_wrong_ownership_with_unchanged_counts_cannot_pass_inventory_comparison(self) -> None:
        submitted = self.submitted()
        pending = self.preparing()
        inventory = self.repository.application_history_inventory()
        changes = []
        wrong_event = deepcopy(inventory)
        index = next(i for i, row in enumerate(wrong_event["events"]) if row["application_id"] == submitted)
        wrong_event["events"][index]["application_id"] = pending
        changes.append(wrong_event)
        wrong_workflow = deepcopy(inventory)
        wrong_workflow["events"][index]["workflow_run_id"] = next(row["workflow_run_id"] for row in inventory["events"] if row["application_id"] == pending)
        changes.append(wrong_workflow)
        wrong_kind = deepcopy(inventory)
        wrong_kind["workflows"][0]["workflow_type"] = ("application_transition"
            if wrong_kind["workflows"][0]["workflow_type"] == "application_create" else "application_create")
        changes.append(wrong_kind)
        for field, value in (("application_id", pending), ("event_id", inventory["events"][index]["id"]),
                             ("material_id", "fictional-foreign-material")):
            wrong_submission = deepcopy(inventory)
            if field == "event_id":
                value = next(row["id"] for row in inventory["events"] if row["application_id"] == pending)
            wrong_submission["submissions"][0][field] = value
            changes.append(wrong_submission)
        for index, changed in enumerate(changes):
            with self.subTest(ownership=index), patch.object(self.repository, "application_history_inventory", return_value=changed):
                self.assertEqual({key: len(rows) for key, rows in changed.items()}, {key: len(rows) for key, rows in inventory.items()})
                self.assert_rejected()

    def test_inventory_contract_is_closed_and_reordered_valid_rows_remain_valid(self) -> None:
        self.preparing()
        self.preparing()
        inventory = self.repository.application_history_inventory()
        reversed_inventory = {key: tuple(reversed(rows)) for key, rows in inventory.items()}
        before = self.repository._connection.serialize()
        with patch.object(self.repository, "application_history_inventory", return_value=reversed_inventory):
            self.assertIsNone(self.applications.validate_historical_inventory())
        self.assertEqual(self.repository._connection.serialize(), before)
        changes = [None, {key: value for key, value in inventory.items() if key != "events"},
                   {**inventory, "fictional_extra": ()}, {**inventory, "events": list(inventory["events"])}]
        extra_field = deepcopy(inventory)
        extra_field["events"][0]["fictional_extra"] = None
        changes.append(extra_field)
        invalid_id = deepcopy(inventory)
        invalid_id["applications"][0]["id"] = None
        changes.append(invalid_id)
        missing_field = deepcopy(inventory)
        del missing_field["events"][0]["workflow_run_id"]
        changes.append(missing_field)
        for index, changed in enumerate(changes):
            with self.subTest(shape=index), patch.object(self.repository, "application_history_inventory", return_value=changed):
                self.assert_rejected()

    def test_one_corrupt_application_among_valid_applications_refuses_the_whole_inventory(self) -> None:
        valid, corrupt = self.preparing(), self.preparing()
        self.assertIsNone(self.applications.validate_historical_material_use(valid))
        event = self.repository.list_application_events(corrupt)[-1]
        original = self.repository.get_workflow_run
        workflow = original(event["workflow_run_id"])
        with patch.object(self.repository, "get_workflow_run", side_effect=lambda identifier:
            {**workflow, "model_name": "fictional-model"} if identifier == workflow["id"] else original(identifier)):
            self.assert_rejected()

    def test_success_and_orphan_failure_preserve_borrowed_caller_work(self) -> None:
        self.preparing()
        before = self.repository._connection.serialize()
        abort = RuntimeError("fictional caller rollback")
        with self.assertRaises(RuntimeError) as raised:
            with self.repository.transaction():
                self.repository.add_workflow_run(run_id="fictional-caller-work", workflow_type="fictional-caller-work", status="queued")
                self.assertIsNone(self.applications.validate_historical_inventory())
                orphan_id = self.orphan(self.repository, "application_transition", "caller-orphan")
                with self.assertRaisesRegex(RepositoryError, f"^{ERROR}$"):
                    self.applications.validate_historical_inventory()
                self.assertTrue(self.repository._connection.in_transaction)
                self.assertIsNotNone(self.repository.get_workflow_run("fictional-caller-work"))
                self.assertIsNotNone(self.repository.get_workflow_run(orphan_id))
                raise abort
        self.assertIs(raised.exception, abort)
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_fixed_failures_hide_inventory_and_application_storage_details(self) -> None:
        self.preparing()
        for method in ("application_history_inventory", "get_application", "list_application_events", "get_workflow_run"):
            with self.subTest(method=method), patch.object(self.repository, method,
                side_effect=sqlite3.OperationalError("fictional-private-inventory-detail")):
                self.assert_rejected()
        with patch.object(self.applications, "_validated_history", side_effect=ValueError("fictional-private-history-detail")):
            self.assert_rejected()

    def test_interrupt_identity_and_owned_transaction_cleanup_are_preserved(self) -> None:
        self.preparing()
        before = self.repository._connection.serialize()
        for target, method in ((self.repository, "application_history_inventory"), (self.applications, "_validated_history")):
            interrupt = KeyboardInterrupt("fictional cancellation")
            with self.subTest(method=method), patch.object(target, method, side_effect=interrupt):
                with self.assertRaises(KeyboardInterrupt) as raised:
                    self.applications.validate_historical_inventory()
            self.assertIs(raised.exception, interrupt)
            self.assertFalse(self.repository._connection.in_transaction)
            self.assertEqual(self.repository._connection.serialize(), before)


if __name__ == "__main__":
    unittest.main()
