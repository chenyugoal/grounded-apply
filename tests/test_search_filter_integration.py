from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from grounded_apply.repositories import SQLiteRepository
from grounded_apply.services.discovery import DiscoveryService, SourceSpec
from grounded_apply.services.jobs import JobService
from grounded_apply.services.schedules import ScheduleService
from grounded_apply.services.searches import SearchIntegrityError, SearchService, _event, validate_search_manifest
from grounded_apply.services.workflow import canonical, digest
from tests.test_materials import SyntheticRenderer, approved_fixture
from tests.test_discovery import FakeTransport, GREENHOUSE, encoded, greenhouse_job
from tests.test_searches import SearchTransport


class SearchFilterIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="gapply-synthetic-search-filters-")
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "fictional.db"
        with SQLiteRepository(self.database).initialize() as repository:
            _, self.claim_ids = approved_fixture(repository)
        self.active_repositories = 0
        self.now = datetime(2026, 9, 20, 12, tzinfo=UTC)
        self.renderer = SyntheticRenderer()
        self.transport = SearchTransport(self)
        self.service = SearchService(self.factory, self.transport, self.renderer, clock=lambda: self.now)

    @contextmanager
    def factory(self, read_only: bool):
        with SQLiteRepository(self.database, existing_only=True, read_only=read_only).initialize() as repository:
            self.active_repositories += 1
            try:
                yield repository
            finally:
                self.active_repositories -= 1

    def manifest(self, **changes):
        return {"schema_version": 2, "sources": [{"id": "fictional-gh", "provider": "greenhouse", "board": "example"}],
            "claim_ids": list(self.claim_ids), **changes}

    def configure(self, spec=None, *, key="fictional-scope"):
        return self.service.configure(self.manifest() if spec is None else spec, idempotency_key=key)["search_id"]

    def jobs(self, values):
        entries = []
        for identifier, title, location in values:
            entry = greenhouse_job(identifier, title=title)
            entry["location"] = {"name": location}
            entries.append(entry)
        self.transport.responses[GREENHOUSE] = encoded({"jobs": entries})

    def test_v1_normalized_bytes_and_existing_checkpoint_shapes_remain_unchanged(self):
        spec = self.manifest(schema_version=1)
        expected = {**spec, "title_contains": [], "layout": {"schema_version": 1, "presentations": {}},
            "questions": [], "questionnaire_coverage": "unknown", "excluded_identities": [],
            "max_jobs": 10, "max_requests": 64, "max_bytes": 67108864}
        self.assertEqual(canonical(validate_search_manifest(spec)), canonical(expected))
        scope = self.configure(spec)
        before = self.service.run(scope, idempotency_key="v1")
        with self.factory(True) as repository:
            original = repository.list_search_events(before["run_id"])
        self.configure(key="v2")
        shown = self.service.get(before["run_id"])
        self.assertNotIn("location", shown["selection"][0])
        self.assertEqual(set(shown["skipped"]), {"explicitly_excluded", "already_applied", "unchanged_draft", "invalid_record", "deferred"})
        with self.factory(True) as repository:
            self.assertEqual(repository.list_search_events(before["run_id"]), original)
        with self.assertRaises(ValueError):
            validate_search_manifest({**spec, "preparation_filters": {}})

    def test_v2_defaults_and_invalid_preferences_validate_without_runtime(self):
        normal = validate_search_manifest(self.manifest())
        self.assertEqual(normal["preparation_filters"], {"title_excludes": [], "location_contains": [],
            "location_excludes": [], "missing_location": "include"})
        self.assertEqual(validate_search_manifest(normal), normal)
        with patch.object(self.service, "_open", side_effect=AssertionError("must not open runtime")):
            self.assertTrue(self.service.configure(self.manifest(), idempotency_key="preview", dry_run=True)["dry_run"])
            for invalid in (None, {"country": "fictional"}, {"missing_location": True}, {"location_contains": "Remote"},
                {"title_excludes": [" hidden "]}, {"title_excludes": ["a\u202eb"]}):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    self.service.configure(self.manifest(preparation_filters=invalid), idempotency_key="invalid", dry_run=True)
        self.assertEqual(self.transport.calls, [])

    def test_preferences_filter_before_capture_and_quota_with_fixed_reason_counts(self):
        self.jobs([(1, "Fictional Research Intern", "Fictional Austin"),
            (2, "Fictional Research Engineer", "Fictional Paris"),
            (3, "Fictional Research Engineer", None),
            (4, "Fictional Research Engineer", "Remote — Excluded Region"),
            (5, "Fictional Research Engineer", "Remote — Fictional Austin"),
            (6, "Fictional Research Engineer", "Fictional Austin"),
            (7, "Fictional Sales", "Fictional Austin")])
        spec = self.manifest(title_contains=["research"], max_jobs=1, preparation_filters={
            "title_excludes": ["INTERN"], "location_contains": ["Austin", "remote"],
            "location_excludes": ["Excluded Region"], "missing_location": "exclude"})
        result = self.service.run(self.configure(spec), idempotency_key="first")
        self.assertEqual(result["counts"]["draft"], 1)
        self.assertTrue(result["selection"][0]["source_url"].endswith("/5"))
        self.assertEqual(result["selection"][0]["location"], "Remote — Fictional Austin")
        for reason in ("title_excluded", "location_excluded", "location_not_matched", "location_unknown_excluded", "deferred"):
            self.assertEqual(result["skipped"][reason], 1)
        self.assertEqual(result["sources"][0]["report"]["count"], 6)
        self.assertEqual(result["sources"][0]["report"]["filtered_count"], 1)
        with self.factory(True) as repository:
            self.assertEqual(len(repository.list_job_snapshots()), 2, "excluded records must not consume private capture capacity")
        self.assertEqual(self.transport.calls[0][0], GREENHOUSE)
        self.assertTrue(result["coverage_complete"])
        self.assertFalse(result["application_ready"])

    def test_unknown_location_default_is_explicit_and_does_not_infer_remote(self):
        self.jobs([(1, "Fictional Engineer", None)])
        spec = self.manifest(preparation_filters={"location_contains": ["Remote"], "location_excludes": ["Fictional City"]})
        result = self.service.run(self.configure(spec), idempotency_key="unknown")
        self.assertEqual(result["counts"]["draft"], 1)
        self.assertIn("location", result["selection"][0])
        self.assertIsNone(result["selection"][0]["location"])
        self.assertEqual(result["skipped"]["location_unknown_excluded"], 0)

    def test_all_excluded_is_empty_success_and_filter_changes_create_separate_scope(self):
        self.jobs([(1, "Fictional Engineer", "Fictional City")])
        rejected = self.manifest(preparation_filters={"location_excludes": ["Fictional City"]})
        first = self.service.run(self.configure(rejected), idempotency_key="excluded")
        self.assertEqual(first["counts"]["total"], 0)
        self.assertTrue(first["coverage_complete"])
        self.assertEqual(first["skipped"]["location_excluded"], 1)
        self.assertEqual(first["sources"][0]["report"]["status"], "successful")
        allowed = self.service.run(self.configure(key="changed"), idempotency_key="allowed")
        self.assertEqual(allowed["counts"]["draft"], 1)
        self.assertNotEqual(allowed["search_id"], first["search_id"])

    def test_persisted_capture_violating_filters_is_rejected_on_read_and_resume(self):
        self.jobs([(1, "Fictional Engineer", "Excluded Place")])
        scope = self.configure(self.manifest(preparation_filters={"location_excludes": ["Excluded Place"]}))
        # Simulate a historically faulty capture implementation, without altering
        # the immutable saved policy or falsifying its workflow hashes.
        with patch("grounded_apply.services.searches._preparation_filters", return_value=None):
            faulty = self.service.run(scope, idempotency_key="faulty-capture")
        calls = len(self.transport.calls)
        with self.assertRaises(SearchIntegrityError):
            self.service.get(faulty["run_id"])
        with self.assertRaises(SearchIntegrityError):
            self.service.resume(faulty["run_id"])
        self.assertEqual(len(self.transport.calls), calls)

    def _assert_rehashed_wrong_title_rejected(self, schema_version):
        self.jobs([(1, "Fictional Research Engineer", "Preferred City")])
        options = {"preparation_filters": {"location_contains": ["Preferred"]}} if schema_version == 2 else {}
        scope = self.configure(self.manifest(schema_version=schema_version, title_contains=["research", "scientist"], **options))
        with patch.object(self.service, "_freeze", side_effect=KeyboardInterrupt()), self.assertRaises(KeyboardInterrupt):
            self.service.run(scope, idempotency_key="scoped-role")
        with self.factory(True) as repository:
            run_id = repository.list_search_runs(scope)[0]["id"]
            original = json.loads(repository.list_search_events(run_id)[-1]["state_json"])["sources"][0]["job_ids"][0]
        wrong = greenhouse_job(2, title="Fictional Sales Manager")
        wrong["location"] = {"name": "Preferred City"}
        posting = DiscoveryService(FakeTransport({GREENHOUSE: encoded({"jobs": [wrong]})})).discover(
            (SourceSpec("fictional-gh", "greenhouse", "example"),)).jobs[0]
        with self.factory(False) as repository:
            wrong_id = JobService(repository).capture_discovered(posting)["job_id"]
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute("SELECT * FROM search_run_events WHERE run_id=? ORDER BY position", (run_id,)).fetchall()
            connection.execute("DROP TRIGGER search_events_no_update")
            previous = None
            for row in rows:
                state = json.loads(row["state_json"])
                state["sources"][0]["job_ids"] = [wrong_id if value == original else value for value in state["sources"][0]["job_ids"]]
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
        with self.factory(True) as repository:
            self.assertEqual(repository.list_material_ids(), ())

    def test_v1_rehashed_capture_cannot_bypass_immutable_title_retrieval(self):
        self._assert_rehashed_wrong_title_rejected(1)

    def test_v2_rehashed_capture_cannot_bypass_title_retrieval_with_matching_location(self):
        self._assert_rehashed_wrong_title_rejected(2)

    def test_daily_v2_scope_replays_without_refetch_and_next_day_skips_valid_draft(self):
        self.jobs([(1, "Fictional Engineer", "Remote — Fictional City")])
        scope = self.configure(self.manifest(preparation_filters={"location_contains": ["Remote"]}))
        schedules = ScheduleService(self.factory, self.transport, self.renderer, clock=lambda: self.now)
        identifier = schedules.configure({"schema_version": 1, "search_id": scope, "timezone": "UTC", "local_time": "09:00",
            "start_date": "2026-09-20"}, idempotency_key="filtered-daily")["schedule_id"]
        first = schedules.tick(identifier)
        calls = len(self.transport.calls)
        replay = schedules.tick(identifier)
        self.assertEqual(first["run_id"], replay["run_id"])
        self.assertEqual(len(self.transport.calls), calls)
        self.now += timedelta(days=1)
        second = schedules.tick(identifier)
        self.assertEqual(second["child"]["counts"]["draft"], 0)
        self.assertEqual(second["child"]["skipped"]["unchanged_draft"], 1)
        self.assertEqual(first["child"]["selection"][0]["location"], "Remote — Fictional City")


if __name__ == "__main__":
    unittest.main()
