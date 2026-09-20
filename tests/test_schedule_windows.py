from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from grounded_apply.repositories import SQLiteRepository
from grounded_apply.services.schedules import ScheduleService
from grounded_apply.services.searches import SearchService
from tests.test_materials import SyntheticRenderer, approved_fixture
from tests.test_netflix_source import job_url, page, posting
from tests.test_search_windows import WindowTransport


class ScheduleWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="gapply-synthetic-schedule-windows-")
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "fictional.db"
        with SQLiteRepository(self.database).initialize() as repository:
            _, claims = approved_fixture(repository)
        self.active_repositories = 0
        self.now = datetime(2026, 9, 20, 12, tzinfo=UTC)
        self.transport = WindowTransport(self)
        for identifier in (1008, 1009):
            self.transport.responses[job_url(identifier)] = page(posting(identifier, title="Fictional Research Engineer"))
        renderer = SyntheticRenderer()
        self.searches = SearchService(self.factory, self.transport, renderer, clock=lambda: self.now)
        self.schedules = ScheduleService(self.factory, self.transport, renderer, clock=lambda: self.now)
        self.search_id = self.searches.configure({"schema_version": 1,
            "sources": [{"id": "fictional-netflix", "provider": "netflix", "board": "netflix"}],
            "claim_ids": list(claims), "title_contains": ["Research"]}, idempotency_key="fictional-search")["search_id"]
        self.schedule_id = self.schedules.configure({"schema_version": 1, "search_id": self.search_id,
            "timezone": "UTC", "local_time": "09:00", "start_date": "2026-09-20"},
            idempotency_key="fictional-daily")["schedule_id"]

    @contextmanager
    def factory(self, read_only: bool):
        with SQLiteRepository(self.database, existing_only=True, read_only=read_only).initialize() as repository:
            self.active_repositories += 1
            try:
                yield repository
            finally:
                self.active_repositories -= 1

    @staticmethod
    def window(report: dict[str, Any]) -> dict[str, Any]:
        return report["child"]["sources"][0]["window"]

    def acknowledge(self, report: dict[str, Any]) -> dict[str, Any]:
        notification = report["notification"]
        self.assertIsNotNone(notification)
        self.assertEqual(notification["run_id"], report["run_id"])
        return self.schedules.ack(self.schedule_id, notification["notification_id"],
            idempotency_key="fictional-ack-" + notification["notification_id"])

    def test_later_daily_window_finds_drafts_and_only_meaningful_changes_notify(self) -> None:
        first = self.schedules.tick(self.schedule_id)
        self.assertTrue(first["executed"])
        self.assertFalse(first["child"]["coverage_complete"])
        self.assertEqual(first["child"]["counts"]["draft"], 0)
        self.assertEqual(first["child"]["sources"][0]["report"]["filtered_count"], 7)
        self.assertEqual(self.window(first)["generation"], 1)
        self.assertEqual(self.window(first)["progress"]["next"]["external_id"], "1007")
        self.assertEqual(len(self.transport.calls), 10)
        self.assertTrue(first["notify"], "first observation must disclose the partial source")
        self.assertFalse(self.acknowledge(first)["notify"])

        same_day = self.schedules.tick(self.schedule_id)
        self.assertFalse(same_day["executed"])
        self.assertFalse(same_day["notify"])
        self.assertEqual(same_day["run_id"], first["run_id"])
        self.assertEqual(self.window(same_day), self.window(first))
        self.assertEqual(len(self.transport.calls), 10)

        self.now += timedelta(days=1)
        second = self.schedules.tick(self.schedule_id)
        self.assertTrue(second["executed"])
        self.assertNotEqual(second["run_id"], first["run_id"])
        self.assertEqual(self.window(second)["generation"], 2)
        self.assertEqual(self.window(second)["base_run_id"], first["run_id"])
        self.assertEqual(self.window(second)["after"]["external_id"], "1007")
        self.assertEqual(self.window(second)["progress"]["next"]["external_id"], "1013")
        self.assertEqual(second["child"]["counts"]["draft"], 2)
        self.assertEqual(len(second["notification"]["delta"]["new_materials"]), 2)
        self.assertEqual(second["notification"]["delta"]["source_changes"], [])
        self.assertFalse(second["child"]["coverage_complete"])
        self.assertEqual(len(self.transport.calls), 20)
        materials = tuple(item["material_id"] for item in second["child"]["items"])
        self.assertFalse(self.acknowledge(second)["notify"])
        repeated = self.schedules.tick(self.schedule_id)
        self.assertFalse(repeated["executed"])
        self.assertFalse(repeated["notify"])
        self.assertEqual(tuple(item["material_id"] for item in repeated["child"]["items"]), materials)
        self.assertEqual(len(self.transport.calls), 20)

        self.now += timedelta(days=1)
        third = self.schedules.tick(self.schedule_id)
        self.assertTrue(third["executed"])
        self.assertTrue(self.window(third)["progress"]["cycle_complete"])
        self.assertFalse(third["child"]["coverage_complete"])
        self.assertEqual(third["child"]["counts"]["draft"], 0)
        self.assertFalse(third["notify"], "unchanged partial-source coverage is not a new notification")
        self.assertEqual(len(self.transport.calls), 26)
        self.assertFalse(third["approvals_recorded"])
        self.assertFalse(third["external_action_taken"])
        self.assertFalse(third["scheduler_installed"])
        with self.factory(True) as repository:
            self.assertEqual(set(repository.list_material_ids()), set(materials))

    def test_interrupted_window_recovers_across_midnight_before_new_daily_generation(self) -> None:
        def interrupt(url: str) -> None:
            if url == job_url(1004):
                raise KeyboardInterrupt()
        self.transport.on_get = interrupt
        with self.assertRaises(KeyboardInterrupt):
            self.schedules.tick(self.schedule_id)
        interrupted = self.schedules.get(self.schedule_id)
        reserved = self.window(interrupted)
        self.assertEqual(interrupted["current_occurrence"]["local_date"], "2026-09-20")
        self.assertEqual(interrupted["attempts"], 1)
        self.assertEqual(reserved["generation"], 1)
        self.assertIsNone(reserved["progress"])
        self.assertIsNone(reserved["after"])
        self.assertEqual(interrupted["child"]["requests_used"], 7)
        self.assertFalse(interrupted["notify"])

        self.transport.on_get = None
        self.now += timedelta(days=1)
        recovered = self.schedules.tick(self.schedule_id)
        self.assertTrue(recovered["executed"])
        self.assertEqual(recovered["run_id"], interrupted["run_id"])
        self.assertEqual(recovered["current_occurrence"]["local_date"], "2026-09-20")
        self.assertEqual(recovered["attempts"], 2)
        self.assertEqual(recovered["child"]["requests_used"], 17)
        recovered_window = self.window(recovered)
        frozen_fields = set(reserved) - {"progress", "completed_epoch"}
        self.assertEqual({key: reserved[key] for key in frozen_fields},
            {key: recovered_window[key] for key in frozen_fields})
        self.assertEqual(recovered_window["progress"]["next"]["external_id"], "1007")
        self.assertEqual(recovered["child"]["counts"]["draft"], 0)
        self.assertFalse(self.acknowledge(recovered)["notify"])

        today = self.schedules.tick(self.schedule_id)
        self.assertTrue(today["executed"])
        self.assertNotEqual(today["run_id"], interrupted["run_id"])
        self.assertEqual(today["current_occurrence"]["local_date"], "2026-09-21")
        self.assertEqual(today["attempts"], 1)
        self.assertEqual(self.window(today)["generation"], 2)
        self.assertEqual(self.window(today)["base_run_id"], recovered["run_id"])
        self.assertEqual(self.window(today)["after"]["external_id"], "1007")
        self.assertEqual(today["child"]["counts"]["draft"], 2)
        self.assertEqual(len(self.transport.calls), 27)
        self.assertEqual(today["missed_dates"], 0)
        self.assertTrue(today["notify"])
        self.assertEqual(len(today["notification"]["delta"]["new_materials"]), 2)
        self.assertFalse(self.acknowledge(today)["notify"])
        replay = self.schedules.tick(self.schedule_id)
        self.assertFalse(replay["executed"])
        self.assertFalse(replay["notify"])
        self.assertEqual(replay["run_id"], today["run_id"])
        self.assertEqual(len(self.transport.calls), 27)


if __name__ == "__main__":
    unittest.main()
