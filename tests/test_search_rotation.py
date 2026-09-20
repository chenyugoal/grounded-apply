from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from grounded_apply.repositories import SQLiteRepository
from grounded_apply.services.jobs import JobService
from grounded_apply.services.schedules import ScheduleService
from grounded_apply.services.searches import SearchIntegrityError, SearchLeaseActiveError, SearchService, _event
from grounded_apply.services.workflow import canonical, digest
from tests.test_batches import RecordingRenderer
from tests.test_discovery import ASHBY, GREENHOUSE, ashby_job, encoded, greenhouse_job
from tests.test_materials import approved_fixture
from tests.test_searches import SearchTransport


class SearchRotationTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="gapply-synthetic-source-rotation-")
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "fictional.db"
        with SQLiteRepository(self.database).initialize() as repository:
            _, self.claim_ids = approved_fixture(repository)
        self.active_repositories = 0
        self.now = datetime(2026, 9, 20, 12, tzinfo=UTC)
        self.transport = SearchTransport(self)
        self.transport.responses[GREENHOUSE] = encoded({"jobs": [greenhouse_job(1)]})
        self.transport.responses[ASHBY] = encoded({"apiVersion": "1", "jobs": [ashby_job("fictional-a")]})
        self.renderer = RecordingRenderer()
        self.service = SearchService(self.factory, self.transport, self.renderer, clock=lambda: self.now)

    @contextmanager
    def factory(self, read_only: bool):
        with SQLiteRepository(self.database, existing_only=True, read_only=read_only).initialize() as repository:
            self.active_repositories += 1
            try:
                yield repository
            finally:
                self.active_repositories -= 1

    def configure(self, **changes):
        spec = {"schema_version": 1, "sources": [
            {"id": "fictional-gh", "provider": "greenhouse", "board": "example"},
            {"id": "fictional-ashby", "provider": "ashby", "board": "example"}],
            "claim_ids": list(self.claim_ids), "max_jobs": 1, **changes}
        return self.service.configure(spec, idempotency_key="fictional-scope")["search_id"]

    def fail_greenhouse(self, structure):
        with self.factory(True) as repository:
            job = JobService(repository).get(structure.job_id)
        if job.discovery["provider"] == "greenhouse":
            self.renderer.failures[structure.job_id] = "synthetic persistent posting failure"

    def test_daily_cap_one_reaches_other_board_and_manuals_do_not_consume_turns(self):
        scope = self.configure(sources=[
            {"id": "fictional-manual-a", "provider": "manual", "careers_url": "https://example.com/a"},
            {"id": "fictional-gh", "provider": "greenhouse", "board": "example"},
            {"id": "fictional-manual-b", "provider": "manual", "careers_url": "https://example.com/b"},
            {"id": "fictional-ashby", "provider": "ashby", "board": "example"}])
        self.renderer.on_render = self.fail_greenhouse
        schedules = ScheduleService(self.factory, self.transport, self.renderer, clock=lambda: self.now)
        schedule = schedules.configure({"schema_version": 1, "search_id": scope, "timezone": "UTC", "local_time": "09:00",
            "start_date": "2026-09-20"}, idempotency_key="fictional-daily")["schedule_id"]
        first = schedules.tick(schedule)["child"]
        self.assertEqual(first["counts"]["blocked"], 1)
        self.assertEqual(first["source_priority"], ["fictional-gh", "fictional-ashby", "fictional-manual-a", "fictional-manual-b"])
        calls = len(self.transport.calls)
        self.assertFalse(schedules.tick(schedule)["executed"])
        self.assertEqual(len(self.transport.calls), calls)
        self.now += timedelta(days=1)
        second = schedules.tick(schedule)["child"]
        self.assertEqual(second["counts"]["draft"], 1)
        self.assertEqual(second["selection"][0]["source_id"], "fictional-ashby")
        self.assertEqual(second["source_order"]["generation"], 2)
        self.assertEqual(second["source_priority"], ["fictional-ashby", "fictional-gh", "fictional-manual-a", "fictional-manual-b"])
        self.assertEqual([entry[0] for entry in self.transport.calls], [GREENHOUSE, ASHBY, ASHBY, GREENHOUSE])
        self.assertEqual([entry["source_id"] for entry in second["sources"]],
            ["fictional-manual-a", "fictional-gh", "fictional-manual-b", "fictional-ashby"])

    def test_more_sources_than_candidate_window_eventually_receive_first_quota(self):
        sources = [{"id": f"fictional-{index}", "provider": "greenhouse", "board": f"fictional{index}"} for index in range(6)]
        for index in range(6):
            self.transport.responses[GREENHOUSE.replace("example", f"fictional{index}")] = encoded({"jobs": [greenhouse_job(index + 1)]})
        scope = self.configure(sources=sources)
        self.renderer.on_render = lambda structure: self.renderer.failures.setdefault(structure.job_id, "synthetic posting failure")
        results = [self.service.run(scope, idempotency_key=f"round-{index}") for index in range(6)]
        self.assertEqual([result["selection"][0]["source_id"] for result in results], [f"fictional-{index}" for index in range(6)])
        self.assertTrue(all(result["candidate_limit"] == 4 for result in results))
        self.assertTrue(all(sum(source["captured_count"] for source in result["sources"]) == 4 for result in results))

    def test_request_budget_rotates_fetch_priority_before_deferring_other_sources(self):
        scope = self.configure(max_requests=1)
        first = self.service.run(scope, idempotency_key="first")
        second = self.service.run(scope, idempotency_key="second")
        self.assertEqual([entry[0] for entry in self.transport.calls], [GREENHOUSE, ASHBY])
        self.assertEqual([first["selection"][0]["source_id"], second["selection"][0]["source_id"]], ["fictional-gh", "fictional-ashby"])
        self.assertTrue(all(not result["coverage_complete"] and result["stop_reason"] == "request_budget" for result in (first, second)))

    def _assert_completed_budget_replay_retains_gap(self, expected, **limits):
        scope = self.configure(**limits)
        first = self.service.run(scope, idempotency_key="bounded")
        self.assertEqual(first["phase"], "complete")
        self.assertEqual(first["stop_reason"], expected)
        calls, builds = len(self.transport.calls), len(self.renderer.built)
        replay = self.service.run(scope, idempotency_key="bounded")
        self.assertEqual(replay["stop_reason"], expected)
        self.assertFalse(replay["coverage_complete"])
        self.assertEqual(replay["source_order"], first["source_order"])
        self.assertEqual((len(self.transport.calls), len(self.renderer.built)), (calls, builds))
        self.assertEqual(self.service.get(first["run_id"])["stop_reason"], expected)

    def test_completed_request_budget_replay_keeps_unfinished_source_reason(self):
        self._assert_completed_budget_replay_retains_gap("request_budget", max_requests=1)

    def test_completed_byte_budget_replay_keeps_unfinished_source_reason(self):
        self._assert_completed_budget_replay_retains_gap("byte_budget", max_bytes=len(self.transport.responses[GREENHOUSE]))

    def test_failed_overlap_does_not_reserve_rotation_and_replays_keep_generation(self):
        scope = self.configure()
        waiting = []
        def overlap():
            self.transport.on_get = None
            with self.assertRaises(SearchLeaseActiveError) as failure:
                self.service.run(scope, idempotency_key="waiting")
            waiting.append(failure.exception.run_id)
            pending = self.service.get(waiting[0])
            self.assertNotIn("source_order", pending)
        self.transport.on_get = overlap
        first = self.service.run(scope, idempotency_key="first")
        second = self.service.resume(waiting[0])
        calls = len(self.transport.calls)
        replay = self.service.run(scope, idempotency_key="first")
        self.assertEqual(first["source_order"], replay["source_order"])
        self.assertEqual(second["source_order"]["generation"], 2)
        self.assertEqual(len(self.transport.calls), calls)

    def test_interrupted_run_keeps_reservation_after_later_run_and_restore(self):
        scope = self.configure()
        self.transport.responses[GREENHOUSE] = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.service.run(scope, idempotency_key="interrupted")
        with self.factory(True) as repository:
            interrupted = repository.list_search_runs(scope)[0]["id"]
        reserved = self.service.get(interrupted)["source_order"]
        self.transport.responses[GREENHOUSE] = encoded({"jobs": [greenhouse_job(1)]})
        later = self.service.run(scope, idempotency_key="later")
        self.assertEqual(later["source_order"]["generation"], 2)
        restored = self.database.with_name("restored.db")
        shutil.copy2(self.database, restored)
        self.database = restored
        resumed = self.service.resume(interrupted)
        self.assertEqual(resumed["source_order"], reserved)
        self.assertEqual(resumed["source_priority"], ["fictional-gh", "fictional-ashby"])
        fresh = self.service.run(scope, idempotency_key="after-restore")
        self.assertEqual(fresh["source_order"]["generation"], 3)

    def test_legacy_started_run_retains_original_order_and_exact_event_prefix(self):
        scope = self.configure()
        self.transport.responses[GREENHOUSE] = KeyboardInterrupt()
        with patch.object(self.service, "_reserve_source_order", return_value=None), self.assertRaises(KeyboardInterrupt):
            self.service.run(scope, idempotency_key="legacy")
        with self.factory(True) as repository:
            run_id = repository.list_search_runs(scope)[0]["id"]
            prefix = repository.list_search_events(run_id)
        self.transport.responses[GREENHOUSE] = encoded({"jobs": [greenhouse_job(1)]})
        resumed = self.service.resume(run_id)
        self.assertNotIn("source_order", resumed)
        self.assertNotIn("source_priority", resumed)
        self.assertEqual(resumed["selection"][0]["source_id"], "fictional-gh")
        with self.factory(True) as repository:
            self.assertEqual(repository.list_search_events(run_id)[:len(prefix)], prefix)
        fresh = self.service.run(scope, idempotency_key="new-policy")
        self.assertEqual(fresh["source_order"]["generation"], 1)

    def _forge_reservation(self, run_id, transform):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute("SELECT * FROM search_run_events WHERE run_id=? ORDER BY position", (run_id,)).fetchall()
            connection.execute("DROP TRIGGER search_events_no_update")
            previous = None
            for row in rows:
                state = json.loads(row["state_json"])
                if "source_order" in state:
                    transform(state["source_order"])
                fingerprint = digest(_event(run_id, row["position"], row["at"], row["action"], state, previous))
                connection.execute("UPDATE search_run_events SET state_json=?,previous_sha256=?,event_sha256=? WHERE id=?",
                    (canonical(state), previous, fingerprint, row["id"]))
                previous = fingerprint
            connection.commit()
        calls = len(self.transport.calls)
        with self.assertRaises(SearchIntegrityError):
            self.service.get(run_id)
        with self.assertRaises(SearchIntegrityError):
            self.service.resume(run_id)
        self.assertEqual(len(self.transport.calls), calls)

    def test_rehashed_future_reservation_epoch_is_rejected(self):
        run = self.service.run(self.configure(), idempotency_key="first")
        self._forge_reservation(run["run_id"], lambda order: order.update(reserved_epoch=100))

    def test_rehashed_rotation_generation_cannot_change_frozen_source_sequence(self):
        run = self.service.run(self.configure(), idempotency_key="first")
        self._forge_reservation(run["run_id"], lambda order: order.update(generation=2, reserved_epoch=2))


if __name__ == "__main__":
    unittest.main()
