from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from grounded_apply.repositories import SQLiteRepository
from grounded_apply.services.schedule_notifications import (
    NotificationUpdate, notification_delta, notification_update, validate_notification_summary,
)
from grounded_apply.services.schedules import ScheduleService
from grounded_apply.services.searches import SearchService
from tests.test_batches import RecordingRenderer
from tests.test_discovery import GREENHOUSE, encoded
from tests.test_materials import approved_fixture
from tests.test_searches import SearchTransport


class ScheduleNotificationRotationTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="gapply-synthetic-rotation-notifications-")
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "fictional.db"
        with SQLiteRepository(self.database).initialize() as repository:
            _, self.claim_ids = approved_fixture(repository)
        self.active_repositories = 0
        self.now = datetime(2026, 9, 20, 12, tzinfo=UTC)
        self.transport = SearchTransport(self)
        self.renderer = RecordingRenderer()
        self.searches = SearchService(self.factory, self.transport, self.renderer, clock=lambda: self.now)
        self.schedules = ScheduleService(self.factory, self.transport, self.renderer, clock=lambda: self.now)

    @contextmanager
    def factory(self, read_only: bool):
        with SQLiteRepository(self.database, existing_only=True, read_only=read_only).initialize() as repository:
            self.active_repositories += 1
            try:
                yield repository
            finally:
                self.active_repositories -= 1

    def configure(self, source_count=2, *, byte_budget=False):
        sources = [{"id": f"fictional-{index}", "provider": "greenhouse", "board": f"fictional{index}"}
            for index in range(source_count)]
        for source in sources:
            self.transport.responses[GREENHOUSE.replace("example", source["board"])] = encoded({"jobs": []})
        limits = {"max_bytes": len(encoded({"jobs": []}))} if byte_budget else {"max_requests": 1}
        scope = self.searches.configure({"schema_version": 1, "sources": sources,
            "claim_ids": list(self.claim_ids), **limits}, idempotency_key="fictional-scope")["search_id"]
        return self.schedules.configure({"schema_version": 1, "search_id": scope, "timezone": "UTC",
            "local_time": "09:00", "start_date": self.now.date().isoformat()},
            idempotency_key="fictional-daily")["schedule_id"]

    def _assert_quiet_rotation(self, source_count, *, byte_budget=False):
        schedule = self.configure(source_count, byte_budget=byte_budget)
        expected_stop = "byte_budget" if byte_budget else "request_budget"
        first = self.schedules.tick(schedule)
        self.assertTrue(first["notify"])
        self.assertEqual(first["notification"]["delta"]["source_changes"], [])
        self.schedules.ack(schedule, first["notification"]["id"], idempotency_key="ack-initial-gap")
        for day in range(1, source_count * 2):
            self.now += timedelta(days=1)
            result = self.schedules.tick(schedule)
            self.assertTrue(result["executed"])
            self.assertFalse(result["notify"])
            self.assertIsNone(result["notification"])
            self.assertEqual(result["child"]["source_priority"][0], f"fictional-{day % source_count}")
            self.assertEqual(result["child"]["stop_reason"], expected_stop)
            self.assertFalse(result["child"]["coverage_complete"])
            self.assertEqual(result["child"]["requests_used"], 1)
            self.assertEqual(result["child"]["counts"]["total"], 0)
            calls = len(self.transport.calls)
            self.assertFalse(self.schedules.tick(schedule)["notify"])
            self.assertEqual(len(self.transport.calls), calls)
        with self.factory(True) as repository:
            self.assertEqual(len(repository.list_schedule_notifications(schedule)), 1)

    def test_two_empty_boards_are_quiet_after_acknowledged_request_gap(self):
        self._assert_quiet_rotation(2)

    def test_three_empty_boards_are_quiet_after_acknowledged_request_gap(self):
        self._assert_quiet_rotation(3)

    def test_byte_budget_rotation_is_quiet_without_hiding_coverage_gap(self):
        self._assert_quiet_rotation(2, byte_budget=True)

    def test_legacy_notices_replay_and_ack_unchanged_then_normalize_once(self):
        # Reproduce the former update policy while leaving persisted delta
        # validation unchanged. No historical event or notice is rewritten.
        def legacy_update(report, previous=None):
            update = notification_update(report, previous)
            sources = {entry["source_id"]: entry for entry in update.summary["sources"]}
            for entry in report["sources"]:
                if entry["stage"] == "deferred":
                    sources[entry["source_id"]] = {"source_id": entry["source_id"], "status": "deferred", "errors": []}
            summary = validate_notification_summary({**update.summary, "sources": list(sources.values())})
            delta = notification_delta(previous, summary)
            return NotificationUpdate(summary, delta, any(bool(value) for value in delta.values()))

        schedule = self.configure()
        with patch("grounded_apply.services.schedules.notification_update", side_effect=legacy_update):
            first = self.schedules.tick(schedule)
            self.schedules.ack(schedule, first["notification"]["id"], idempotency_key="ack-legacy-first")
            self.now += timedelta(days=1)
            second = self.schedules.tick(schedule)
        with self.factory(True) as repository:
            old_notices = repository.list_schedule_notifications(schedule)
            old_events = repository.list_schedule_events(schedule)
        before = self.database.read_bytes()
        reviewed = self.schedules.get(schedule)
        self.assertEqual(reviewed["notification"], second["notification"])
        self.assertEqual(self.database.read_bytes(), before)
        self.schedules.ack(schedule, second["notification"]["id"], idempotency_key="ack-legacy-second")
        self.now += timedelta(days=1)
        normalization = self.schedules.tick(schedule)
        self.assertTrue(normalization["notify"])
        changes = normalization["notification"]["delta"]["source_changes"]
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["source_id"], "fictional-0")
        self.assertEqual(changes[0]["previous"]["status"], "deferred")
        self.assertEqual(changes[0]["current"]["status"], "successful")
        self.schedules.ack(schedule, normalization["notification"]["id"], idempotency_key="ack-normalization")
        for _ in range(3):
            self.now += timedelta(days=1)
            self.assertFalse(self.schedules.tick(schedule)["notify"])
        with self.factory(True) as repository:
            self.assertEqual(repository.list_schedule_notifications(schedule)[:len(old_notices)], old_notices)
            self.assertEqual(repository.list_schedule_events(schedule)[:len(old_events)], old_events)


if __name__ == "__main__":
    unittest.main()
