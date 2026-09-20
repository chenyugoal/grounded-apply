from __future__ import annotations

import shutil
import tempfile
import unittest
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from grounded_apply.repositories import SQLiteRepository
from grounded_apply.repositories.snapshots import validate_profile_snapshot
from grounded_apply.services.batches import BatchCapacityError, BatchService
from grounded_apply.services.material_models import MaterialValidationError
from grounded_apply.services.schedules import ScheduleCapacityError, ScheduleExecutionError, ScheduleLeaseLostError, ScheduleService
from grounded_apply.services.searches import SearchService
from grounded_apply.services.storage_limits import StorageCapacityError
from tests.test_discovery import ASHBY, GREENHOUSE, ashby_job, encoded, greenhouse_job
from tests.test_materials import SyntheticRenderer, approved_fixture
from tests.test_searches import SearchTransport


# Exercise real allocation failures with a small injected allowance. The
# production-capacity gate covers larger profiles without inflating this suite.
TEST_CAPACITY_BYTES = 16 * 1024 * 1024


class SizedRenderer:
    """Synthetic adapter with fixed per-job artifact sizes, including on replay."""
    def __init__(self, sizes=()):
        self.sizes = tuple(sizes)
        self.by_job = {}
        self.built = []
        self.on_render = None

    def result(self, structure):
        rendered = SyntheticRenderer().render(structure)
        return replace(rendered, pdf=rendered.pdf + b" " * self.by_job[structure.job_id])

    def render(self, structure):
        if structure.job_id not in self.by_job:
            self.by_job[structure.job_id] = self.sizes[len(self.by_job)] if len(self.by_job) < len(self.sizes) else 0
        self.built.append(structure.job_id)
        if self.on_render is not None:
            self.on_render(structure)
        return self.result(structure)

    def validate(self, structure, rendered):
        if self.result(structure) != rendered:
            raise MaterialValidationError("Synthetic material changed")


class ScheduleCapacityTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="gapply-synthetic-daily-capacity-")
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "fictional.db"
        with SQLiteRepository(self.database).initialize() as repository:
            _, self.claim_ids = approved_fixture(repository)
        self.active_repositories = 0
        self.now = datetime(2026, 10, 20, 12, tzinfo=UTC)
        self.transport = SearchTransport(self)
        self.renderer = SizedRenderer()
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

    def configure(self, *, sources=None):
        search_id = self.searches.configure({"schema_version": 1,
            "sources": sources or [{"id": "fictional-gh", "provider": "greenhouse", "board": "example"}],
            "claim_ids": list(self.claim_ids), "max_jobs": 2}, idempotency_key="fictional-search")["search_id"]
        schedule_id = self.schedules.configure({"schema_version": 1, "search_id": search_id, "timezone": "UTC",
            "local_time": "09:00", "start_date": "2026-10-20"}, idempotency_key="fictional-daily")["schedule_id"]
        return search_id, schedule_id

    def material_ids(self):
        with self.factory(True) as repository:
            return repository.list_material_ids()

    def assert_capacity_partial(self, result):
        self.assertEqual(result["status"], "budget_exhausted")
        self.assertEqual(result["stop_reason"], "capacity_reached")
        self.assertEqual(result["child"]["stop_reason"], "capacity_reached")
        self.assertEqual(result["child"]["batch"]["stop_reason"], "capacity_reached")
        self.assertEqual(result["child"]["counts"], {"total": 2, "queued": 0, "building": 1, "draft": 1, "blocked": 0})
        self.assertEqual(len(result["notification"]["delta"]["new_materials"]), 1)
        self.assertEqual(result["notification"]["delta"]["run_health_change"]["current"]["status"], "capacity_reached")
        self.assertFalse(result["application_ready"])
        self.assertFalse(result["approvals_recorded"])
        self.assertLessEqual(self.database.stat().st_size, TEST_CAPACITY_BYTES)
        validate_profile_snapshot(self.database.read_bytes())

    @patch("grounded_apply.services.schedules.MAX_SNAPSHOT_BYTES", TEST_CAPACITY_BYTES)
    def test_schedule_reserve_mid_material_keeps_partial_draft_notice_and_bounded_retries(self):
        self.renderer.sizes = (TEST_CAPACITY_BYTES - 2 * 1024 * 1024, 1024 * 1024)
        _, schedule_id = self.configure()
        first = self.schedules.tick(schedule_id)
        self.assert_capacity_partial(first)
        identifiers, calls = self.material_ids(), len(self.transport.calls)
        self.assertEqual(len(identifiers), 1)
        before = self.database.read_bytes()
        self.assertEqual(self.schedules.get(schedule_id)["child"]["items"][0]["material_id"], identifiers[0])
        self.assertEqual(self.database.read_bytes(), before)
        restored = self.database.with_name("restored.db")
        shutil.copy2(self.database, restored)
        self.database = restored
        self.assertEqual(self.schedules.get(schedule_id)["notification"], first["notification"])
        second = self.schedules.tick(schedule_id)
        self.assertEqual(second["stop_reason"], "capacity_reached")
        self.assertEqual(second["run_id"], first["run_id"])
        self.assertEqual(self.material_ids(), identifiers)
        self.assertEqual(len(self.transport.calls), calls)
        third = self.schedules.tick(schedule_id)
        self.assertEqual(third["status"], "retry_limit")
        builds = len(self.renderer.built)
        self.assertFalse(self.schedules.tick(schedule_id)["executed"])
        self.assertEqual(len(self.renderer.built), builds)
        self.assertEqual(len(self.transport.calls), calls)
        self.assertLessEqual(self.database.stat().st_size, TEST_CAPACITY_BYTES)
        validate_profile_snapshot(self.database.read_bytes())

    @patch("grounded_apply.services.batches.MAX_SNAPSHOT_BYTES", TEST_CAPACITY_BYTES)
    def test_material_allowance_mid_run_rolls_back_only_later_artifact(self):
        self.renderer.sizes = (0, TEST_CAPACITY_BYTES + 1024 * 1024)
        _, schedule_id = self.configure()
        result = self.schedules.tick(schedule_id)
        self.assert_capacity_partial(result)
        self.assertEqual(len(self.material_ids()), 1)

    def test_committed_child_before_capacity_checkpoint_reconciles_without_rerender(self):
        _, schedule_id = self.configure()
        original = BatchService._checkpoint
        def full(service, batch, item, state, owner, epoch):
            if state["stage"] == "draft" and len(service._repository.list_material_ids()) == 2:
                raise BatchCapacityError("Synthetic parent checkpoint capacity")
            return original(service, batch, item, state, owner, epoch)
        with patch.object(BatchService, "_checkpoint", full):
            partial = self.schedules.tick(schedule_id)
        self.assert_capacity_partial(partial)
        identifiers = self.material_ids()
        self.assertEqual(len(identifiers), 2)
        calls, builds = len(self.transport.calls), len(self.renderer.built)
        completed = self.schedules.tick(schedule_id)
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["child"]["counts"]["draft"], 2)
        self.assertEqual(self.material_ids(), identifiers)
        self.assertEqual((len(self.transport.calls), len(self.renderer.built)), (calls, builds))

    def test_source_capture_capacity_preserves_prior_source_and_retries_only_uncommitted_source(self):
        self.transport.responses[GREENHOUSE] = encoded({"jobs": [greenhouse_job(1)]})
        self.transport.responses[ASHBY] = encoded({"apiVersion": "1", "jobs": [ashby_job("fictional-two")]})
        _, schedule_id = self.configure(sources=[
            {"id": "fictional-gh", "provider": "greenhouse", "board": "example"},
            {"id": "fictional-ashby", "provider": "ashby", "board": "example"}])
        original = self.schedules._capacity
        def full(repository, *, reserve=True):
            if reserve and len(repository.list_job_snapshots()) > 2:
                raise ScheduleCapacityError("Synthetic source checkpoint capacity")
            original(repository, reserve=reserve)
        with patch.object(self.schedules, "_capacity", full):
            result = self.schedules.tick(schedule_id)
        self.assertEqual(result["stop_reason"], "capacity_reached")
        self.assertEqual(result["child"]["stop_reason"], "capacity_reached")
        self.assertEqual([source["stage"] for source in result["child"]["sources"]], ["complete", "fetching"])
        self.assertIsNone(result["child"]["batch_id"])
        self.assertEqual(result["notification"]["delta"]["new_materials"], [])
        with self.factory(True) as repository:
            self.assertEqual(len(repository.list_job_snapshots()), 2)
        recovered = self.schedules.tick(schedule_id)
        self.assertEqual(recovered["child"]["counts"]["draft"], 2)
        self.assertEqual([call[0] for call in self.transport.calls], [GREENHOUSE, ASHBY, ASHBY])

    def test_capacity_before_child_binding_has_fixed_notice_and_no_fabricated_child(self):
        search_id, schedule_id = self.configure()
        original = self.schedules._capacity
        def full(repository, *, reserve=True):
            runs = repository.list_search_runs(search_id)
            if reserve and any(repository.get_scheduled_search_link(run["id"]) is None for run in runs):
                raise ScheduleCapacityError("Synthetic binding capacity")
            original(repository, reserve=reserve)
        with patch.object(self.schedules, "_capacity", full):
            result = self.schedules.tick(schedule_id)
        self.assertEqual(result["status"], "budget_exhausted")
        self.assertEqual(result["stop_reason"], "capacity_reached")
        self.assertIsNone(result["run_id"])
        self.assertIsNone(result["child"])
        self.assertEqual(result["notification"]["delta"]["new_materials"], [])
        self.assertIsNone(result["notification"]["delta"]["run_health_change"]["current"]["coverage_complete"])
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.material_ids(), ())
        self.assertEqual(self.schedules.tick(schedule_id)["child"]["counts"]["draft"], 2)

    def _assert_accounting_capacity(self, action, dispatched):
        _, schedule_id = self.configure()
        original, failed = SearchService._change, False
        def account(service, run_id, owner, epoch, event, change, **kwargs):
            nonlocal failed
            if event == action and not failed:
                failed = True
                with patch.object(self.schedules, "_capacity", side_effect=ScheduleCapacityError("Synthetic request accounting capacity")):
                    return original(service, run_id, owner, epoch, event, change, **kwargs)
            return original(service, run_id, owner, epoch, event, change, **kwargs)
        with patch.object(SearchService, "_change", account):
            result = self.schedules.tick(schedule_id)
        self.assertTrue(failed)
        self.assertEqual(result["stop_reason"], "capacity_reached")
        self.assertEqual(result["child"]["stop_reason"], "capacity_reached")
        self.assertEqual(result["child"]["requests_used"], dispatched)
        self.assertEqual(len(self.transport.calls), dispatched)
        self.assertEqual(self.material_ids(), ())
        recovered = self.schedules.tick(schedule_id)
        self.assertEqual(recovered["child"]["counts"]["draft"], 2)
        self.assertEqual(recovered["child"]["requests_used"], dispatched + 1)

    def test_reservation_capacity_stops_before_get_and_preserves_retry(self):
        self._assert_accounting_capacity("request_reserved", 0)

    def test_settlement_capacity_retains_charged_request_and_preserves_retry(self):
        self._assert_accounting_capacity("request_settled", 1)

    @patch("grounded_apply.services.schedules.MAX_SNAPSHOT_BYTES", TEST_CAPACITY_BYTES)
    def test_pause_and_reenable_fences_old_owner_before_oversized_commit(self):
        self.renderer.sizes = (TEST_CAPACITY_BYTES - 2 * 1024 * 1024, 1024 * 1024)
        _, schedule_id = self.configure()
        def revoke(structure):
            if len(self.renderer.built) == 2:
                self.schedules.set_enabled(schedule_id, False, idempotency_key="pause-during-render")
                self.schedules.set_enabled(schedule_id, True, idempotency_key="resume-during-render")
        self.renderer.on_render = revoke
        with self.assertRaises(ScheduleExecutionError) as failure:
            self.schedules.tick(schedule_id)
        causes, cause = [], failure.exception
        while cause is not None:
            causes.append(cause)
            cause = cause.__cause__
        self.assertTrue(any(isinstance(cause, ScheduleLeaseLostError) for cause in causes))
        self.assertFalse(any(isinstance(cause, StorageCapacityError) for cause in causes))
        self.assertEqual(len(self.material_ids()), 1)
        shown = self.schedules.get(schedule_id)
        self.assertNotEqual(shown["stop_reason"], "capacity_reached")
        self.assertFalse(shown["child"]["lease_active"])
        self.assertLessEqual(self.database.stat().st_size, TEST_CAPACITY_BYTES)


if __name__ == "__main__":
    unittest.main()
