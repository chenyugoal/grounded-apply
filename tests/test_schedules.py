from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

from grounded_apply.repositories import SQLiteRepository
from grounded_apply.services.schedules import (
    ScheduleCapacityError, ScheduleExecutionError, ScheduleIntegrityError,
    ScheduleLeaseActiveError, ScheduleService, _event,
)
from grounded_apply.services.searches import SearchService
from grounded_apply.services.workflow import canonical, digest
from tests.test_batches import RecordingRenderer
from tests.test_discovery import GREENHOUSE, encoded, greenhouse_job
from tests.test_materials import approved_fixture
from tests.test_searches import SearchTransport


class ScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="gapply-synthetic-schedules-")
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "fictional.db"
        with SQLiteRepository(self.database).initialize() as repository:
            _, self.claim_ids = approved_fixture(repository)
        self.active_repositories = 0
        self.now = datetime(2026, 10, 20, 12, tzinfo=UTC)
        self.renderer = RecordingRenderer()
        self.transport = SearchTransport(self)
        self.searches = SearchService(self.factory, self.transport, self.renderer, clock=lambda: self.now)
        self.service = ScheduleService(self.factory, self.transport, self.renderer, clock=lambda: self.now)
        self.search_id = self.searches.configure({"schema_version": 1,
            "sources": [{"id": "fictional-gh", "provider": "greenhouse", "board": "example"}],
            "claim_ids": list(self.claim_ids), "max_jobs": 2}, idempotency_key="fictional-scope")["search_id"]

    @contextmanager
    def factory(self, read_only: bool):
        with SQLiteRepository(self.database, existing_only=True, read_only=read_only).initialize() as repository:
            self.active_repositories += 1
            try:
                yield repository
            finally:
                self.active_repositories -= 1

    def spec(self, **changes):
        return {"schema_version": 1, "search_id": self.search_id, "timezone": "UTC", "local_time": "09:00",
            "start_date": "2026-10-20", **changes}

    def configure(self, **changes):
        return self.service.configure(self.spec(**changes), idempotency_key="fictional-daily")["schedule_id"]

    def materials(self):
        with self.factory(True) as repository:
            return repository.list_material_ids()

    def test_preview_is_read_only_and_new_past_start_rejected_but_exact_replay_survives(self):
        with patch.object(self.service, "_open", side_effect=AssertionError("no runtime")):
            preview = self.service.configure(self.spec(), idempotency_key="preview", dry_run=True)
        self.assertFalse(preview["search_validated"])
        identifier = self.configure()
        self.now += timedelta(days=1)
        self.assertEqual(self.configure(), identifier)
        with self.assertRaises(ValueError):
            self.service.configure(self.spec(), idempotency_key="new-past")
        before = self.database.read_bytes()
        self.assertFalse(self.service.tick(identifier, dry_run=True)["executed"])
        self.assertEqual(before, self.database.read_bytes())
        self.assertEqual(self.transport.calls, [])

    def test_empty_day_is_durable_quiet_and_replayed_without_network(self):
        self.transport.responses[GREENHOUSE] = encoded({"jobs": []})
        identifier = self.configure()
        first = self.service.tick(identifier)
        self.assertTrue(first["executed"])
        self.assertEqual(first["status"], "completed")
        self.assertFalse(first["notify"])
        again = self.service.tick(identifier)
        self.assertEqual(again["run_id"], first["run_id"])
        self.assertFalse(again["executed"])
        self.assertEqual(len(self.transport.calls), 1)
        self.assertFalse(again["scheduler_installed"])

    def test_packages_notice_survives_output_loss_ack_and_old_notice_keeps_own_child(self):
        identifier = self.configure()
        first = self.service.tick(identifier)
        notice = first["notification"]
        self.assertEqual(notice["run_id"], first["run_id"])
        self.assertEqual(self.service.tick(identifier)["notification"], notice)
        self.now += timedelta(days=1)
        changed = greenhouse_job(1)
        changed["content"] += "<p>Additional fictional role context.</p>"
        self.transport.responses[GREENHOUSE] = encoded({"jobs": [changed]})
        second = self.service.tick(identifier)
        self.assertNotEqual(second["run_id"], first["run_id"])
        self.assertEqual(second["notification"]["run_id"], first["run_id"])
        self.assertEqual(second["notifications_pending"], 2)
        acknowledged = self.service.ack(identifier, notice["id"], idempotency_key="fictional-ack")
        self.assertEqual(acknowledged["notification"]["run_id"], second["run_id"])
        self.assertTrue(self.service.ack(identifier, notice["id"], idempotency_key="fictional-ack")["replayed"])
        with self.assertRaises(ValueError):
            self.service.ack(identifier, notice["id"], idempotency_key="different-ack")

    def test_unfinished_previous_day_resumes_before_latest_day_without_duplicate_material(self):
        identifier = self.configure(max_items=1)
        first = self.service.tick(identifier)
        self.assertEqual(first["status"], "budget_exhausted")
        self.now += timedelta(days=3)
        resumed = self.service.tick(identifier)
        self.assertEqual(resumed["run_id"], first["run_id"])
        self.assertEqual(resumed["attempts"], 2)
        self.assertEqual(len(self.transport.calls), 1)
        self.assertEqual(len(self.materials()), 2)
        latest = self.service.tick(identifier)
        self.assertNotEqual(latest["run_id"], first["run_id"])
        self.assertEqual(latest["current_occurrence"]["local_date"], "2026-10-23")
        self.assertEqual(latest["missed_dates"], 2)

    def test_same_clock_notifications_follow_committed_event_order(self):
        identifier = self.configure(max_items=1)
        first = self.service.tick(identifier)
        second = self.service.tick(identifier)
        self.assertEqual(second["notifications_pending"], 2)
        self.assertEqual(first["notification"]["id"], second["notification"]["id"])

    def test_attempt_exhaustion_is_durable_and_later_day_can_progress(self):
        identifier = self.configure(max_items=1, max_attempts=1)
        first = self.service.tick(identifier)
        self.assertEqual(first["child"]["remaining_count"], 1)
        exhausted = self.service.tick(identifier)
        self.assertFalse(exhausted["executed"])
        self.assertEqual(exhausted["current_occurrence"]["status"], "retry_limit")
        self.now += timedelta(days=1)
        next_day = self.service.tick(identifier)
        self.assertNotEqual(next_day["run_id"], first["run_id"])
        self.assertEqual(next_day["child"]["counts"]["draft"], 1)
        self.assertEqual(len(self.materials()), 2)

    def test_no_progress_attempt_limit_notifies_once_after_prior_budget_was_acknowledged(self):
        identifier = self.configure(max_items=1, max_attempts=3)
        first = self.service.tick(identifier)
        self.service.ack(identifier, first["notification"]["id"], idempotency_key="first-budget-ack")
        # A bounded invocation may expire before it can make more child progress.
        # Return the actual fresh child review to model that outcome without
        # changing its already validated durable checkpoints.
        def no_progress(search, run_id, **kwargs):
            return search.get(run_id)
        with patch.object(SearchService, "resume", autospec=True, side_effect=no_progress):
            second = self.service.tick(identifier)
            self.assertFalse(second["notify"])
            exhausted = self.service.tick(identifier)
        self.assertEqual(exhausted["attempts"], 3)
        self.assertTrue(exhausted["notify"])
        self.assertEqual(exhausted["notification"]["delta"]["run_health_change"]["current"]["status"], "retry_limit")
        self.assertEqual(exhausted["notification"]["delta"]["new_materials"], [])
        notice_id = exhausted["notification"]["id"]
        self.service.ack(identifier, notice_id, idempotency_key="attempt-limit-ack")
        calls, materials = len(self.transport.calls), self.materials()
        quiet = self.service.tick(identifier)
        self.assertFalse(quiet["notify"])
        self.assertFalse(quiet["executed"])
        self.assertEqual(len(self.transport.calls), calls)
        self.assertEqual(self.materials(), materials)

    def test_real_transport_interrupt_preserved_and_same_child_recovered(self):
        identifier = self.configure()
        self.transport.on_get = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.service.tick(identifier)
        interrupted = self.service.get(identifier)
        self.assertEqual(interrupted["current_occurrence"]["status"], "running")
        self.assertEqual(interrupted["attempts"], 1)
        self.transport.on_get = None
        resumed = self.service.tick(identifier)
        self.assertEqual(resumed["run_id"], interrupted["run_id"])
        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(len(self.materials()), 2)

    def test_scheduled_child_cannot_be_resumed_directly_before_network_or_preparation(self):
        identifier = self.configure(max_items=1)
        first = self.service.tick(identifier)
        calls, materials = len(self.transport.calls), self.materials()
        with self.assertRaises(ValueError):
            self.searches.resume(first["run_id"])
        self.assertEqual(len(self.transport.calls), calls)
        self.assertEqual(self.materials(), materials)
        other = self.service.configure(self.spec(), idempotency_key="interrupt-daily")["schedule_id"]
        self.transport.on_get = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.service.tick(other)
        run_id = self.service.get(other)["run_id"]
        calls = len(self.transport.calls)
        with self.assertRaises(ValueError):
            self.searches.resume(run_id)
        self.assertEqual(len(self.transport.calls), calls)

    def test_child_committed_before_parent_checkpoint_recovers_after_owner_expiry(self):
        identifier = self.configure()
        original = self.service._append_occurrence
        def interrupted(repository, occurrence_id, manifest, action, state, **kwargs):
            if action == "finished":
                raise KeyboardInterrupt()
            return original(repository, occurrence_id, manifest, action, state, **kwargs)
        with patch.object(self.service, "_append_occurrence", side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
            self.service.tick(identifier)
        self.assertEqual(len(self.materials()), 2)
        calls = len(self.transport.calls)
        with self.assertRaises(ScheduleLeaseActiveError):
            self.service.tick(identifier)
        self.now += timedelta(seconds=1141)
        result = self.service.tick(identifier)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(self.transport.calls), calls)
        self.assertEqual(len(self.materials()), 2)
        self.assertTrue(result["notify"])

    def test_final_attempt_committed_child_reconciles_without_new_attempt_or_dispatch(self):
        identifier = self.configure(max_attempts=1)
        with patch.object(self.service, "_notice", side_effect=KeyboardInterrupt()), self.assertRaises(KeyboardInterrupt):
            self.service.tick(identifier)
        self.assertEqual(len(self.materials()), 2)
        self.now += timedelta(seconds=1141)
        with patch.object(self.transport, "get", side_effect=AssertionError("no retry GET")), patch.object(self.renderer, "render", side_effect=AssertionError("no retry render")):
            result = self.service.tick(identifier)
        self.assertFalse(result["executed"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["attempts"], 1)
        self.assertTrue(result["notify"])

    def test_final_attempt_batch_commit_before_search_checkpoint_is_reconciled(self):
        identifier = self.configure(max_attempts=1)
        original = SearchService._change
        def interrupt(service, run_id, owner, epoch, action, update, **kwargs):
            if action == "child_progress":
                raise KeyboardInterrupt()
            return original(service, run_id, owner, epoch, action, update, **kwargs)
        with patch.object(SearchService, "_change", new=interrupt), self.assertRaises(KeyboardInterrupt):
            self.service.tick(identifier)
        self.assertEqual(len(self.materials()), 2)
        result = self.service.tick(identifier)
        self.assertFalse(result["executed"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["child"]["phase"], "complete")
        self.assertEqual(result["attempts"], 1)
        self.assertTrue(result["notify"])

    def test_empty_request_budget_child_reconciliation_preserves_coverage_gap(self):
        self.search_id = self.searches.configure({"schema_version": 1,
            "sources": [{"id": "fictional-gh", "provider": "greenhouse", "board": "example"},
                {"id": "fictional-other", "provider": "ashby", "board": "example"}],
            "claim_ids": list(self.claim_ids), "max_requests": 1}, idempotency_key="budget-scope")["search_id"]
        self.transport.responses[GREENHOUSE] = encoded({"jobs": []})
        identifier = self.configure(max_attempts=1)
        with patch.object(self.service, "_notice", side_effect=KeyboardInterrupt()), self.assertRaises(KeyboardInterrupt):
            self.service.tick(identifier)
        self.now += timedelta(seconds=1141)
        result = self.service.tick(identifier)
        self.assertFalse(result["executed"])
        self.assertEqual(result["stop_reason"], "request_budget")
        self.assertFalse(result["child"]["coverage_complete"])
        self.assertEqual(result["attempts"], 1)
        self.assertIsNone(result["child"]["batch_id"])
        self.assertTrue(result["notify"])

    def test_batch_reconciliation_retains_discovery_request_budget(self):
        self.search_id = self.searches.configure({"schema_version": 1,
            "sources": [{"id": "fictional-gh", "provider": "greenhouse", "board": "example"},
                {"id": "fictional-other", "provider": "ashby", "board": "example"}],
            "claim_ids": list(self.claim_ids), "max_requests": 1}, idempotency_key="budget-scope")["search_id"]
        identifier = self.configure(max_attempts=1)
        original = SearchService._change
        def interrupt(service, run_id, owner, epoch, action, update, **kwargs):
            if action == "child_progress":
                raise KeyboardInterrupt()
            return original(service, run_id, owner, epoch, action, update, **kwargs)
        with patch.object(SearchService, "_change", new=interrupt), self.assertRaises(KeyboardInterrupt):
            self.service.tick(identifier)
        result = self.service.tick(identifier)
        self.assertFalse(result["executed"])
        self.assertEqual(result["stop_reason"], "request_budget")
        self.assertEqual(result["child"]["counts"]["draft"], 2)
        self.assertFalse(result["child"]["coverage_complete"])

    def test_clock_rollback_during_render_blocks_material_commit_and_releases_owner(self):
        identifier = self.configure()
        original = self.now
        def rollback(structure):
            self.renderer.on_render = None
            self.now -= timedelta(minutes=1)
        self.renderer.on_render = rollback
        with self.assertRaises(ScheduleExecutionError):
            self.service.tick(identifier)
        self.assertEqual(self.materials(), ())
        self.now = original
        result = self.service.tick(identifier)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(self.materials()), 2)

    def test_pause_then_immediate_resume_during_render_fences_old_owner(self):
        identifier = self.configure()
        def revoke(structure):
            self.renderer.on_render = None
            self.service.set_enabled(identifier, False, idempotency_key="pause-during-render")
            self.service.set_enabled(identifier, True, idempotency_key="resume-during-render")
        self.renderer.on_render = revoke
        with self.assertRaises(ScheduleExecutionError):
            self.service.tick(identifier)
        self.assertEqual(self.materials(), ())
        recovered = self.service.tick(identifier)
        self.assertEqual(recovered["status"], "completed")
        self.assertEqual(len(self.materials()), 2)

    def test_offline_and_paused_dates_are_separate_latest_only(self):
        self.transport.responses[GREENHOUSE] = encoded({"jobs": []})
        identifier = self.configure()
        self.service.tick(identifier)
        self.now += timedelta(days=5)
        result = self.service.tick(identifier)
        self.assertEqual(result["missed_dates"], 4)
        self.service.set_enabled(identifier, False, idempotency_key="pause")
        self.now += timedelta(days=4)
        self.assertFalse(self.service.tick(identifier)["executed"])
        self.service.set_enabled(identifier, True, idempotency_key="resume")
        result = self.service.tick(identifier)
        self.assertEqual(result["missed_dates"], 4)
        self.assertEqual(result["paused_dates"], 3)
        self.assertEqual(len(self.transport.calls), 3)

    def test_existing_occurrence_due_instant_survives_timezone_database_reinterpretation(self):
        identifier = self.configure(max_items=1)
        first = self.service.tick(identifier)
        with patch("grounded_apply.services.schedules.due_instant", side_effect=AssertionError("historical instant must remain frozen")):
            shown = self.service.get(identifier)
        self.assertEqual(shown["due"]["due_at"], first["current_occurrence"]["due_at"])

    def test_capacity_before_dispatch_rolls_back_attempt_and_never_requests(self):
        identifier = self.configure()
        with patch("grounded_apply.services.schedules.MAX_SNAPSHOT_BYTES", 1), self.assertRaises(ScheduleCapacityError):
            self.service.tick(identifier)
        self.assertEqual(self.service.get(identifier)["attempts"], 0)
        self.assertEqual(self.transport.calls, [])

    def test_orphan_self_hashed_notification_is_not_deliverable(self):
        identifier = self.configure()
        result = self.service.tick(identifier)
        with self.factory(False) as repository, repository.transaction():
            old = repository.get_schedule_notification(result["notification"]["id"])
            payload = {key: value for key, value in old.items() if key not in {"id", "notification_sha256", "delta_json"}}
            payload["delta"] = json.loads(old["delta_json"])
            payload["created_at"] = (self.now + timedelta(seconds=1)).isoformat(timespec="microseconds")
            fake = str(uuid5(NAMESPACE_URL, "grounded-apply.notification@1/" + digest(payload)))
            repository.insert_schedule_notification(notification_id=fake, **payload, notification_sha256=digest(payload))
        with self.assertRaises(ScheduleIntegrityError):
            self.service.get(identifier)

    def test_rehashed_selected_date_and_gap_forgery_are_rejected(self):
        self.transport.responses[GREENHOUSE] = encoded({"jobs": []})
        identifier = self.configure()
        self.service.tick(identifier)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute("SELECT * FROM schedule_events ORDER BY position").fetchall()
            connection.execute("DROP TRIGGER schedule_events_no_update")
            previous = None
            for row in rows:
                state = json.loads(row["state_json"])
                if row["action"] != "created":
                    state["last_local_date"] = "2026-11-20"
                    state["missed_dates"] = 100
                fingerprint = digest(_event(identifier, row["position"], row["at"], row["action"], state, previous, row["workflow_run_id"]))
                connection.execute("UPDATE schedule_events SET state_json=?,previous_sha256=?,event_sha256=? WHERE id=?",
                    (canonical(state), previous, fingerprint, row["id"]))
                previous = fingerprint
            connection.commit()
        with self.assertRaises(ScheduleIntegrityError):
            self.service.tick(identifier)


if __name__ == "__main__":
    unittest.main()
