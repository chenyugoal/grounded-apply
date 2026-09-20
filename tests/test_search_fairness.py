from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from grounded_apply.repositories import SQLiteRepository
from grounded_apply.services.jobs import JobService
from grounded_apply.services.schedules import ScheduleService
from grounded_apply.services.searches import SearchExecutionError, SearchService
from tests.test_batches import RecordingRenderer
from tests.test_discovery import ASHBY, GREENHOUSE, ashby_job, encoded, greenhouse_job
from tests.test_materials import approved_fixture
from tests.test_searches import SearchTransport


class SearchFairnessTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="gapply-synthetic-search-fairness-")
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "fictional.db"
        with SQLiteRepository(self.database).initialize() as repository:
            _, self.claim_ids = approved_fixture(repository)
        self.active_repositories = 0
        self.now = datetime(2026, 9, 20, 12, tzinfo=UTC)
        self.transport = SearchTransport(self)
        self.transport.responses[GREENHOUSE] = encoded({"jobs": [greenhouse_job(i) for i in range(1, 5)]})
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

    def configure(self, *, key="fictional-scope", **changes):
        spec = {"schema_version": 1, "sources": [{"id": "fictional-gh", "provider": "greenhouse", "board": "example"}],
            "claim_ids": list(self.claim_ids), "max_jobs": 2, **changes}
        return self.service.configure(spec, idempotency_key=key)["search_id"]

    def ids(self, report):
        return [item["source_url"].rsplit("/", 1)[-1] for item in report["selection"]]

    def fail_first_two(self, structure):
        with self.factory(True) as repository:
            job = JobService(repository).get(structure.job_id)
        if job.source_url.rsplit("/", 1)[-1] in {"1", "2"}:
            self.renderer.failures[structure.job_id] = "synthetic persistent per-job rendering failure"

    def test_daily_persistent_blockers_cannot_starve_later_jobs_and_are_retried(self):
        scope = self.configure()
        self.renderer.on_render = self.fail_first_two
        schedules = ScheduleService(self.factory, self.transport, self.renderer, clock=lambda: self.now)
        schedule = schedules.configure({"schema_version": 1, "search_id": scope, "timezone": "UTC", "local_time": "09:00",
            "start_date": "2026-09-20"}, idempotency_key="fair-daily")["schedule_id"]
        first = schedules.tick(schedule)
        self.assertEqual(self.ids(first["child"]), ["1", "2"])
        self.assertEqual(first["child"]["counts"]["blocked"], 2)
        self.now += timedelta(days=1)
        second = schedules.tick(schedule)
        self.assertEqual(self.ids(second["child"]), ["3", "4"])
        self.assertEqual(second["child"]["counts"]["draft"], 2)
        self.now += timedelta(days=1)
        third = schedules.tick(schedule)
        self.assertEqual(self.ids(third["child"]), ["1", "2"])
        self.assertEqual(third["child"]["counts"]["blocked"], 2)
        self.assertEqual(third["child"]["skipped"]["unchanged_draft"], 2)
        self.assertEqual(len(set(self.renderer.built)), 4)
        with self.factory(True) as repository:
            self.assertEqual(len(repository.list_material_ids()), 2)

    def test_attempt_totals_and_stable_adapter_ties_drive_order_without_hiding_blockers(self):
        scope = self.configure(max_jobs=1)
        self.renderer.on_render = lambda structure: self.renderer.failures.setdefault(structure.job_id, "synthetic job failure")
        results = [self.service.run(scope, idempotency_key=f"round-{i}") for i in range(5)]
        self.assertEqual([self.ids(report) for report in results], [["1"], ["2"], ["3"], ["4"], ["1"]])
        self.assertTrue(all(report["counts"]["blocked"] == 1 for report in results))
        before = len(self.renderer.built), len(self.transport.calls)
        replay = self.service.run(scope, idempotency_key="round-0")
        self.assertEqual(self.ids(replay), ["1"])
        # An explicit replay retries its fixed blocked item, without reselecting
        # or rediscovering as newer rounds change comparative attempt counts.
        self.assertEqual(len(self.renderer.built), before[0] + 1)
        self.assertEqual(len(self.transport.calls), before[1])

    def test_changed_content_is_a_fresh_version_and_changed_scope_does_not_inherit_priority(self):
        scope = self.configure(max_jobs=1)
        self.renderer.on_render = lambda structure: self.renderer.failures.setdefault(structure.job_id, "synthetic job failure")
        first = self.service.run(scope, idempotency_key="first")
        changed = greenhouse_job(1)
        changed["content"] += "<p>New fictional role details.</p>"
        self.transport.responses[GREENHOUSE] = encoded({"jobs": [changed, greenhouse_job(2)]})
        updated = self.service.run(scope, idempotency_key="updated")
        self.assertEqual(self.ids(updated), ["1"])
        self.assertNotEqual(updated["selection"][0]["job_id"], first["selection"][0]["job_id"])
        other = self.service.run(self.configure(key="separate", max_jobs=1), idempotency_key="first")
        self.assertEqual(self.ids(other), ["1"])

    def test_source_checkpoint_order_stays_frozen_when_other_run_changes_history(self):
        scope = self.configure()
        self.renderer.on_render = self.fail_first_two
        with patch.object(self.service, "_freeze", side_effect=RuntimeError("synthetic after-source interruption")), self.assertRaises(SearchExecutionError) as interrupted:
            self.service.run(scope, idempotency_key="captured")
        later = self.service.run(scope, idempotency_key="later")
        self.assertEqual(self.ids(later), ["1", "2"])
        calls = len(self.transport.calls)
        recovered = self.service.resume(interrupted.exception.run_id)
        self.assertEqual(self.ids(recovered), ["1", "2"])
        self.assertEqual(len(self.transport.calls), calls)
        fresh = self.service.run(scope, idempotency_key="fresh")
        self.assertEqual(self.ids(fresh), ["3", "4"])

    def test_v2_filters_still_precede_quota_and_multisource_rotation_is_preserved(self):
        scope = self.configure(schema_version=2, preparation_filters={"title_excludes": ["Intern"]},
            sources=[{"id": "fictional-gh", "provider": "greenhouse", "board": "example"},
                {"id": "fictional-ashby", "provider": "ashby", "board": "example"}])
        self.transport.responses[GREENHOUSE] = encoded({"jobs": [greenhouse_job(1, title="Fictional Intern"), greenhouse_job(2), greenhouse_job(3)]})
        self.transport.responses[ASHBY] = encoded({"jobs": [ashby_job("a"), ashby_job("b")], "apiVersion": "1"})
        self.renderer.on_render = lambda structure: self.renderer.failures.setdefault(structure.job_id, "synthetic job failure")
        first = self.service.run(scope, idempotency_key="first")
        self.assertEqual([item["source_id"] for item in first["selection"]], ["fictional-gh", "fictional-ashby"])
        self.assertEqual(self.ids(first), ["2", "a"])
        second = self.service.run(scope, idempotency_key="second")
        self.assertEqual([item["source_id"] for item in second["selection"]], ["fictional-ashby", "fictional-gh"])
        self.assertEqual(self.ids(second), ["b", "3"])
        self.assertEqual(second["skipped"]["title_excluded"], 1)


if __name__ == "__main__":
    unittest.main()
