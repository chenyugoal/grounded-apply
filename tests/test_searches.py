from __future__ import annotations

import json
import copy
import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

from grounded_apply.repositories import RepositoryError, SQLiteRepository
from grounded_apply.services.applications import ApplicationService
from grounded_apply.services.batches import BatchService
from grounded_apply.services.jobs import JobService
from grounded_apply.services.materials import MaterialService
from grounded_apply.services.profile_lifecycle import ProfileLifecycleService
from grounded_apply.services.searches import (
    SearchCapacityError, SearchExecutionError, SearchIntegrityError, SearchLeaseActiveError,
    SearchLeaseLostError, SearchService, _event as search_event, validate_search_manifest,
)
from grounded_apply.services.workflow import digest
from tests.test_batches import RecordingRenderer
from tests.test_discovery import ASHBY, GREENHOUSE, ashby_job, encoded, greenhouse_job
from tests.test_materials import SyntheticRenderer, approved_fixture


class SearchTransport:
    def __init__(self, fixture) -> None:
        self.fixture = fixture
        self.calls = []
        self.on_get = None
        self.responses = {GREENHOUSE: encoded({"jobs": [greenhouse_job(1), greenhouse_job(2)]}),
            ASHBY: encoded({"apiVersion": "1", "jobs": [ashby_job("fictional-1"), ashby_job("fictional-2")]})}

    def get(self, url: str, *, max_bytes: int, timeout: float) -> bytes:
        self.fixture.assertEqual(self.fixture.active_repositories, 0, "runtime must be closed during every public request")
        self.fixture.assertNotIn("Avery", url)
        self.fixture.assertNotIn("claim", url)
        self.calls.append((url, max_bytes, timeout))
        if self.on_get is not None:
            self.on_get()
        result = self.responses[url]
        if isinstance(result, BaseException):
            raise result
        return result


class SearchTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="gapply-synthetic-searches-")
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "fictional.db"
        with SQLiteRepository(self.database).initialize() as repository:
            self.manual_job, self.claim_ids = approved_fixture(repository)
        self.active_repositories = 0
        self.renderer = RecordingRenderer()
        self.transport = SearchTransport(self)
        self.service = SearchService(self.factory, self.transport, self.renderer)

    @contextmanager
    def factory(self, read_only: bool):
        with SQLiteRepository(self.database, existing_only=True, read_only=read_only).initialize() as repository:
            self.active_repositories += 1
            try:
                yield repository
            finally:
                self.active_repositories -= 1

    def manifest(self, **changes):
        return {"schema_version": 1, "sources": [{"id": "fictional-gh", "provider": "greenhouse", "board": "example"}],
            "claim_ids": list(self.claim_ids), "max_jobs": 2, **changes}

    def configure(self, spec=None, key="fictional-scope"):
        return self.service.configure(self.manifest() if spec is None else spec, idempotency_key=key)["search_id"]

    def start(self, spec=None, key="fictional-run"):
        return self.service.run(self.configure(spec), idempotency_key=key)

    def ids(self, table):
        with self.factory(True) as repository:
            return {"jobs": lambda: tuple(record["id"] for record in repository.list_job_snapshots()),
                "materials": repository.list_material_ids, "runs": lambda: tuple(record["id"] for record in repository.list_search_runs()),
                "batches": lambda: tuple(record["id"] for record in repository.list_preparation_batches())}[table]()

    def test_manifest_normalizes_closed_choices_and_preview_never_opens_runtime(self) -> None:
        normalized = validate_search_manifest(self.manifest())
        self.assertEqual(validate_search_manifest(normalized), normalized)
        with patch.object(self.service, "_open", side_effect=AssertionError("must not open")):
            preview = self.service.configure(self.manifest(), idempotency_key="preview", dry_run=True)
        self.assertFalse(preview["profile_validated"])
        self.assertEqual(self.transport.calls, [])
        for changed in ({"max_jobs": 51}, {"max_requests": True}, {"max_bytes": 0},
            {"title_contains": [" untrimmed"]}, {"secret_answer": "fictional"}, {"schema_version": True}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                validate_search_manifest(self.manifest(**changed))

    def test_configuration_and_runs_are_bound_to_exact_scopes(self) -> None:
        scope = self.configure()
        self.assertEqual(self.configure(), scope)
        with self.assertRaises(RepositoryError):
            self.configure(self.manifest(max_jobs=3))
        first = self.service.run(scope, idempotency_key="same-run")
        calls = len(self.transport.calls)
        repeated = self.service.run(scope, idempotency_key="same-run")
        self.assertEqual(repeated["run_id"], first["run_id"])
        self.assertEqual(len(self.transport.calls), calls)
        self.assertEqual(len(self.ids("materials")), 2)
        before = self.database.read_bytes()
        self.assertEqual(len(self.service.list(scope)), 1)
        self.assertEqual(len(self.service.list_searches()), 1)
        self.assertEqual(self.database.read_bytes(), before)

    def test_round_robin_sources_and_manual_outage_coverage_are_explicit(self) -> None:
        sources = self.manifest()["sources"] + [{"id": "fictional-ashby", "provider": "ashby", "board": "example"},
            {"id": "fictional-gap", "provider": "manual", "careers_url": "https://example.com/careers"}]
        result = self.start(self.manifest(sources=sources))
        self.assertEqual([item["source_id"] for item in result["selection"]], ["fictional-gh", "fictional-ashby"])
        self.assertFalse(result["coverage_complete"])
        self.assertEqual(result["sources"][2]["report"]["status"], "manual_required")
        self.assertEqual(result["counts"]["draft"], 2)
        self.assertEqual(result["requests_used"], 2)
        self.assertFalse(result["approvals_recorded"])
        self.assertFalse(result["application_ready"])

    def test_empty_success_and_failed_source_are_distinct_durable_runs(self) -> None:
        self.transport.responses[GREENHOUSE] = encoded({"jobs": []})
        scope = self.configure()
        empty = self.service.run(scope, idempotency_key="empty")
        self.assertEqual(empty["status"], "completed")
        self.assertTrue(empty["coverage_complete"])
        self.assertIsNone(empty["batch_id"])
        self.transport.responses[GREENHOUSE] = RuntimeError("untrusted remote payload must be redacted")
        failed = self.service.run(scope, idempotency_key="outage")
        self.assertFalse(failed["coverage_complete"])
        self.assertEqual(failed["sources"][0]["report"]["status"], "failed")
        self.assertNotIn("untrusted remote", json.dumps(failed))
        self.assertEqual(len(self.ids("runs")), 2)
        self.assertEqual(len(self.ids("jobs")), 1)

    def test_prior_current_drafts_are_skipped_before_final_preparation_cap(self) -> None:
        self.transport.responses[GREENHOUSE] = encoded({"jobs": [greenhouse_job(index) for index in range(1, 7)]})
        scope = self.configure()
        first = self.service.run(scope, idempotency_key="first")
        second = self.service.run(scope, idempotency_key="second")
        self.assertEqual(second["skipped"]["unchanged_draft"], 2)
        self.assertEqual(second["counts"]["draft"], 2)
        self.assertFalse({item["job_id"] for item in first["items"]} & {item["job_id"] for item in second["items"]})
        self.assertEqual(len(self.ids("materials")), 4)

    def test_changed_content_and_changed_scope_can_prepare_existing_postings(self) -> None:
        self.transport.responses[GREENHOUSE] = encoded({"jobs": [greenhouse_job(1)]})
        scope = self.configure()
        first = self.service.run(scope, idempotency_key="first")
        changed = greenhouse_job(1)
        changed["content"] += "<p>Additional fictional role context.</p>"
        self.transport.responses[GREENHOUSE] = encoded({"jobs": [changed]})
        second = self.service.run(scope, idempotency_key="second")
        self.assertNotEqual(first["selection"][0]["job_id"], second["selection"][0]["job_id"])
        changed_scope = self.configure(self.manifest(claim_ids=list(reversed(self.claim_ids))), key="changed-scope")
        third = self.service.run(changed_scope, idempotency_key="first")
        self.assertEqual(third["selection"][0]["job_id"], second["selection"][0]["job_id"])
        self.assertNotEqual(third["items"][0]["material_id"], second["items"][0]["material_id"])

    def test_retired_fact_prevents_prior_draft_skip_and_new_material_reuse(self) -> None:
        self.transport.responses[GREENHOUSE] = encoded({"jobs": [greenhouse_job(1)]})
        scope = self.configure()
        self.service.run(scope, idempotency_key="before")
        with self.factory(False) as repository:
            lifecycle = ProfileLifecycleService(repository)
            preview = lifecycle.retire(self.claim_ids[0], actor_id="synthetic-user", idempotency_key="retire")
            lifecycle.retire(self.claim_ids[0], actor_id="synthetic-user", idempotency_key="retire", confirm=True, preview_token=preview.preview_token)
        result = self.service.run(scope, idempotency_key="after")
        self.assertEqual(result["skipped"]["unchanged_draft"], 0)
        self.assertEqual(result["counts"]["blocked"], 1)
        self.assertEqual(len(self.ids("materials")), 1)

    def test_source_capture_and_checkpoint_rollback_atomically_then_resume(self) -> None:
        scope = self.configure()
        original = self.service._append
        def interrupted(repository, run_id, manifest, action, state, **kwargs):
            if action == "source_completed":
                raise RuntimeError("synthetic interruption after capture before checkpoint")
            return original(repository, run_id, manifest, action, state, **kwargs)
        with patch.object(self.service, "_append", side_effect=interrupted), self.assertRaises(SearchExecutionError) as failure:
            self.service.run(scope, idempotency_key="source-crash")
        self.assertEqual(len(self.ids("jobs")), 1)
        paused = self.service.get(failure.exception.run_id)
        self.assertEqual(paused["requests_used"], 1)
        self.assertEqual(paused["sources"][0]["stage"], "fetching")
        resumed = self.service.resume(failure.exception.run_id)
        self.assertEqual(resumed["counts"]["draft"], 2)
        self.assertEqual(resumed["requests_used"], 2)
        self.assertEqual(len(self.ids("jobs")), 3)

    def test_source_checkpoint_recovery_does_not_repeat_network(self) -> None:
        scope = self.configure()
        with patch.object(self.service, "_freeze", side_effect=RuntimeError("synthetic stop after source")), self.assertRaises(SearchExecutionError) as failure:
            self.service.run(scope, idempotency_key="freeze-crash")
        self.assertEqual(len(self.ids("jobs")), 3)
        calls = len(self.transport.calls)
        recovered = self.service.resume(failure.exception.run_id)
        self.assertEqual(recovered["counts"]["draft"], 2)
        self.assertEqual(len(self.transport.calls), calls)

    def test_child_commit_before_parent_checkpoint_reuses_child_and_materials(self) -> None:
        scope = self.configure()
        original = self.service._change
        def interrupted(run_id, owner, epoch, action, change, **kwargs):
            if action == "child_progress":
                raise RuntimeError("synthetic parent interruption")
            return original(run_id, owner, epoch, action, change, **kwargs)
        with patch.object(self.service, "_change", side_effect=interrupted), self.assertRaises(SearchExecutionError) as failure:
            self.service.run(scope, idempotency_key="child-crash")
        material_ids, batches, calls = self.ids("materials"), self.ids("batches"), len(self.transport.calls)
        recovered = self.service.resume(failure.exception.run_id)
        self.assertEqual(recovered["status"], "completed")
        self.assertEqual(self.ids("materials"), material_ids)
        self.assertEqual(self.ids("batches"), batches)
        self.assertEqual(len(self.transport.calls), calls)
        self.assertEqual(len(self.renderer.built), 2)

    def test_request_charge_survives_process_interruption_before_response(self) -> None:
        scope = self.configure(self.manifest(max_requests=1))
        self.transport.responses[GREENHOUSE] = SystemExit("synthetic abrupt stop")
        with self.assertRaises(SystemExit):
            self.service.run(scope, idempotency_key="request-crash")
        run_id = self.ids("runs")[0]
        paused = self.service.get(run_id)
        self.assertEqual(paused["requests_used"], 1)
        self.assertEqual(paused["bytes_charged"], 16 * 1024 * 1024)
        recovered = self.service.resume(run_id)
        self.assertEqual(len(self.transport.calls), 1)
        self.assertFalse(recovered["coverage_complete"])
        self.assertEqual(recovered["sources"][0]["stage"], "deferred")
        self.assertEqual(recovered["stop_reason"], "request_budget")

    def test_small_response_budget_is_enforced_even_when_adapter_returns_too_much(self) -> None:
        result = self.start(self.manifest(max_bytes=1))
        self.assertEqual(result["bytes_charged"], 1)
        self.assertEqual(result["sources"][0]["report"]["error"], "response_too_large")
        self.assertEqual(self.ids("materials"), ())

    def test_overlap_refused_at_search_scope_and_network_has_no_open_runtime(self) -> None:
        scope = self.configure()
        def overlap():
            self.transport.on_get = None
            with self.assertRaises(SearchLeaseActiveError):
                self.service.run(scope, idempotency_key="overlap")
            with self.factory(True) as repository:
                active = repository.get_search_lease(scope)
                events = repository.list_search_events(active["run_id"])
                self.assertEqual(json.loads(events[-1]["state_json"])["requests_used"], 1)
        self.transport.on_get = overlap
        result = self.service.run(scope, idempotency_key="first")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(self.transport.calls), 1)

    def test_expired_network_owner_cannot_settle_or_capture_after_takeover(self) -> None:
        moment = [datetime.now(UTC)]
        self.service = SearchService(self.factory, self.transport, self.renderer, clock=lambda: moment[0])
        scope = self.configure()
        def takeover():
            self.transport.on_get = None
            moment[0] += timedelta(seconds=1000)
            run_id = self.ids("runs")[0]
            completed = self.service.resume(run_id)
            self.assertEqual(completed["status"], "completed")
        self.transport.on_get = takeover
        with self.assertRaises(SearchExecutionError) as failure:
            self.service.run(scope, idempotency_key="expired", max_seconds=1)
        self.assertIsInstance(failure.exception.__cause__, SearchLeaseLostError)
        self.assertEqual(self.service.get(failure.exception.run_id)["status"], "completed")
        self.assertEqual(len(self.ids("materials")), 2)

    def test_search_owned_batch_cannot_bypass_parent_on_standalone_resume(self) -> None:
        result = self.start()
        with self.factory(False) as repository, self.assertRaisesRegex(ValueError, "saved search"):
            BatchService(repository, MaterialService(repository, self.renderer)).run(result["batch_id"])

    def test_parent_lease_expiring_during_render_prevents_child_commit(self) -> None:
        moment = [datetime.now(UTC)]
        self.transport.responses[GREENHOUSE] = encoded({"jobs": [greenhouse_job(1)]})
        self.service = SearchService(self.factory, self.transport, self.renderer, clock=lambda: moment[0])
        scope = self.configure()
        def expire(structure):
            self.renderer.on_render = None
            moment[0] += timedelta(seconds=1000)
        self.renderer.on_render = expire
        with self.assertRaises(SearchExecutionError) as failure:
            self.service.run(scope, idempotency_key="expired-parent", max_seconds=1)
        self.assertIsInstance(failure.exception.__cause__, SearchLeaseLostError)
        self.assertEqual(self.ids("materials"), ())
        recovered = self.service.resume(failure.exception.run_id)
        self.assertEqual(recovered["status"], "completed")
        self.assertEqual(len(self.ids("materials")), 1)

    def test_capture_capacity_rolls_back_source_window_and_remains_resumable(self) -> None:
        scope = self.configure()
        original = self.service._append
        def full(repository, run_id, manifest, action, state, **kwargs):
            if action == "source_completed":
                raise SearchCapacityError("synthetic capacity")
            return original(repository, run_id, manifest, action, state, **kwargs)
        with patch.object(self.service, "_append", side_effect=full):
            stopped = self.service.run(scope, idempotency_key="capacity")
        self.assertEqual(stopped["stop_reason"], "capacity_reached")
        self.assertEqual(len(self.ids("jobs")), 1)
        resumed = self.service.resume(stopped["run_id"])
        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(len(self.ids("jobs")), 3)

    def test_runtime_guard_error_inside_transport_accounting_is_not_source_outage(self) -> None:
        scope = self.configure()
        original = self.service._change
        def guarded(run_id, owner, epoch, action, change, **kwargs):
            if action == "request_reserved":
                raise RepositoryError("synthetic private runtime guard")
            return original(run_id, owner, epoch, action, change, **kwargs)
        with patch.object(self.service, "_change", side_effect=guarded), self.assertRaises(SearchExecutionError) as failure:
            self.service.run(scope, idempotency_key="guarded")
        state = self.service.get(failure.exception.run_id)
        self.assertEqual(state["status"], "failed")
        self.assertIsNone(state["sources"][0]["report"])
        self.assertEqual(self.transport.calls, [])

    def test_rehashed_action_history_cannot_refund_consumed_bytes_without_reservation(self) -> None:
        scope = self.configure()
        self.transport.responses[GREENHOUSE] = SystemExit("synthetic stop")
        with self.assertRaises(SystemExit):
            self.service.run(scope, idempotency_key="accounting-crash")
        run_id = self.ids("runs")[0]
        with self.factory(False) as repository, repository.transaction():
            _, manifest, state = self.service._validated(repository, run_id)
            state["bytes_charged"], state["inflight_bytes"] = 50, 0
            self.service._append(repository, run_id, manifest, "request_settled", state)
            state["stop_reason"] = "shared_failure"
            self.service._append(repository, run_id, manifest, "stopped", state)
            original = repository.list_search_events(run_id)
        malicious = copy.deepcopy(original)
        previous_hash = malicious[-1]["event_sha256"]
        for action, charged, inflight in (("resumed", 50, 50), ("request_settled", 0, 0)):
            state = {**state, "stop_reason": None, "bytes_charged": charged, "inflight_bytes": inflight}
            position = len(malicious)
            at = malicious[-1]["at"]
            event_hash = digest({"run_id": run_id, "position": position, "at": at,
                "action": action, "state": state, "previous_sha256": previous_hash})
            malicious.append({"id": str(uuid5(NAMESPACE_URL, f"{run_id}/event/{position}")), "run_id": run_id,
                "position": position, "at": at, "action": action, "state_json": json.dumps(state),
                "previous_sha256": previous_hash, "event_sha256": event_hash})
            previous_hash = event_hash
        with self.factory(True) as repository, patch.object(repository, "list_search_events", return_value=malicious):
            with self.assertRaises(SearchIntegrityError):
                self.service._history(repository, run_id, manifest)
        self.assertEqual(self.service.get(run_id)["bytes_charged"], 50)

    def test_rehashed_complete_phase_cannot_conceal_unfinished_batch(self) -> None:
        scope = self.configure()
        result = self.service.run(scope, idempotency_key="incomplete", max_items=1)
        self.assertEqual(result["remaining_count"], 1)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute("SELECT * FROM search_run_events WHERE run_id=? ORDER BY position", (result["run_id"],)).fetchall()
            connection.execute("DROP TRIGGER search_events_no_update")
            previous, completing = None, False
            for row in rows:
                state = json.loads(row["state_json"])
                completing = completing or row["action"] == "child_progress"
                if completing:
                    state["phase"] = "complete"
                fingerprint = digest(search_event(result["run_id"], row["position"], row["at"], row["action"], state, previous))
                connection.execute("UPDATE search_run_events SET state_json=?,previous_sha256=?,event_sha256=? WHERE id=?",
                    (json.dumps(state), previous, fingerprint, row["id"]))
                previous = fingerprint
            connection.commit()
        with self.assertRaises(SearchIntegrityError):
            self.service.get(result["run_id"])
        with self.assertRaises(SearchIntegrityError):
            self.service.resume(result["run_id"])

    def test_corrupt_checkpoint_and_immutable_scope_fail_closed(self) -> None:
        result = self.start()
        with closing(sqlite3.connect(self.database)) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE saved_searches SET manifest_json = '{}' ")
            connection.execute("DROP TRIGGER search_events_no_update")
            connection.execute("UPDATE search_run_events SET event_sha256 = ? WHERE run_id = ?", ("0" * 64, result["run_id"]))
            connection.commit()
        calls = len(self.transport.calls)
        with self.assertRaises(SearchIntegrityError):
            self.service.resume(result["run_id"])
        self.assertEqual(len(self.transport.calls), calls)

    def _record_applied(self, job_id: str) -> None:
        with self.factory(False) as repository:
            materials = MaterialService(repository, SyntheticRenderer())
            applications = ApplicationService(repository, materials)
            app = applications.add(job_id, actor_id="synthetic-user", idempotency_key="synthetic-app-" + job_id)
            material = materials.build(job_id, self.claim_ids, idempotency_key="synthetic-manual-material-" + job_id)
            materials.approve(material["material_id"], bundle_sha256=material["bundle_sha256"], actor_id="synthetic-user",
                idempotency_key="synthetic-approved-" + job_id, confirm=True)
            for state in ("shortlisted", "preparing", "ready_for_review", "applied", "withdrawn"):
                kwargs = {"actor_id": "synthetic-user", "idempotency_key": app["application_id"] + state}
                if state in {"ready_for_review", "applied"}:
                    kwargs["material_id"] = material["material_id"]
                if state == "applied":
                    kwargs["confirm_submitted"] = True
                preview = applications.transition(app["application_id"], state, **kwargs)
                applications.transition(app["application_id"], state, **kwargs, confirm=True, preview_token=preview["preview_token"])

    def test_historical_submission_is_rechecked_at_material_commit(self) -> None:
        self.transport.responses[GREENHOUSE] = encoded({"jobs": [greenhouse_job(1)]})
        def applied_during_render(structure):
            self.renderer.on_render = None
            self._record_applied(structure.job_id)
        self.renderer.on_render = applied_during_render
        result = self.start()
        self.assertEqual(result["counts"]["blocked"], 1)
        self.assertEqual(result["items"][0]["blockers"][0]["reason"], "already_applied")
        self.assertIsNone(result["items"][0]["material_id"])
        self.assertEqual(len(self.ids("materials")), 1)
        subsequent = self.service.run(result["search_id"], idempotency_key="after-withdrawal")
        self.assertEqual(subsequent["skipped"]["already_applied"], 1)
        self.assertIsNone(subsequent["batch_id"])


if __name__ == "__main__":
    unittest.main()
