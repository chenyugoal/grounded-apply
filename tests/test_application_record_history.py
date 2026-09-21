from __future__ import annotations

import json
import sqlite3
import unittest
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from grounded_apply.repositories import RepositoryError, SQLiteRepository
from grounded_apply.services.applications import ApplicationService, _event_hash
from grounded_apply.services.materials import MaterialService
from grounded_apply.services.workflow import canonical, digest, hash_bytes
from tests import test_application_material_history
from tests.test_application_material_history import APPLIED_AT
from tests.test_materials import SyntheticRenderer


HISTORY_ERROR = "Application history failed integrity checks"
USE_ERROR = "Application historical material use failed integrity checks"


class ApplicationRecordHistoryTests(unittest.TestCase):
    setUp = test_application_material_history.ApplicationMaterialHistoryTests.setUp
    _build = test_application_material_history.ApplicationMaterialHistoryTests._build
    build = test_application_material_history.ApplicationMaterialHistoryTests.build
    approve = test_application_material_history.ApplicationMaterialHistoryTests.approve
    move = test_application_material_history.ApplicationMaterialHistoryTests.move
    preparing = test_application_material_history.ApplicationMaterialHistoryTests.preparing
    history = test_application_material_history.ApplicationMaterialHistoryTests.history
    retire = test_application_material_history.ApplicationMaterialHistoryTests.retire

    @contextmanager
    def workflow_row(self, identifier: str, row: dict):
        original = self.repository.get_workflow_run
        with patch.object(self.repository, "get_workflow_run", side_effect=lambda value: row if value == identifier else original(value)):
            yield

    def assert_rejected(self, application_id: str) -> None:
        before = self.repository._connection.serialize()
        for operation, message in ((self.applications.get, HISTORY_ERROR),
            (self.applications.validate_historical_material_use, USE_ERROR)):
            with self.subTest(operation=operation.__name__), self.assertRaisesRegex(RepositoryError, f"^{message}$"):
                operation(application_id)
            self.assertEqual(self.repository._connection.serialize(), before)
            self.assertFalse(self.repository._connection.in_transaction)

    def assert_valid(self, application_id: str) -> None:
        before = self.repository._connection.serialize()
        self.assertEqual(self.applications.get(application_id)["application_id"], application_id)
        self.assertIsNone(self.applications.validate_historical_material_use(application_id))
        self.assertEqual(self.repository._connection.serialize(), before)

    def submitted(self, *, transformation: str = "approved_text_selection@2") -> str:
        material_id = self.build(transformation=transformation)
        self.approve(material_id)
        return self.history(material_id)

    @contextmanager
    def event_row(self, application_id: str, *, fields: dict | None = None,
                  payload: dict | None = None, raw_payload: str | None = None):
        """Bind deliberate last-event changes so a hash failure cannot hide them."""
        events = deepcopy(self.repository.list_application_events(application_id))
        event = events[-1]
        event.update(fields or {})
        if payload is not None:
            event["payload_json"] = canonical(payload)
        if raw_payload is not None:
            event["payload_json"] = raw_payload
        value = {key: item for key, item in event.items() if key != "payload_json"}
        value["payload"] = json.loads(event["payload_json"])
        event["event_sha256"] = _event_hash(value)
        workflow = self.repository.get_workflow_run(event["workflow_run_id"])
        workflow_input = json.loads(workflow["input_json"])
        if event["position"]:
            workflow_input.update(value["payload"])
        workflow = {**workflow, "input_json": canonical(workflow_input), "input_hash_sha256": digest(workflow_input)}
        with patch.object(self.repository, "list_application_events", return_value=events), self.workflow_row(workflow["id"], workflow):
            yield

    def orphan_replay_workflow(self, event: dict) -> dict:
        """Keep the linked workflow valid while cloning its old request key."""
        original = self.repository.get_workflow_run(event["workflow_run_id"])
        relocated = json.loads(original["input_json"])
        relocated["idempotency_sha256"] = hash_bytes(f"fictional-relocated-{event['id']}".encode())
        clone = {**original, "id": f"fictional-orphan-{event['position']}"}
        with self.repository.transaction():
            self.repository._connection.execute("UPDATE workflow_runs SET idempotency_key = ?, input_json = ?, input_hash_sha256 = ? WHERE id = ?",
                (relocated["idempotency_sha256"], canonical(relocated), digest(relocated), original["id"]))
            columns = tuple(clone)
            self.repository._connection.execute(
                f"INSERT INTO workflow_runs ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", tuple(clone.values()))
        self.assert_valid(event["application_id"])
        return clone

    def test_workflow_version_boolean_and_float_aliases_fail_with_original_integer_hash(self) -> None:
        application_id = self.preparing()
        events = self.repository.list_application_events(application_id)
        for event in (events[0], events[-1]):
            original = self.repository.get_workflow_run(event["workflow_run_id"])
            for version in (True, 1.0):
                with self.subTest(position=event["position"], version=version):
                    payload = json.loads(original["input_json"])
                    payload["version"] = version
                    changed = {**original, "input_json": json.dumps(payload)}
                    with self.workflow_row(original["id"], changed):
                        self.assert_rejected(application_id)

    def test_completed_workflow_rejects_unsupported_metadata(self) -> None:
        application_id = self.preparing()
        event = self.repository.list_application_events(application_id)[-1]
        original = self.repository.get_workflow_run(event["workflow_run_id"])
        for field, value in (("model_name", "fictional-model"), ("prompt_version", "fictional-prompt"),
            ("failure_code", "fictional-failure"), ("failure_reason", "fictional-private-detail"),
            ("outstanding_need_info_json", '["fictional-pending"]'), ("retry_policy_json", '{"attempts":1}')):
            with self.subTest(field=field), self.workflow_row(original["id"], {**original, field: value}):
                self.assert_rejected(application_id)

    def test_workflow_json_duplicates_invalid_constants_and_deep_values_are_fixed_failures(self) -> None:
        application_id = self.preparing()
        event = self.repository.list_application_events(application_id)[-1]
        original = self.repository.get_workflow_run(event["workflow_run_id"])
        alterations = (
            ("input_json", '{"version":1,' + original["input_json"][1:]),
            ("retry_policy_json", '{"fictional":{"duplicate":1,"duplicate":1}}'),
            ("input_json", original["input_json"].replace('"version":1', '"version":NaN')),
            ("input_json", "[" * 1100 + "]" * 1100),
        )
        for field, value in alterations:
            with self.subTest(field=field, length=len(value)), self.workflow_row(original["id"], {**original, field: value}):
                self.assert_rejected(application_id)

    def test_workflow_clocks_require_exact_event_bindings_and_aware_nondecreasing_update(self) -> None:
        application_id = self.preparing()
        for event in (self.repository.list_application_events(application_id)[0], self.repository.list_application_events(application_id)[-1]):
            original = self.repository.get_workflow_run(event["workflow_run_id"])
            at = datetime.fromisoformat(event["at"])
            equivalent = at.astimezone(timezone(timedelta(hours=2))).isoformat()
            for field in ("created_at", "started_at", "finished_at", "updated_at"):
                values = (None, "fictional-private-clock", at.replace(tzinfo=None).isoformat(), (at - timedelta(seconds=1)).isoformat())
                if field != "updated_at":
                    values += (equivalent,)
                for value in values:
                    with self.subTest(position=event["position"], field=field, value=value), self.workflow_row(original["id"], {**original, field: value}):
                        self.assert_rejected(application_id)

    def test_equivalent_offset_and_future_workflow_updates_are_valid_without_rewriting(self) -> None:
        application_id = self.preparing()
        event = self.repository.list_application_events(application_id)[-1]
        original = self.repository.get_workflow_run(event["workflow_run_id"])
        at = datetime.fromisoformat(event["at"])
        for updated in (at.astimezone(timezone(timedelta(hours=-8))).isoformat(),
                        (at + timedelta(hours=1)).astimezone(timezone(timedelta(hours=-8))).isoformat(),
                        "2099-01-01T00:00:00+00:00"):
            with self.subTest(updated=updated), self.workflow_row(original["id"], {**original, "updated_at": updated}):
                self.assert_valid(application_id)

    def test_application_and_workflow_rows_are_closed_and_bind_fetched_identities(self) -> None:
        application_id = self.preparing()
        application = self.repository.get_application(application_id)
        changes = ({**application, "fictional_extra": None},
                   {key: value for key, value in application.items() if key != "id"},
                   {**application, "id": "fictional-foreign-application"})
        for index, changed in enumerate(changes):
            with self.subTest(application=index), patch.object(self.repository, "get_application", return_value=changed):
                self.assert_rejected(application_id)
        event = self.repository.list_application_events(application_id)[-1]
        original = self.repository.get_workflow_run(event["workflow_run_id"])
        changes = ({**original, "fictional_extra": None},
                   {key: value for key, value in original.items() if key != "started_at"},
                   {**original, "id": "fictional-foreign-workflow"})
        for index, changed in enumerate(changes):
            with self.subTest(workflow=index), self.workflow_row(original["id"], changed):
                self.assert_rejected(application_id)

    def test_event_rows_reject_numeric_positions_extra_fields_and_missing_fields(self) -> None:
        application_id = self.preparing()
        for fields in ({"position": 2.0}, {"fictional_extra": None}):
            with self.subTest(fields=fields), self.event_row(application_id, fields=fields):
                self.assert_rejected(application_id)
        events = self.repository.list_application_events(application_id)
        for field in ("actor_id", "position"):
            changed = deepcopy(events)
            del changed[-1][field]
            with self.subTest(missing=field), patch.object(self.repository, "list_application_events", return_value=changed):
                self.assert_rejected(application_id)
        with patch("grounded_apply.services.applications.timestamp", return_value=events[0]["at"]):
            other = self.applications.add(self.job_id, actor_id="fictional-user", idempotency_key="fictional-position")["application_id"]
        self.move(other, "shortlisted")
        with self.event_row(other, fields={"position": True}):
            self.assert_rejected(other)

    def test_event_payload_false_alias_and_duplicate_keys_fail_even_with_rebound_hashes(self) -> None:
        application_id = self.preparing()
        event = self.repository.list_application_events(application_id)[-1]
        payload = json.loads(event["payload_json"])
        with self.event_row(application_id, payload={**payload, "human_confirmed_submission": 0}):
            self.assert_rejected(application_id)
        duplicate = '{"human_confirmed_submission":false,' + event["payload_json"][1:]
        with self.event_row(application_id, raw_payload=duplicate):
            self.assert_rejected(application_id)

    def test_submission_numeric_boolean_aliases_fail_with_original_expected_snapshot_hash(self) -> None:
        application_id = self.submitted()
        original = self.repository.get_submission_snapshot(application_id)
        content = json.loads(original["snapshot_json"])
        for field, value in (("schema_version", True), ("schema_version", 1.0),
                             ("human_confirmed_submission", 1), ("external_action_taken", 0)):
            with self.subTest(field=field, value=value):
                changed = {**original, "snapshot_json": json.dumps({**content, field: value})}
                with patch.object(self.repository, "get_submission_snapshot", return_value=changed):
                    self.assert_rejected(application_id)
        nested = deepcopy(content)
        nested["structure"]["schema_version"] = True
        with patch.object(self.repository, "get_submission_snapshot", return_value={**original, "snapshot_json": json.dumps(nested)}):
            self.assert_rejected(application_id)

    def test_submission_rejects_recursive_duplicate_keys_nonfinite_and_unknown_fields(self) -> None:
        application_id = self.submitted()
        original = self.repository.get_submission_snapshot(application_id)
        raw = original["snapshot_json"]
        job = json.loads(raw)["job"]
        changes = ('{"schema_version":1,' + raw[1:],
            raw.replace('"job":{', '"job":{"id":' + json.dumps(job["id"]) + ',', 1),
            raw.replace('"schema_version":1', '"schema_version":Infinity', 1),
            '{"fictional_extra":null,' + raw[1:])
        for index, changed in enumerate(changes):
            with self.subTest(encoding=index), patch.object(self.repository, "get_submission_snapshot", return_value={**original, "snapshot_json": changed}):
                self.assert_rejected(application_id)

    def test_submission_row_requires_exact_closed_identity_time_and_digest(self) -> None:
        application_id = self.submitted()
        original = self.repository.get_submission_snapshot(application_id)
        changes = [{**original, field: value} for field, value in (
            ("application_id", "fictional-foreign-application"), ("event_id", "fictional-foreign-event"),
            ("material_id", "fictional-foreign-material"), ("snapshot_sha256", "0" * 64),
            ("submitted_at", APPLIED_AT.astimezone(timezone(timedelta(hours=2))).isoformat()),
            ("fictional_extra", None))]
        changes.append({key: value for key, value in original.items() if key != "application_id"})
        for index, changed in enumerate(changes):
            with self.subTest(row=index), patch.object(self.repository, "get_submission_snapshot", return_value=changed):
                self.assert_rejected(application_id)

    def test_harmless_json_encoding_and_later_retirement_preserve_v1_v2_snapshot_bytes(self) -> None:
        applications = [self.submitted(transformation=version) for version in ("approved_text_selection@1", "approved_text_selection@2")]
        self.retire(self.claim_ids[0], at=APPLIED_AT + timedelta(seconds=1))

        def reencode(raw: str) -> str:
            value = json.loads(raw)
            if isinstance(value, dict):
                value = dict(reversed(tuple(value.items())))
            return json.dumps(value, indent=2, ensure_ascii=True).replace("fictional", "\\u0066ictional").replace("schema_version", "\\u0073chema_version")

        original_workflow = self.repository.get_workflow_run
        original_events = self.repository.list_application_events
        original_snapshot = self.repository.get_submission_snapshot

        def workflows(identifier: str):
            row = original_workflow(identifier)
            if row is None or row["workflow_type"] not in {"application_create", "application_transition"}:
                return row
            return {key: reencode(value) if key.endswith("_json") else value for key, value in row.items()}

        with (patch.object(self.repository, "get_workflow_run", side_effect=workflows),
              patch.object(self.repository, "list_application_events", side_effect=lambda identifier: [
                  {**event, "payload_json": reencode(event["payload_json"])} for event in original_events(identifier)]),
              patch.object(self.repository, "get_submission_snapshot", side_effect=lambda identifier: {
                  **original_snapshot(identifier), "snapshot_json": reencode(original_snapshot(identifier)["snapshot_json"])})):
            for application_id in applications:
                self.assert_valid(application_id)
        image = self.repository._connection.serialize()
        with SQLiteRepository.from_snapshot(image) as repository:
            service = ApplicationService(repository, MaterialService(repository, SyntheticRenderer()))
            for application_id in applications:
                self.assertEqual(service.get(application_id)["state"], "applied")
                self.assertIsNone(service.validate_historical_material_use(application_id))
        self.assertEqual(self.repository._connection.serialize(), image)

    def test_valid_create_and_transition_replay_survive_later_transitions_without_writes(self) -> None:
        application_id = self.preparing()
        event = self.repository.list_application_events(application_id)[1]
        workflow = self.repository.get_workflow_run(event["workflow_run_id"])
        before = self.repository._connection.serialize()
        created = self.applications.add(self.job_id, actor_id="fictional-user", idempotency_key="fictional-application-1")
        transitioned = self.applications.transition(application_id, "shortlisted", actor_id="fictional-user",
            idempotency_key="fictional-transition-1", confirm=True, preview_token=workflow["input_hash_sha256"])
        self.assertTrue(created["replayed"] and transitioned["replayed"])
        self.assertEqual(created["state"], "preparing")
        self.assertEqual(transitioned["state"], "preparing")
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_valid_transition_key_from_another_application_remains_a_caller_conflict(self) -> None:
        first = self.preparing()
        second = self.preparing()
        event = self.repository.list_application_events(first)[1]
        workflow = self.repository.get_workflow_run(event["workflow_run_id"])
        before = self.repository._connection.serialize()
        with self.assertRaisesRegex(ValueError, "^Transition key already used; replay the exact confirmed request$"):
            self.applications.transition(second, "shortlisted", actor_id="fictional-user",
                idempotency_key="fictional-transition-1", confirm=True, preview_token=workflow["input_hash_sha256"])
        self.assertEqual(self.repository._connection.serialize(), before)
        self.assertEqual(self.applications.get(first)["state"], "preparing")
        self.assertEqual(self.applications.get(second)["state"], "preparing")

    def test_orphan_workflow_cannot_replay_a_real_application_creation(self) -> None:
        application_id = self.preparing()
        self.orphan_replay_workflow(self.repository.list_application_events(application_id)[0])
        before = self.repository._connection.serialize()
        with self.assertRaisesRegex(RepositoryError, "^Application replay failed integrity checks$"):
            self.applications.add(self.job_id, actor_id="fictional-user", idempotency_key="fictional-application-1")
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_orphan_workflow_cannot_replay_a_real_transition_after_later_transitions(self) -> None:
        application_id = self.preparing()
        clone = self.orphan_replay_workflow(self.repository.list_application_events(application_id)[1])
        before = self.repository._connection.serialize()
        with self.assertRaisesRegex(RepositoryError, "^Transition replay result is invalid$"):
            self.applications.transition(application_id, "shortlisted", actor_id="fictional-user",
                idempotency_key="fictional-transition-1", confirm=True, preview_token=clone["input_hash_sha256"])
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_malformed_transition_replay_lookup_fails_before_request_comparison(self) -> None:
        application_id = self.preparing()
        event = self.repository.list_application_events(application_id)[1]
        workflow = self.repository.get_workflow_run(event["workflow_run_id"])
        before = self.repository._connection.serialize()
        for field, value in (("input_json", "fictional-private-invalid-json"),
                             ("input_json", None), ("generated_artifacts_json", "{}"),
                             ("generated_artifacts_json", '["fictional-foreign-application","fictional-event"]')):
            with self.subTest(field=field, value=value), patch.object(self.repository, "get_workflow_run_by_idempotency_key",
                return_value={**workflow, field: value}):
                with self.assertRaisesRegex(RepositoryError, "^Transition replay result is invalid$"):
                    self.applications.transition(application_id, "shortlisted", actor_id="fictional-user",
                        idempotency_key="fictional-transition-1", confirm=True, preview_token=workflow["input_hash_sha256"])
            self.assertEqual(self.repository._connection.serialize(), before)

    def test_invalid_linked_workflow_blocks_replay_and_post_write_corruption_rolls_back(self) -> None:
        application_id = self.preparing()
        event = self.repository.list_application_events(application_id)[1]
        workflow = self.repository.get_workflow_run(event["workflow_run_id"])
        with self.workflow_row(workflow["id"], {**workflow, "model_name": "fictional-model"}):
            before = self.repository._connection.serialize()
            with self.assertRaisesRegex(RepositoryError, f"^{HISTORY_ERROR}$"):
                self.applications.transition(application_id, "shortlisted", actor_id="fictional-user",
                    idempotency_key="fictional-transition-1", confirm=True, preview_token=workflow["input_hash_sha256"])
            self.assertEqual(self.repository._connection.serialize(), before)
        options = {"actor_id": "fictional-user", "idempotency_key": "fictional-corrupt-transition"}
        preview = self.applications.transition(application_id, "archived", **options)
        before = self.repository._connection.serialize()
        original = self.repository.update_workflow_run

        def corrupt_after_finish(identifier: str, **fields):
            result = original(identifier, **fields)
            if fields.get("status") == "succeeded":
                self.repository._connection.execute("UPDATE workflow_runs SET model_name = 'fictional-model' WHERE id = ?", (identifier,))
            return result

        with patch.object(self.repository, "update_workflow_run", side_effect=corrupt_after_finish):
            with self.assertRaisesRegex(RepositoryError, f"^{HISTORY_ERROR}$"):
                self.applications.transition(application_id, "archived", **options, confirm=True, preview_token=preview["preview_token"])
        self.assertEqual(self.repository._connection.serialize(), before)
        self.assertFalse(self.repository._connection.in_transaction)

    def test_storage_errors_are_fixed_and_interrupts_are_preserved_without_writes(self) -> None:
        application_id = self.preparing()
        with patch.object(self.repository, "get_workflow_run", side_effect=sqlite3.OperationalError("fictional-private-storage-detail")):
            self.assert_rejected(application_id)
        before = self.repository._connection.serialize()
        for operation in (self.applications.get, self.applications.validate_historical_material_use):
            interrupt = KeyboardInterrupt("fictional cancellation")
            with patch.object(self.repository, "get_workflow_run", side_effect=interrupt):
                with self.assertRaises(KeyboardInterrupt) as raised:
                    operation(application_id)
            self.assertIs(raised.exception, interrupt)
            self.assertFalse(self.repository._connection.in_transaction)
            self.assertEqual(self.repository._connection.serialize(), before)


if __name__ == "__main__":
    unittest.main()
