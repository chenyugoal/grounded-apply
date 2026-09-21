from __future__ import annotations

import json
import unittest
from datetime import datetime
from unittest.mock import patch

from grounded_apply.repositories import RepositoryError, SQLiteRepository
from grounded_apply.services.applications import ApplicationService, _event_hash
from grounded_apply.services.materials import MaterialBlocked, MaterialService
from grounded_apply.services.profile_lifecycle import ProfileLifecycleService
from grounded_apply.services.workflow import canonical, digest, validate_workflow
from tests import test_applications
from tests.test_materials import SyntheticRenderer


ERROR = "Application history failed integrity checks"


class ApplicationChronologyTests(unittest.TestCase):
    setUp = test_applications.ApplicationTests.setUp
    move = test_applications.ApplicationTests.move

    def add_at(self, at: str) -> str:
        self.application_index = getattr(self, "application_index", 0) + 1
        with patch("grounded_apply.services.applications.timestamp", return_value=at):
            return self.service.add(self.job_id, actor_id="synthetic-user",
                idempotency_key=f"fictional-application-{self.application_index}")["application_id"]

    def move_at(self, application_id: str, state: str, at: str, **fields):
        with patch("grounded_apply.services.applications.timestamp", return_value=at):
            return self.move(application_id, state,
                idempotency_key=f"fictional-{application_id}-{state}", **fields)

    def approved_material(self, *, at: str = "2030-01-01T10:00:00+00:00") -> str:
        material = self.materials.build(self.job_id, self.claim_ids, idempotency_key="fictional-material")
        with patch("grounded_apply.services.materials.timestamp", return_value=at):
            self.materials.approve(material["material_id"], bundle_sha256=material["bundle_sha256"],
                actor_id="synthetic-user", idempotency_key="fictional-approval", confirm=True)
        return material["material_id"]

    def preparing(self) -> str:
        application_id = self.add_at("2030-01-01T07:00:00+00:00")
        self.move_at(application_id, "shortlisted", "2030-01-01T08:00:00+00:00")
        self.move_at(application_id, "preparing", "2030-01-01T09:00:00+00:00")
        return application_id

    def assert_bad_history(self, application_id: str) -> None:
        before = self.repository._connection.serialize()
        with self.assertRaisesRegex(RepositoryError, f"^{ERROR}$"):
            self.service.get(application_id)
        self.assertEqual(self.repository._connection.serialize(), before)
        self.assertFalse(self.repository._connection.in_transaction)

    def rewrite_times(self, application_id: str, times: list[str], *, bind_workflow: bool = True) -> None:
        """Rebind an isolated synthetic history completely, except its chronology."""
        events = self.service.get(application_id)["events"]
        self.assertEqual(len(events), len(times))
        connection = self.repository._connection
        names = ("applications_no_update", "events_no_update", "submissions_no_update")
        triggers = [connection.execute("SELECT sql FROM sqlite_schema WHERE name = ?", (name,)).fetchone()[0]
                    for name in names]
        with self.repository.transaction():
            for name in names:
                connection.execute(f"DROP TRIGGER {name}")
            previous = None
            for event, at in zip(events, times, strict=True):
                event["at"], event["previous_sha256"] = at, previous
                event["event_sha256"] = _event_hash(event)
                connection.execute("UPDATE application_events SET at = ?, previous_sha256 = ?, event_sha256 = ? WHERE id = ?",
                    (at, previous, event["event_sha256"], event["id"]))
                workflow = self.repository.get_workflow_run(event["workflow_run_id"])
                payload = json.loads(workflow["input_json"])
                if event["position"]:
                    payload["previous_sha256"] = previous
                connection.execute("UPDATE workflow_runs SET input_json = ?, input_hash_sha256 = ? WHERE id = ?",
                    (canonical(payload), digest(payload), workflow["id"]))
                if bind_workflow:
                    connection.execute("UPDATE workflow_runs SET created_at = ?, started_at = ?, finished_at = ?, updated_at = ? WHERE id = ?",
                        (at, at, at, at, workflow["id"]))
                validate_workflow(self.repository.get_workflow_run(workflow["id"]), workflow["workflow_type"], payload)
                previous = event["event_sha256"]
            connection.execute("UPDATE applications SET created_at = ? WHERE id = ?", (times[0], application_id))
            snapshot = self.repository.get_submission_snapshot(application_id)
            if snapshot is not None:
                submitted_at = next(event["at"] for event in events if event["state"] == "applied")
                content = json.loads(snapshot["snapshot_json"])
                content["recorded_at"] = submitted_at
                connection.execute("UPDATE submission_snapshots SET snapshot_json = ?, snapshot_sha256 = ?, submitted_at = ? WHERE application_id = ?",
                    (canonical(content), digest(content), submitted_at, application_id))
            for ddl in triggers:
                connection.execute(ddl)

    def test_valid_offset_order_is_accepted_without_normalizing_saved_times(self) -> None:
        application_id = self.add_at("2030-01-01T10:00:00+02:00")
        self.move_at(application_id, "shortlisted", "2030-01-01T09:00:00+00:00")
        before = self.repository._connection.serialize()
        history = self.service.get(application_id)
        self.assertEqual([event["at"] for event in history["events"]],
            ["2030-01-01T10:00:00+02:00", "2030-01-01T09:00:00+00:00"])
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_inverted_offset_order_rolls_back_confirmed_transition(self) -> None:
        application_id = self.add_at("2030-01-01T09:00:00+00:00")
        before = self.repository._connection.serialize()
        with self.assertRaisesRegex(RepositoryError, f"^{ERROR}$"):
            self.move_at(application_id, "shortlisted", "2030-01-01T10:00:00+02:00")
        self.assertEqual(self.repository._connection.serialize(), before)
        self.assertEqual(self.service.get(application_id)["state"], "discovered")

    def test_naive_or_malformed_first_event_rolls_back_application_and_workflow(self) -> None:
        for at in ("2030-01-01T08:00:00", "2030-01-01", "fictional-private-invalid-clock", ""):
            with self.subTest(at=at):
                before = self.repository._connection.serialize()
                with self.assertRaisesRegex(RepositoryError, f"^{ERROR}$"):
                    self.add_at(at)
                self.assertEqual(self.repository._connection.serialize(), before)

    def test_ready_before_saved_approval_rolls_back_confirmed_transition(self) -> None:
        application_id = self.preparing()
        material_id = self.approved_material()
        before = self.repository._connection.serialize()
        for at in ("2030-01-01T09:30:00+00:00", "2030-01-01T11:30:00+02:00"):
            with self.subTest(at=at), self.assertRaisesRegex(RepositoryError, f"^{ERROR}$"):
                self.move_at(application_id, "ready_for_review", at, material_id=material_id)
            self.assertEqual(self.repository._connection.serialize(), before)

    def test_equal_instants_with_different_offsets_preserve_both_raw_values(self) -> None:
        application_id = self.add_at("2030-01-01T10:00:00+02:00")
        self.move_at(application_id, "shortlisted", "2030-01-01T08:00:00Z")
        before = self.repository._connection.serialize()
        records = self.repository.list_application_events(application_id)
        result = self.service.get(application_id)
        self.assertEqual([event["at"] for event in result["events"]],
            ["2030-01-01T10:00:00+02:00", "2030-01-01T08:00:00Z"])
        self.assertEqual([event["event_sha256"] for event in result["events"]],
            [record["event_sha256"] for record in records])
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_rehashed_inverted_history_fails_even_when_all_workflow_bindings_match(self) -> None:
        application_id = self.add_at("2030-01-01T08:00:00+00:00")
        self.move_at(application_id, "shortlisted", "2030-01-01T09:00:00+00:00")
        self.rewrite_times(application_id, ["2030-01-01T09:00:00+00:00", "2030-01-01T10:00:00+02:00"])
        self.assert_bad_history(application_id)

    def test_rehashed_naive_or_malformed_first_and_later_events_fail_read_only(self) -> None:
        for position in (0, 1):
            for at in ("2030-01-01T08:00:00", "fictional-private-invalid-clock"):
                with self.subTest(position=position, at=at):
                    application_id = self.add_at("2030-01-01T07:00:00+00:00")
                    self.move_at(application_id, "shortlisted", "2030-01-01T08:00:00+00:00")
                    times = ["2030-01-01T07:00:00+00:00", "2030-01-01T08:00:00+00:00"]
                    times[position] = at
                    self.rewrite_times(application_id, times)
                    self.assert_bad_history(application_id)

    def test_invalid_later_clock_rolls_back_workflow_and_preserves_preview_replay(self) -> None:
        application_id = self.add_at("2030-01-01T07:00:00+00:00")
        options = {"actor_id": "synthetic-user", "idempotency_key": "fictional-retry-clock"}
        preview = self.service.transition(application_id, "shortlisted", **options)
        before = self.repository._connection.serialize()
        for at in ("2030-01-01T08:00:00", "fictional-private-invalid-clock"):
            with self.subTest(at=at), patch("grounded_apply.services.applications.timestamp", return_value=at):
                with self.assertRaisesRegex(RepositoryError, f"^{ERROR}$"):
                    self.service.transition(application_id, "shortlisted", **options,
                        confirm=True, preview_token=preview["preview_token"])
            self.assertEqual(self.repository._connection.serialize(), before)
        with patch("grounded_apply.services.applications.timestamp", return_value="2030-01-01T08:00:00+00:00"):
            self.service.transition(application_id, "shortlisted", **options, confirm=True, preview_token=preview["preview_token"])
        saved = self.repository._connection.serialize()
        self.assertTrue(self.service.transition(application_id, "shortlisted", **options,
            confirm=True, preview_token=preview["preview_token"])["replayed"])
        self.assertEqual(self.repository._connection.serialize(), saved)

    def test_submission_compares_approval_instants_and_keeps_exact_snapshot_identity(self) -> None:
        material_id = self.approved_material()
        for submitted in ("2030-01-01T09:00:00-02:00", "2030-01-01T12:00:00+02:00"):
            with self.subTest(submitted=submitted):
                application_id = self.preparing()
                self.move_at(application_id, "ready_for_review", "2030-01-01T11:00:00+01:00", material_id=material_id)
                self.move_at(application_id, "applied", submitted, material_id=material_id, confirm_submitted=True)
                snapshot = self.repository.get_submission_snapshot(application_id)
                raw_events = self.repository.list_application_events(application_id)
                before = self.repository._connection.serialize()
                history = self.service.get(application_id)
                content = json.loads(snapshot["snapshot_json"])
                self.assertEqual(content["recorded_at"], submitted)
                self.assertEqual(content["approval"]["approved_at"], "2030-01-01T10:00:00+00:00")
                self.assertEqual(snapshot["snapshot_sha256"], digest(content))
                self.assertEqual(history["submission_sha256"], snapshot["snapshot_sha256"])
                self.assertEqual(self.repository.list_application_events(application_id), raw_events)
                self.assertEqual(self.repository.get_submission_snapshot(application_id), snapshot)
                self.assertEqual(self.repository._connection.serialize(), before)

    def test_submission_builder_refuses_offset_before_approval_and_invalid_clock(self) -> None:
        material_id = self.approved_material()
        bundle = self.materials.get(material_id)["bundle_sha256"]
        before = self.repository._connection.serialize()
        for at in ("2030-01-01T10:30:00+02:00", "2030-01-01T11:00:00", "fictional-private-invalid-clock"):
            with self.subTest(at=at):
                event = {"at": at, "payload": {"material_id": material_id, "bundle_sha256": bundle,
                                               "human_confirmed_submission": True}}
                with self.assertRaises(ValueError) as raised:
                    self.service._submission(self.job_id, event, require_current=True)
                self.assertIn(str(raised.exception), ("Submission requires the exact approved material", "Application timestamp is invalid"))
                self.assertEqual(self.repository._connection.serialize(), before)

    def test_invalid_submission_clock_rolls_back_event_workflow_and_snapshot(self) -> None:
        application_id = self.preparing()
        material_id = self.approved_material()
        self.move_at(application_id, "ready_for_review", "2030-01-01T10:00:00+00:00", material_id=material_id)
        before = self.repository._connection.serialize()
        for at in ("2030-01-01T10:30:00+02:00", "2030-01-01T11:00:00", "fictional-private-invalid-clock"):
            with self.subTest(at=at), self.assertRaises(ValueError) as raised:
                self.move_at(application_id, "applied", at, material_id=material_id, confirm_submitted=True)
            self.assertIn(str(raised.exception), ("Submission requires the exact approved material", "Application timestamp is invalid"))
            self.assertEqual(self.repository._connection.serialize(), before)
            self.assertIsNone(self.repository.get_submission_snapshot(application_id))
            self.assertEqual(self.service.get(application_id)["state"], "ready_for_review")

    def test_rehashed_ready_before_approval_and_submission_history_fail(self) -> None:
        material_id = self.approved_material()
        for applied in (False, True):
            with self.subTest(applied=applied):
                application_id = self.preparing()
                self.move_at(application_id, "ready_for_review", "2030-01-01T10:00:00+00:00", material_id=material_id)
                times = ["2030-01-01T07:00:00+00:00", "2030-01-01T08:00:00+00:00",
                         "2030-01-01T09:00:00+00:00", "2030-01-01T11:30:00+02:00"]
                if applied:
                    self.move_at(application_id, "applied", "2030-01-01T11:00:00+00:00", material_id=material_id, confirm_submitted=True)
                    times.append("2030-01-01T11:45:00+02:00")
                self.rewrite_times(application_id, times)
                self.assert_bad_history(application_id)

    def test_timezone_equivalence_does_not_relax_exact_workflow_or_snapshot_bindings(self) -> None:
        application_id = self.add_at("2030-01-01T08:00:00+00:00")
        self.move_at(application_id, "shortlisted", "2030-01-01T09:00:00+00:00")
        self.rewrite_times(application_id, ["2030-01-01T08:00:00+00:00", "2030-01-01T11:00:00+02:00"], bind_workflow=False)
        self.assert_bad_history(application_id)
        material_id = self.approved_material()
        submitted_id = self.preparing()
        self.move_at(submitted_id, "ready_for_review", "2030-01-01T10:00:00+00:00", material_id=material_id)
        self.move_at(submitted_id, "applied", "2030-01-01T11:00:00+00:00", material_id=material_id, confirm_submitted=True)
        connection = self.repository._connection
        ddl = connection.execute("SELECT sql FROM sqlite_schema WHERE name = 'submissions_no_update'").fetchone()[0]
        snapshot = self.repository.get_submission_snapshot(submitted_id)
        content = json.loads(snapshot["snapshot_json"])
        content["recorded_at"] = "2030-01-01T13:00:00+02:00"
        with self.repository.transaction():
            connection.execute("DROP TRIGGER submissions_no_update")
            connection.execute("UPDATE submission_snapshots SET submitted_at = ?, snapshot_json = ?, snapshot_sha256 = ? WHERE application_id = ?",
                (content["recorded_at"], canonical(content), digest(content), submitted_id))
            connection.execute(ddl)
        self.assert_bad_history(submitted_id)

    def test_retirement_preserves_offset_submission_history_and_invalidates_current_readiness(self) -> None:
        material_id = self.approved_material()
        ready_id, submitted_id = self.preparing(), self.preparing()
        for application_id in (ready_id, submitted_id):
            self.move_at(application_id, "ready_for_review", "2030-01-01T11:00:00+01:00", material_id=material_id)
        self.move_at(submitted_id, "applied", "2030-01-01T09:00:00-02:00", material_id=material_id, confirm_submitted=True)
        submission = self.repository.get_submission_snapshot(submitted_id)
        self.assertTrue(self.service.get(ready_id)["currently_ready"])
        lifecycle = ProfileLifecycleService(self.repository)
        retired_at = datetime.fromisoformat("2031-01-01T00:00:00+00:00")
        preview = lifecycle.retire(self.claim_ids[0], actor_id="synthetic-user", idempotency_key="fictional-retirement", now=retired_at)
        lifecycle.retire(self.claim_ids[0], actor_id="synthetic-user", idempotency_key="fictional-retirement",
            confirm=True, preview_token=preview.preview_token, now=retired_at)
        before = self.repository._connection.serialize()
        with SQLiteRepository.from_snapshot(before) as repository:
            service = ApplicationService(repository, MaterialService(repository, SyntheticRenderer()))
            self.assertEqual(service.get(submitted_id)["state"], "applied")
            self.assertFalse(service.get(ready_id)["currently_ready"])
            self.assertEqual(repository.get_submission_snapshot(submitted_id), submission)
        with self.assertRaises(MaterialBlocked):
            self.service.transition(ready_id, "applied", actor_id="synthetic-user", idempotency_key="fictional-later-submit",
                material_id=material_id, confirm_submitted=True)
        self.assertEqual(self.repository._connection.serialize(), before)


if __name__ == "__main__":
    unittest.main()
