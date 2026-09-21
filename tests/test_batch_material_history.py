from __future__ import annotations

import json
import sqlite3
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from grounded_apply.repositories import SQLiteRepository
from grounded_apply.repositories.snapshots import validate_profile_snapshot
from grounded_apply.services.batches import BatchIntegrityError, BatchService
from grounded_apply.services.materials import MaterialService
from grounded_apply.services.profile import ProfileService
from grounded_apply.services.profile_lifecycle import ProfileLifecycleService
from grounded_apply.services.questionnaires import QuestionnaireService
from grounded_apply.services.workflow import canonical, digest, finish_workflow, request_input, start_workflow
from tests import test_batch_search_history
from tests.test_batches import RecordingRenderer


ERROR = "Batch historical materials failed integrity checks"


class BatchMaterialHistoryTests(unittest.TestCase):
    setUp = test_batch_search_history.BatchSearchHistoryTests.setUp
    create = test_batch_search_history.BatchSearchHistoryTests.create
    material_id = test_batch_search_history.BatchSearchHistoryTests.material_id

    def assert_rejected(self, batch_id: str) -> None:
        before = self.repository._connection.serialize()
        with self.assertRaisesRegex(BatchIntegrityError, f"^{ERROR}$"):
            self.service.validate_historical_materials(batch_id)
        self.assertEqual(self.repository._connection.serialize(), before)
        self.assertFalse(self.repository._connection.in_transaction)

    def workflow(self, batch_id: str) -> dict:
        return self.repository.get_workflow_run(self.repository.get_preparation_batch(batch_id)["workflow_run_id"])

    def workflow_patch(self, original: dict, replacement: dict):
        get = self.repository.get_workflow_run
        return patch.object(self.repository, "get_workflow_run",
            side_effect=lambda identifier: replacement if identifier == original["id"] else get(identifier))

    def rewrite_events(self, batch_id: str, change, index: int = 0) -> None:
        """Rehash only isolated fixture history, restoring its original trigger."""
        item = self.repository.list_preparation_items(batch_id)[index]
        events = self.repository.list_preparation_events(item["id"])
        connection = self.repository._connection
        trigger = connection.execute("SELECT sql FROM sqlite_schema WHERE name = 'preparation_events_no_update'").fetchone()[0]
        with self.repository.transaction():
            connection.execute("DROP TRIGGER preparation_events_no_update")
            previous = None
            for position, event in enumerate(events):
                state = json.loads(event["state_json"])
                change(position, event, state)
                event_hash = digest({"item_id": item["id"], "position": event["position"], "at": event["at"],
                    "state": state, "previous_sha256": previous})
                connection.execute("UPDATE preparation_batch_events SET at = ?, state_json = ?, previous_sha256 = ?, event_sha256 = ? WHERE id = ?",
                    (event["at"], canonical(state), previous, event_hash, event["id"]))
                previous = event_hash
            connection.execute(trigger)
        self.assertEqual(connection.execute("SELECT sql FROM sqlite_schema WHERE name = 'preparation_events_no_update'").fetchone()[0], trigger)

    def retire(self) -> None:
        lifecycle = ProfileLifecycleService(self.repository)
        preview = lifecycle.retire(self.claim_ids[0], actor_id="fictional-reviewer", idempotency_key="fictional-batch-retirement")
        lifecycle.retire(self.claim_ids[0], actor_id="fictional-reviewer", idempotency_key="fictional-batch-retirement",
            confirm=True, preview_token=preview.preview_token)

    def test_queued_and_interrupted_batches_need_no_profile_or_unreferenced_material_reads(self) -> None:
        batch_id, _ = self.create(1, run=False)
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted):
                if interrupted:
                    original = self.service._checkpoint
                    def crash_after_child(batch, item, state, owner, epoch):
                        if state["stage"] == "draft":
                            raise RuntimeError("fictional interrupted parent")
                        return original(batch, item, state, owner, epoch)
                    with patch.object(self.service, "_checkpoint", side_effect=crash_after_child), self.assertRaises(RuntimeError):
                        self.service.run(batch_id)
                    self.assertEqual(len(self.repository.list_material_ids()), 1)
                before = self.database.read_bytes()
                with patch.object(self.materials, "get", side_effect=AssertionError("No checkpoint material")), \
                     patch.object(ProfileService, "validated_profile", side_effect=AssertionError("No recorded material facts")), \
                     patch.object(self.renderer, "validate", side_effect=AssertionError("No recorded PDF")):
                    self.assertIsNone(self.service.validate_historical_materials(batch_id))
                self.assertEqual(self.database.read_bytes(), before)

    def test_partial_repeated_checkpoints_load_one_bundle_pdf_and_fact_snapshot_in_one_read_transaction(self) -> None:
        batch_id, _ = self.create(1, partial=True)
        self.service.run(batch_id)
        material_id = self.material_id(batch_id)
        item = self.repository.list_preparation_items(batch_id)[0]
        self.assertGreater(sum(json.loads(event["state_json"])["material_id"] == material_id
            for event in self.repository.list_preparation_events(item["id"])), 1)
        image = self.repository._connection.serialize()
        before = (self.database.read_bytes(), self.database.stat().st_mtime_ns,
                  sorted(path.name for path in self.database.parent.iterdir()))
        with SQLiteRepository.from_snapshot(image) as repository:
            renderer = RecordingRenderer()
            materials = MaterialService(repository, renderer)
            service = BatchService(repository, materials)
            trace = []
            repository._connection.set_trace_callback(trace.append)
            with patch.object(materials, "get", wraps=materials.get) as loaded, \
                 patch.object(materials, "_validate_historical_fact_snapshot", wraps=materials._validate_historical_fact_snapshot) as facts, \
                 patch.object(renderer, "validate", wraps=renderer.validate) as pdf, \
                 patch.object(renderer, "render", side_effect=AssertionError("No rendering")), \
                 patch.object(materials, "plan", side_effect=AssertionError("No current selection")), \
                 patch.object(materials, "is_approved", side_effect=AssertionError("No approval authority")), \
                 patch.object(materials, "validate_historical_facts", side_effect=AssertionError("No second bundle load")), \
                 patch.object(QuestionnaireService, "prepare", side_effect=AssertionError("No current answers")):
                self.assertIsNone(service.validate_historical_materials(batch_id))
            loaded.assert_called_once_with(material_id, require_current=False)
            self.assertEqual(facts.call_count, 1)
            self.assertEqual(pdf.call_count, 1)
            self.assertEqual([statement for statement in trace if statement in {"BEGIN", "ROLLBACK"}], ["BEGIN", "ROLLBACK"])
            self.assertEqual(repository._connection.serialize(), image)
        self.assertEqual((self.database.read_bytes(), self.database.stat().st_mtime_ns,
            sorted(path.name for path in self.database.parent.iterdir())), before)

    def test_unapproved_drafts_and_optional_need_info_are_valid_history(self) -> None:
        batch_id, _ = self.create(2, run=False)
        result = self.service.run(batch_id)
        self.assertEqual(result["counts"]["draft"], 2)
        with patch.object(self.renderer, "validate", wraps=self.renderer.validate) as pdf:
            self.assertIsNone(self.service.validate_historical_materials(batch_id))
        self.assertEqual(pdf.call_count, 2)
        self.assertTrue(all(self.repository.get_material_approval(item["material_id"]) is None for item in result["items"]))
        manifest = {"schema_version": 1, "claim_ids": list(self.claim_ids), "jobs": [{"job_id": self.job_id}],
            "questions": [{"id": "fictional-optional", "text": "Do you need sponsorship?", "claim_ids": [], "required": False}]}
        optional = self.service.create(manifest, idempotency_key="fictional-optional-batch")["batch_id"]
        self.assertEqual(self.service.run(optional)["counts"]["draft"], 1)
        self.assertIsNone(self.service.validate_historical_materials(optional))

    def test_exact_reuse_may_predate_a_newer_batch(self) -> None:
        first, _ = self.create(1)
        material_id = self.material_id(first)
        manifest = json.loads(self.repository.get_preparation_batch(first)["manifest_json"])
        second = self.service.create(manifest, idempotency_key="fictional-later-batch")["batch_id"]
        self.service.run(second)
        self.assertEqual(self.material_id(second), material_id)
        self.assertLess(datetime.fromisoformat(self.repository.get_material_version(material_id)["created_at"]),
            datetime.fromisoformat(self.repository.get_preparation_batch(second)["created_at"]))
        self.assertIsNone(self.service.validate_historical_materials(first))
        self.assertIsNone(self.service.validate_historical_materials(second))
        self.assertEqual(len(self.renderer.built), 1)

    def test_later_retirement_and_blocked_checkpoint_preserve_creation_facts(self) -> None:
        batch_id, _ = self.create(1)
        material_id = self.material_id(batch_id)
        self.retire()
        self.assertEqual(self.service.run(batch_id)["counts"]["blocked"], 1)
        before = self.database.read_bytes()
        with patch.object(self.materials, "get", wraps=self.materials.get) as loaded:
            self.assertIsNone(self.service.validate_historical_materials(batch_id))
        loaded.assert_called_once_with(material_id, require_current=False)
        self.assertEqual(self.database.read_bytes(), before)
        self.assertFalse(self.service.get(batch_id)["items"][0]["currently_valid"])

    def test_earlier_rehashed_draft_cannot_hide_required_need_info_behind_later_blocked_state(self) -> None:
        batch_id, _ = self.create(1, partial=True)
        self.assertIsNone(self.service.validate_historical_materials(batch_id))
        item = self.repository.list_preparation_items(batch_id)[0]
        events = self.repository.list_preparation_events(item["id"])
        blocked = json.loads(events[-1]["state_json"])
        last = len(events) - 1
        def conceal(position, event, state):
            if position == last:
                state.update(stage="draft", blockers=[])
        self.rewrite_events(batch_id, conceal)
        with self.repository.transaction():
            self.service._append_event(item["id"], blocked, at=events[-1]["at"])
        validate_profile_snapshot(self.repository._connection.serialize())
        self.assertEqual(self.service.get(batch_id)["counts"]["blocked"], 1)
        self.assertFalse(self.service.search_history(batch_id, current_job_ids=frozenset())[0].reusable)
        self.assert_rejected(batch_id)

    def test_material_checkpoint_must_not_precede_creation_and_exact_equality_is_valid(self) -> None:
        batch_id, _ = self.create(1)
        material = self.repository.get_material_version(self.material_id(batch_id))
        created = datetime.fromisoformat(material["created_at"])
        item = self.repository.list_preparation_items(batch_id)[0]
        events = self.repository.list_preparation_events(item["id"])
        self.assertLess(datetime.fromisoformat(events[-2]["at"]), created - timedelta(microseconds=1))
        last = len(events) - 1
        for offset in (0, -1):
            with self.subTest(microsecond_offset=offset):
                def change(position, event, state):
                    if position == last:
                        event["at"] = (created + timedelta(microseconds=offset)).isoformat(timespec="microseconds")
                self.rewrite_events(batch_id, change)
                if offset == 0:
                    self.assertIsNone(self.service.validate_historical_materials(batch_id))
                else:
                    self.assertEqual(self.service.get(batch_id)["counts"]["draft"], 1)
                    self.assert_rejected(batch_id)

    def test_boolean_and_float_creation_versions_do_not_alias_integer_payloads(self) -> None:
        batch_id, _ = self.create(1, run=False)
        original = self.workflow(batch_id)
        for field in ("version", "batch_schema_version"):
            for value in (True, 1.0):
                with self.subTest(field=field, value_type=type(value).__name__):
                    payload = json.loads(original["input_json"])
                    payload[field] = value
                    row = {**original, "input_json": canonical(payload)}
                    self.assertEqual(row["input_hash_sha256"], original["input_hash_sha256"])
                    with self.workflow_patch(original, row):
                        self.assertEqual(self.service.get(batch_id)["counts"]["queued"], 1)
                        self.assert_rejected(batch_id)

    def test_creation_workflow_metadata_and_raw_clock_bindings_are_closed(self) -> None:
        batch_id, _ = self.create(1, run=False)
        original = self.workflow(batch_id)
        created = datetime.fromisoformat(original["created_at"])
        variants = [(key, "fictional-unsupported") for key in ("model_name", "prompt_version", "failure_code", "failure_reason")]
        variants += [("outstanding_need_info_json", '[{"reason":"fictional"}]'), ("retry_policy_json", '{"attempts":1}'),
            ("id", "fictional-other-workflow"), ("started_at", None), ("started_at", "malformed"),
            ("started_at", created.replace(tzinfo=None).isoformat()),
            ("started_at", (created - timedelta(seconds=1)).isoformat()),
            ("finished_at", (created + timedelta(seconds=1)).isoformat()),
            ("created_at", (created - timedelta(seconds=1)).isoformat()),
            ("updated_at", "malformed"), ("updated_at", created.replace(tzinfo=None).isoformat()),
            ("updated_at", (created - timedelta(microseconds=1)).isoformat())]
        for key, value in variants:
            with self.subTest(field=key, value=value), self.workflow_patch(original, {**original, key: value}):
                self.assert_rejected(batch_id)
        future = (created + timedelta(days=365)).isoformat()
        with self.workflow_patch(original, {**original, "updated_at": future}):
            self.assertIsNone(self.service.validate_historical_materials(batch_id))

    def test_closed_fetched_rows_and_exact_integer_positions_are_required(self) -> None:
        batch_id, _ = self.create(1, run=False)
        batch = self.repository.get_preparation_batch(batch_id)
        items = self.repository.list_preparation_items(batch_id)
        events = self.repository.list_preparation_events(items[0]["id"])
        workflow = self.workflow(batch_id)
        lease = self.repository.get_preparation_lease(batch_id)
        sections = (("get_preparation_batch", batch, False), ("list_preparation_items", items[0], True),
                    ("list_preparation_events", events[0], True), ("get_workflow_run", workflow, False),
                    ("get_preparation_lease", lease, False))
        for method, original, sequence in sections:
            variants = [{**original, "unexpected": None}, {key: value for key, value in original.items() if key != next(iter(original))}]
            if "position" in original:
                variants += [{**original, "position": value} for value in (False, 0.0)]
            if "epoch" in original:
                variants += [{**original, "epoch": False}]
            for index, row in enumerate(variants):
                changed = (self.workflow_patch(original, row) if method == "get_workflow_run"
                    else patch.object(self.repository, method, return_value=[row] if sequence else row))
                with self.subTest(method=method, variant=index), changed:
                    self.assert_rejected(batch_id)

    def test_duplicate_nested_json_nonfinite_and_type_aliases_are_rejected(self) -> None:
        batch_id, _ = self.create(1, run=False)
        batch = self.repository.get_preparation_batch(batch_id)
        item = self.repository.list_preparation_items(batch_id)[0]
        event = self.repository.list_preparation_events(item["id"])[0]
        workflow = self.workflow(batch_id)
        variants = [
            ("get_workflow_run", {**workflow, "input_json": workflow["input_json"][:-1] + ',"version":1}'}, False),
            ("get_workflow_run", {**workflow, "retry_policy_json": '{"x":{"x":0,"x":0}}'}, False),
            ("get_preparation_batch", {**batch, "manifest_json": batch["manifest_json"].replace('"presentations":{}', '"presentations":{},"presentations":{}', 1)}, False),
            ("list_preparation_items", {**item, "spec_json": item["spec_json"].replace('"schema_version":1', '"schema_version":true', 1)}, True),
            ("list_preparation_events", {**event, "state_json": event["state_json"][:-1] + ',"attempts":0}'}, True),
            ("list_preparation_events", {**event, "state_json": event["state_json"].replace('"attempts":0', '"attempts":NaN')}, True),
            ("get_workflow_run", {**workflow, "input_json": "[" * 2000 + "]" * 2000}, False),
        ]
        for index, (method, row, sequence) in enumerate(variants):
            changed = (self.workflow_patch(workflow, row) if method == "get_workflow_run"
                else patch.object(self.repository, method, return_value=[row] if sequence else row))
            with self.subTest(variant=index), changed:
                self.assert_rejected(batch_id)

    def test_harmless_json_formatting_and_unicode_escapes_preserve_history(self) -> None:
        batch_id, _ = self.create(1, run=False)
        originals = {"get_preparation_batch": self.repository.get_preparation_batch(batch_id),
            "get_workflow_run": self.workflow(batch_id), "get_preparation_lease": self.repository.get_preparation_lease(batch_id)}
        item = self.repository.list_preparation_items(batch_id)[0]
        event = self.repository.list_preparation_events(item["id"])[0]
        originals.update(list_preparation_items=item, list_preparation_events=event)
        def formatted(row):
            result = dict(row)
            for key, value in row.items():
                if key.endswith("_json"):
                    result[key] = json.dumps(json.loads(value), indent=2, sort_keys=False).replace("schema", "\\u0073chema")
            return result
        with patch.object(self.repository, "get_preparation_batch", return_value=formatted(originals["get_preparation_batch"])), \
             self.workflow_patch(originals["get_workflow_run"], formatted(originals["get_workflow_run"])), \
             patch.object(self.repository, "list_preparation_items", return_value=[formatted(item)]), \
             patch.object(self.repository, "list_preparation_events", return_value=[formatted(event)]):
            before = self.database.read_bytes()
            self.assertIsNone(self.service.validate_historical_materials(batch_id))
            self.assertEqual(self.database.read_bytes(), before)

    def test_rehashed_other_job_material_binding_is_not_accepted(self) -> None:
        batch_id, _ = self.create(2)
        self.assertIsNone(self.service.validate_historical_materials(batch_id))
        second = self.repository.list_preparation_items(batch_id)[1]
        other = json.loads(self.repository.list_preparation_events(second["id"])[-1]["state_json"])
        def swap(position, event, state):
            for field in ("fingerprint", "child_key", "material_id", "bundle_sha256"):
                if state[field] is not None:
                    state[field] = other[field]
        self.rewrite_events(batch_id, swap)
        self.assert_rejected(batch_id)

    def test_optional_approval_records_are_audited_without_partial_eligibility(self) -> None:
        batch_id, _ = self.create(1, partial=True)
        material_id = self.material_id(batch_id)
        material = self.materials.get(material_id, require_current=False)
        payload = request_input("fictional-partial-record", {"material_id": material_id,
            "bundle_sha256": material["bundle_sha256"], "actor_id": "fictional-reviewer"})
        with self.repository.transaction():
            workflow = start_workflow(self.repository, "material_approval", payload, material["created_at"])
            self.repository.insert_material_approval(material_id=material_id, bundle_sha256=material["bundle_sha256"],
                actor_id="fictional-reviewer", approved_at=material["created_at"], workflow_run_id=workflow["id"])
            finish_workflow(self.repository, workflow["id"], [material_id], material["created_at"])
        self.assertIsNone(self.materials.validate_historical_approval_record(material_id))
        with patch.object(self.materials, "is_approved", side_effect=AssertionError("Record-only approval check")), \
             patch.object(self.materials, "validate_historical_approval_eligibility", side_effect=AssertionError("Partial history is valid")):
            self.assertIsNone(self.service.validate_historical_materials(batch_id))
        with self.repository.transaction():
            self.repository.update_workflow_run(workflow["id"], generated_artifacts=["fictional-wrong-material"])
        self.assert_rejected(batch_id)

    def test_bundle_and_creation_fact_failures_remain_fatal(self) -> None:
        batch_id, _ = self.create(1)
        material = self.repository.get_material_version(self.material_id(batch_id))
        for row in ({**material, "pdf_bytes": b"fictional-invalid-pdf"}, {**material, "id": "fictional-other-material"}):
            with self.subTest(field="pdf" if row["id"] == material["id"] else "id"), \
                 patch.object(self.repository, "get_material_version", return_value=row):
                self.assert_rejected(batch_id)
        with patch.object(self.materials, "_validate_historical_fact_snapshot", side_effect=ValueError("fictional private fact detail")):
            self.assert_rejected(batch_id)

    def test_borrowed_caller_work_survives_success_and_integrity_failure(self) -> None:
        batch_id, _ = self.create(1)
        before = self.repository._connection.serialize()
        class AbortFixture(Exception):
            pass
        with self.assertRaises(AbortFixture):
            with self.repository.transaction():
                marker = self.repository.add_workflow_run(workflow_type="fictional-caller-work", status="queued")
                self.assertIsNone(self.service.validate_historical_materials(batch_id))
                with patch.object(self.repository, "get_preparation_batch", side_effect=sqlite3.OperationalError("fictional storage detail")), \
                     self.assertRaisesRegex(BatchIntegrityError, f"^{ERROR}$"):
                    self.service.validate_historical_materials(batch_id)
                self.assertTrue(self.repository._connection.in_transaction)
                self.assertIsNotNone(self.repository.get_workflow_run(marker["id"]))
                raise AbortFixture
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_storage_failures_are_fixed_and_content_free(self) -> None:
        batch_id, _ = self.create(1)
        for method in ("get_preparation_batch", "list_preparation_items", "list_preparation_events",
                       "get_preparation_lease", "get_workflow_run", "get_material_version", "get_material_approval"):
            with self.subTest(method=method), patch.object(self.repository, method, side_effect=sqlite3.OperationalError("fictional private SQL detail")):
                self.assert_rejected(batch_id)
        for value in ("fictional-missing-batch", "invalid/batch", None):
            with self.subTest(input_type=type(value).__name__):
                self.assert_rejected(value)

    def test_interrupts_keep_identity_and_release_owned_read_transaction(self) -> None:
        batch_id, _ = self.create(1)
        for target, method in ((self.repository, "get_preparation_batch"),
                               (self.materials, "_validate_historical_fact_snapshot")):
            sentinel = KeyboardInterrupt("fictional interruption")
            before = self.repository._connection.serialize()
            with patch.object(target, method, side_effect=sentinel), self.assertRaises(KeyboardInterrupt) as raised:
                self.service.validate_historical_materials(batch_id)
            self.assertIs(raised.exception, sentinel)
            self.assertFalse(self.repository._connection.in_transaction)
            self.assertEqual(self.repository._connection.serialize(), before)


if __name__ == "__main__":
    unittest.main()
