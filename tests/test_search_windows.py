from __future__ import annotations

import copy
import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from grounded_apply.repositories import SQLiteRepository
from grounded_apply.services.searches import SearchExecutionError, SearchIntegrityError, SearchService, _event
from grounded_apply.services.workflow import canonical, digest
from tests.test_discovery import FixedTransportError
from tests.test_materials import SyntheticRenderer, approved_fixture
from tests.test_netflix_source import JOBS_SITEMAP_URL, ROBOTS_URL, job_url, page, posting, responses


class WindowTransport:
    def __init__(self, fixture):
        self.fixture = fixture
        self.responses = responses([(identifier, "2026-09-18") for identifier in range(1001, 1016)])
        self.calls = []
        self.on_get = None

    def get(self, url, *, max_bytes, timeout):
        self.fixture.assertEqual(self.fixture.active_repositories, 0)
        self.calls.append(url)
        if self.on_get is not None:
            self.on_get(url)
        result = self.responses[url]
        if isinstance(result, BaseException):
            raise result
        return result


class SearchWindowTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="gapply-synthetic-search-windows-")
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "fictional.db"
        with SQLiteRepository(self.database).initialize() as repository:
            _, self.claim_ids = approved_fixture(repository)
        self.active_repositories = 0
        self.transport = WindowTransport(self)
        self.now = datetime(2026, 9, 20, 12, tzinfo=UTC)
        self.service = SearchService(self.factory, self.transport, SyntheticRenderer(), clock=lambda: self.now)

    @contextmanager
    def factory(self, read_only):
        with SQLiteRepository(self.database, existing_only=True, read_only=read_only).initialize() as repository:
            self.active_repositories += 1
            try:
                yield repository
            finally:
                self.active_repositories -= 1

    def configure(self, key="fictional-scope", **changes):
        spec = {"schema_version": 1, "sources": [{"id": "fictional-netflix", "provider": "netflix", "board": "netflix"}],
            "claim_ids": list(self.claim_ids), "title_contains": ["Research"], **changes}
        return self.service.configure(spec, idempotency_key=key)["search_id"]

    @staticmethod
    def window(report):
        return report["sources"][0]["window"]

    def test_filtered_first_window_advances_to_relevant_later_posting(self):
        self.transport.responses[job_url(1008)] = page(posting(1008, title="Fictional Research Engineer"))
        scope = self.configure()
        first = self.service.run(scope, idempotency_key="first")
        self.assertEqual(first["counts"]["draft"], 0)
        self.assertEqual(self.window(first)["progress"]["next"]["external_id"], "1007")
        self.assertEqual(first["sources"][0]["report"]["filtered_count"], 7)
        second = self.service.run(scope, idempotency_key="second")
        self.assertEqual(second["counts"]["draft"], 1)
        self.assertEqual(self.window(second)["after"]["external_id"], "1007")
        self.assertEqual(self.window(second)["progress"]["next"]["external_id"], "1013")
        self.assertEqual(self.transport.calls[10:14], [ROBOTS_URL, self.transport.calls[1], JOBS_SITEMAP_URL, job_url(1001)])
        self.assertEqual(self.transport.calls[14:20], [job_url(identifier) for identifier in range(1008, 1014)])
        self.assertFalse(second["coverage_complete"])
        third = self.service.run(scope, idempotency_key="third")
        self.assertTrue(self.window(third)["progress"]["cycle_complete"])
        self.assertIsNone(self.window(third)["progress"]["next"])
        self.assertFalse(third["coverage_complete"], "a complete tail cycle is not a full current board snapshot")

    def test_cursor_frozen_before_first_get_and_same_run_replay_does_not_advance(self):
        scope = self.configure()
        observed = []
        def inspect(url):
            if url == ROBOTS_URL:
                with self.factory(True) as repository:
                    run = repository.list_search_runs(scope)[-1]
                    state = json.loads(repository.list_search_events(run["id"])[-1]["state_json"])
                    observed.append(copy.deepcopy(state["sources"][0]["window"]))
        self.transport.on_get = inspect
        first = self.service.run(scope, idempotency_key="first")
        self.assertEqual(observed[0]["generation"], 1)
        self.assertIsNone(observed[0]["progress"])
        self.assertGreater(observed[0]["reserved_epoch"], 0)
        calls = len(self.transport.calls)
        replay = self.service.run(scope, idempotency_key="first")
        self.assertEqual(self.window(replay), self.window(first))
        self.assertEqual(len(self.transport.calls), calls)

    def test_interrupted_detail_retries_same_input_and_preserves_accounting(self):
        scope = self.configure()
        def interrupt(url):
            if url == job_url(1004):
                raise KeyboardInterrupt()
        self.transport.on_get = interrupt
        with self.assertRaises(KeyboardInterrupt):
            self.service.run(scope, idempotency_key="interrupted")
        with self.factory(True) as repository:
            run_id = repository.list_search_runs(scope)[0]["id"]
        before = self.service.get(run_id)
        self.assertIsNone(self.window(before)["progress"])
        self.assertEqual(before["requests_used"], 7)
        self.transport.on_get = None
        recovered = self.service.resume(run_id)
        self.assertEqual(self.window(recovered)["generation"], 1)
        self.assertEqual(self.window(recovered)["reserved_epoch"], self.window(before)["reserved_epoch"])
        self.assertEqual(recovered["requests_used"], 17)
        self.assertEqual(self.window(recovered)["progress"]["next"]["external_id"], "1007")

    def test_older_pending_completion_cannot_replace_newer_generation(self):
        scope = self.configure()
        self.transport.on_get = lambda url: (_ for _ in ()).throw(KeyboardInterrupt()) if url == ROBOTS_URL else None
        with self.assertRaises(KeyboardInterrupt):
            self.service.run(scope, idempotency_key="old")
        with self.factory(True) as repository:
            old_id = repository.list_search_runs(scope)[0]["id"]
        self.transport.on_get = None
        new = self.service.run(scope, idempotency_key="new")
        self.assertEqual(self.window(new)["generation"], 2)
        old = self.service.resume(old_id)
        self.assertEqual(self.window(old)["generation"], 1)
        latest = self.service.run(scope, idempotency_key="latest")
        self.assertEqual(self.window(latest)["base_run_id"], new["run_id"])
        self.assertEqual(self.window(latest)["base_generation"], 2)
        self.assertEqual(self.window(latest)["after"]["external_id"], "1007")
        self.assertGreater(self.window(old)["completed_epoch"], self.window(new)["completed_epoch"])

    def test_request_budget_does_not_consume_forbidden_unattempted_tail(self):
        scope = self.configure(max_requests=5)
        first = self.service.run(scope, idempotency_key="limited")
        self.assertEqual(first["requests_used"], 5)
        self.assertEqual(self.window(first)["progress"]["consumed_tail_count"], 1)
        self.assertEqual(self.window(first)["progress"]["next"]["external_id"], "1002")
        second = self.service.run(scope, idempotency_key="next")
        self.assertEqual(self.window(second)["after"]["external_id"], "1002")
        self.assertEqual(self.window(second)["progress"]["next"]["external_id"], "1003")

    def test_source_completion_failure_rolls_back_progress_and_jobs_together(self):
        scope = self.configure(title_contains=[])
        original = self.service._append
        def interrupt(repository, run_id, manifest, action, state, **kwargs):
            if action == "source_completed":
                raise RuntimeError("synthetic checkpoint interruption")
            return original(repository, run_id, manifest, action, state, **kwargs)
        with patch.object(self.service, "_append", side_effect=interrupt), self.assertRaises(SearchExecutionError) as failure:
            self.service.run(scope, idempotency_key="interrupted")
        report = self.service.get(failure.exception.run_id)
        self.assertIsNone(self.window(report)["progress"])
        with self.factory(True) as repository:
            self.assertEqual(len(repository.list_job_snapshots()), 1)
        resumed = self.service.resume(report["run_id"])
        self.assertEqual(self.window(resumed)["generation"], 1)
        self.assertEqual(resumed["counts"]["draft"], 7)

    def test_inventory_failure_keeps_cursor_and_successful_new_run_advances(self):
        scope = self.configure()
        first = self.service.run(scope, idempotency_key="first")
        saved = self.transport.responses[ROBOTS_URL]
        self.transport.responses[ROBOTS_URL] = FixedTransportError("forbidden")
        failed = self.service.run(scope, idempotency_key="failed")
        self.assertEqual(self.window(failed)["after"], self.window(failed)["progress"]["next"])
        self.assertIsNone(self.window(failed)["progress"]["indexed_count"])
        self.transport.responses[ROBOTS_URL] = saved
        third = self.service.run(scope, idempotency_key="third")
        self.assertEqual(self.window(third)["base_run_id"], failed["run_id"])
        self.assertEqual(self.window(third)["after"], self.window(first)["progress"]["next"])

    def test_copied_closed_database_retains_cursor_without_external_state(self):
        scope = self.configure()
        first = self.service.run(scope, idempotency_key="first")
        restored = self.database.with_name("restored-fictional.db")
        shutil.copyfile(self.database, restored)
        restored.chmod(0o600)
        self.database = restored
        next_run = self.service.run(scope, idempotency_key="after-restore")
        self.assertEqual(self.window(next_run)["base_run_id"], first["run_id"])
        self.assertEqual(self.window(next_run)["after"]["external_id"], "1007")

    def rehash(self, run_id, mutate):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute("SELECT * FROM search_run_events WHERE run_id=? ORDER BY position", (run_id,)).fetchall()
            connection.execute("DROP TRIGGER search_events_no_update")
            previous = None
            for row in rows:
                state = json.loads(row["state_json"])
                mutate(state)
                fingerprint = digest(_event(run_id, row["position"], row["at"], row["action"], state, previous))
                connection.execute("UPDATE search_run_events SET state_json=?,previous_sha256=?,event_sha256=? WHERE id=?",
                    (canonical(state), previous, fingerprint, row["id"]))
                previous = fingerprint
            connection.commit()

    def test_rehashed_cursor_or_generation_does_not_escape_scope_custody(self):
        scope = self.configure()
        first = self.service.run(scope, idempotency_key="first")
        second = self.service.run(scope, idempotency_key="second")
        def forge(state):
            window = state["sources"][0].get("window")
            if window is not None:
                window["base_progress_sha256"] = "0" * 64
        self.rehash(second["run_id"], forge)
        with self.assertRaises(SearchIntegrityError):
            self.service.get(second["run_id"])
        calls = len(self.transport.calls)
        with self.assertRaises(SearchExecutionError) as failure:
            self.service.run(scope, idempotency_key="third")
        self.assertIsInstance(failure.exception.__cause__, SearchIntegrityError)
        self.assertEqual(len(self.transport.calls), calls)
        self.assertNotEqual(first["run_id"], second["run_id"])

    def test_rehashed_reused_generation_is_rejected_even_with_canonical_window(self):
        scope = self.configure()
        self.service.run(scope, idempotency_key="first")
        second = self.service.run(scope, idempotency_key="second")
        def forge(state):
            window = state["sources"][0].get("window")
            if window is not None:
                window.update(generation=1, base_run_id=None, base_generation=None, base_progress_sha256=None, after=None)
                if window["progress"] is not None:
                    window["progress"]["after"] = None
        self.rehash(second["run_id"], forge)
        with self.assertRaises(SearchIntegrityError):
            self.service.get(second["run_id"])

    def test_legacy_source_checkpoint_bytes_remain_valid_and_new_run_starts_first_window(self):
        scope = self.configure()
        def legacy_start(run_id, index, owner, epoch):
            def change(state, manifest):
                state["sources"][index]["stage"] = "fetching"
                state["sources"][index]["attempts"] += 1
                state["inflight_bytes"] = 0
            return self.service._change(run_id, owner, epoch, "source_started", change)["sources"][index]
        with patch.object(self.service, "_start_source", side_effect=legacy_start):
            first = self.service.run(scope, idempotency_key="legacy")
        self.assertNotIn("window", first["sources"][0])
        with self.factory(True) as repository:
            original = repository.list_search_events(first["run_id"])
        second = self.service.run(scope, idempotency_key="windowed")
        self.assertEqual(self.window(second)["generation"], 1)
        self.assertIsNone(self.window(second)["after"])
        self.assertEqual(self.service.get(first["run_id"])["sources"], first["sources"])
        with self.factory(True) as repository:
            self.assertEqual(repository.list_search_events(first["run_id"]), original)

    def test_changed_scope_has_its_own_cursor(self):
        first = self.service.run(self.configure(), idempotency_key="first")
        changed = self.service.run(self.configure("changed-scope", title_contains=["Different"]), idempotency_key="first")
        self.assertEqual(self.window(changed)["generation"], 1)
        self.assertIsNone(self.window(changed)["after"])
        self.assertIsNone(self.window(changed)["base_run_id"])
        self.assertEqual(first["search_id"] == changed["search_id"], False)


if __name__ == "__main__":
    unittest.main()
