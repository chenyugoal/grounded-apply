from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from grounded_apply.cli import main
from grounded_apply.config import resolve_runtime_paths
from grounded_apply.repositories import SQLiteRepository
from scripts.check_search import ASHBY, GREENHOUSE, SOURCES
from tests.test_materials import SyntheticRenderer, approved_fixture
from tests.test_search_cli import FixtureTransport


class ScheduleCliTests(unittest.TestCase):
    def setUp(self) -> None:
        from grounded_apply.services.schedules import ScheduleService
        self.service_class = ScheduleService
        self.now = datetime(2026, 9, 20, 8, tzinfo=UTC)
        directory = tempfile.TemporaryDirectory(prefix="gapply-synthetic-schedule-cli-")
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name) / "fictional-home"
        environment = patch.dict(os.environ, {"GROUNDED_APPLY_HOME": str(self.home)})
        environment.start()
        self.addCleanup(environment.stop)
        self.transport = FixtureTransport()
        self.transport.responses[GREENHOUSE]["body"]["jobs"] = self.transport.responses[GREENHOUSE]["body"]["jobs"][:1]
        self.transport.responses[GREENHOUSE]["body"]["meta"]["total"] = 1
        self.transport.responses[ASHBY]["body"]["jobs"] = self.transport.responses[ASHBY]["body"]["jobs"][:1]
        patches = [patch("grounded_apply.repositories.discovery_http.PublicJobHTTPTransport", return_value=self.transport),
            patch("grounded_apply.repositories.latex_renderer.LatexResumeRenderer", return_value=SyntheticRenderer()),
            patch("grounded_apply.services.schedules.ScheduleService",
                side_effect=lambda *args, **kwargs: ScheduleService(*args, **kwargs, clock=lambda: self.now))]
        for patched in patches:
            patched.start()
            self.addCleanup(patched.stop)

    def invoke(self, *args: str, spec: object = None) -> tuple[int, dict, str]:
        output, errors = io.StringIO(), io.StringIO()
        with patch("sys.stdin", io.StringIO(json.dumps(spec))), redirect_stdout(output), redirect_stderr(errors):
            code = main(["--log-events", *args, "--json"])
        return code, json.loads(output.getvalue()) if output.getvalue() else {}, errors.getvalue()

    def manifest(self, search_id: str = "fictional-search", **changes) -> dict:
        return {"schema_version": 1, "search_id": search_id, "timezone": "UTC", "local_time": "09:00",
            "start_date": "2026-09-20", **changes}

    def configure(self, spec: dict, *extra: str, key: str = "fictional-daily") -> tuple[int, dict, str]:
        return self.invoke("schedules", "configure", "--spec-file", "-", "--idempotency-key", key, *extra, spec=spec)

    def save(self, *, partial: bool = False, max_items: int = 20) -> str:
        self.assertEqual(self.invoke("profile", "init")[0], 0)
        with SQLiteRepository(resolve_runtime_paths().database).initialize() as repository:
            _, claims = approved_fixture(repository)
        search_spec = {"schema_version": 1, "sources": SOURCES if partial else SOURCES[:2],
            "claim_ids": list(claims), "max_jobs": 2}
        code, search, _ = self.invoke("searches", "configure", "--spec-file", "-",
            "--idempotency-key", "fictional-scope", spec=search_spec)
        self.assertEqual(code, 0, search)
        code, schedule, _ = self.configure(self.manifest(search["data"]["search_id"], max_items=max_items))
        self.assertEqual(code, 0, schedule)
        return schedule["data"]["schedule_id"]

    def tick(self, identifier: str, *extra: str) -> tuple[int, dict, str]:
        return self.invoke("schedules", "tick", "--schedule-id", identifier, *extra)

    def ack(self, identifier: str, notification: str) -> tuple[int, dict, str]:
        return self.invoke("schedules", "ack", "--schedule-id", identifier, "--notification-id", notification,
            "--idempotency-key", "ack-" + notification)

    def test_invalid_manifest_and_identifiers_precede_runtime_and_network(self) -> None:
        for invalid in ({}, self.manifest(max_items=True), self.manifest(max_seconds=3601),
            self.manifest(max_attempts=0), self.manifest(timezone="../UTC"), self.manifest(local_time="24:00"),
            self.manifest(start_date="1999-01-01"), self.manifest(sensitive_answer="fictional")):
            with self.subTest(invalid=invalid), patch("grounded_apply.cli._open_initialized_profile_repository") as opened:
                code, result, _ = self.configure(invalid)
                self.assertEqual(code, 2)
                self.assertFalse(result["ok"])
                opened.assert_not_called()
        with patch("grounded_apply.cli._open_initialized_profile_repository") as opened:
            self.assertEqual(self.tick("../fictional")[0], 2)
            opened.assert_not_called()
        self.assertFalse(self.home.exists())
        self.assertEqual(self.transport.calls, [])

    def test_syntax_preview_creates_no_runtime_or_network(self) -> None:
        with patch("grounded_apply.cli._open_initialized_profile_repository") as opened:
            code, result, _ = self.configure(self.manifest(), "--dry-run")
        self.assertEqual(code, 0, result)
        self.assertTrue(result["data"]["dry_run"])
        opened.assert_not_called()
        self.assertFalse(self.home.exists())
        self.assertEqual(self.transport.calls, [])

    def test_missing_schedule_tick_preview_is_failed_without_mutation(self) -> None:
        self.save()
        before = resolve_runtime_paths().database.read_bytes()
        code, result, events = self.tick("fictional-missing-schedule", "--dry-run")
        self.assertEqual(code, 2, result)
        self.assertFalse(result["ok"])
        self.assertFalse(result["data"]["review_available"])
        self.assertEqual(result["data"]["schedule_id"], "fictional-missing-schedule")
        self.assertNotIn("child", result["data"])
        self.assertNotEqual(json.loads(events.splitlines()[-1])["outcome"], "schedule_outcome_unknown")
        self.assertEqual(resolve_runtime_paths().database.read_bytes(), before)
        self.assertEqual(self.transport.calls, [])

    def test_configuration_replay_and_read_only_due_preview(self) -> None:
        identifier = self.save()
        code, shown, _ = self.invoke("schedules", "show", "--schedule-id", identifier)
        self.assertEqual(code, 0, shown)
        spec = shown["data"]["manifest"]
        self.assertEqual(self.configure(spec)[1]["data"]["schedule_id"], identifier)
        self.assertEqual(self.configure({**spec, "local_time": "10:00"})[0], 2)
        self.now += timedelta(hours=2)
        before = resolve_runtime_paths().database.read_bytes()
        with patch("grounded_apply.services.searches.SearchService.run") as run:
            code, preview, _ = self.tick(identifier, "--dry-run")
            self.assertEqual(code, 0, preview)
            self.assertFalse(preview["data"]["executed"])
            self.assertEqual(preview["data"]["due"]["action"], "start")
            self.assertEqual(self.invoke("schedules", "show", "--schedule-id", identifier)[0], 0)
            listed = self.invoke("schedules", "list")[1]["data"]["schedules"]
            self.assertEqual(len(listed), 1)
            run.assert_not_called()
        self.assertEqual(resolve_runtime_paths().database.read_bytes(), before)
        self.assertEqual(self.transport.calls, [])

    def test_exact_configuration_retry_after_start_day_preserves_saved_authorization(self) -> None:
        identifier = self.save()
        spec = self.invoke("schedules", "show", "--schedule-id", identifier)[1]["data"]["manifest"]
        self.now += timedelta(days=1)
        code, replay, _ = self.configure(spec)
        self.assertEqual(code, 0, replay)
        self.assertEqual(replay["data"]["schedule_id"], identifier)
        self.assertEqual(self.configure(spec, key="different-new-authorization")[0], 2)
        self.assertEqual(len(self.invoke("schedules", "list")[1]["data"]["schedules"]), 1)
        self.assertEqual(self.transport.calls, [])

    def test_daily_partial_source_is_explicit_and_diagnostics_keep_profile_private(self) -> None:
        identifier = self.save(partial=True)
        self.now += timedelta(hours=2)
        code, result, diagnostics = self.tick(identifier)
        self.assertEqual(code, 2, result)
        data = result["data"]
        self.assertTrue(data["executed"])
        self.assertFalse(data["child"]["coverage_complete"])
        self.assertEqual(data["child"]["counts"]["draft"], 2)
        self.assertTrue(all(item["questionnaire_coverage"] == "unknown" for item in data["child"]["items"]))
        self.assertFalse(data["external_action_taken"])
        self.assertFalse(data["application_ready"])
        self.assertFalse(data["child"]["approvals_recorded"])
        with SQLiteRepository(resolve_runtime_paths().database, read_only=True).initialize() as repository:
            self.assertEqual(repository.list_applications(), [])
            self.assertTrue(all(repository.get_material_approval(key) is None for key in repository.list_material_ids()))
        for forbidden in ("Avery", "Quill", "fictional-search", "example.com", str(self.home), "source_text"):
            self.assertNotIn(forbidden, diagnostics)

    def test_terminal_day_stable_notice_ack_and_unchanged_tomorrow_are_quiet(self) -> None:
        identifier = self.save()
        self.now += timedelta(hours=2)
        code, first, _ = self.tick(identifier)
        self.assertEqual(code, 0, first)
        notification = first["data"]["notification"]["id"]
        requests = list(self.transport.calls)
        repeated = self.tick(identifier)[1]["data"]
        self.assertFalse(repeated["executed"])
        self.assertEqual(repeated["run_id"], first["data"]["run_id"])
        self.assertEqual(repeated["notification"]["id"], notification)
        self.assertEqual(self.transport.calls, requests)
        self.assertEqual(self.ack(identifier, notification)[0], 0)
        self.assertEqual(self.ack(identifier, notification)[0], 0)
        self.assertFalse(self.tick(identifier)[1]["data"]["notify"])
        self.now += timedelta(days=1)
        code, tomorrow, _ = self.tick(identifier)
        self.assertEqual(code, 0, tomorrow)
        self.assertTrue(tomorrow["data"]["executed"])
        self.assertFalse(tomorrow["data"]["notify"])
        self.assertIsNone(tomorrow["data"]["notification"])
        self.assertEqual(len(self.transport.calls), len(requests) + 2)

    def test_item_budget_resumes_same_child_without_refetch(self) -> None:
        identifier = self.save(max_items=1)
        self.now += timedelta(hours=2)
        code, first, _ = self.tick(identifier)
        self.assertEqual(code, 2, first)
        self.assertEqual(first["data"]["child"]["remaining_count"], 1)
        requests = list(self.transport.calls)
        code, resumed, _ = self.tick(identifier)
        self.assertEqual(code, 0, resumed)
        self.assertEqual(resumed["data"]["run_id"], first["data"]["run_id"])
        self.assertEqual(resumed["data"]["attempts"], 2)
        self.assertEqual(resumed["data"]["child"]["remaining_count"], 0)
        self.assertEqual(self.transport.calls, requests)

    def test_pause_prevents_execution_and_resume_reuses_explicit_scope(self) -> None:
        identifier = self.save()
        self.assertEqual(self.invoke("schedules", "pause", "--schedule-id", identifier,
            "--idempotency-key", "pause")[0], 0)
        self.now += timedelta(days=3, hours=2)
        paused = self.tick(identifier)[1]["data"]
        self.assertFalse(paused["enabled"])
        self.assertFalse(paused["executed"])
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.invoke("schedules", "resume", "--schedule-id", identifier,
            "--idempotency-key", "resume")[0], 0)
        code, resumed, _ = self.tick(identifier)
        self.assertEqual(code, 0, resumed)
        self.assertTrue(resumed["data"]["enabled"])
        self.assertEqual(resumed["data"]["due"]["local_date"], "2026-09-23")
        self.assertEqual(len(self.transport.calls), 2)

    def test_output_loss_is_redacted_and_replaying_same_tick_does_not_duplicate_work(self) -> None:
        from grounded_apply.cli import _emit
        identifier = self.save()
        self.now += timedelta(hours=2)
        failed = False

        def fail_once(args, **kwargs):
            nonlocal failed
            if kwargs["command"] == "schedules.tick" and kwargs.get("error") is None and not failed:
                failed = True
                raise OSError("fictional private output failure")
            return _emit(args, **kwargs)

        with patch("grounded_apply.cli._emit", side_effect=fail_once):
            code, result, events = self.tick(identifier)
        self.assertEqual(code, 2)
        self.assertNotIn("fictional private", json.dumps(result) + events)
        self.assertEqual(json.loads(events.splitlines()[-1])["outcome"], "schedule_outcome_unknown")
        requests = list(self.transport.calls)
        code, repeated, _ = self.tick(identifier)
        self.assertEqual(code, 0, repeated)
        self.assertFalse(repeated["data"]["executed"])
        self.assertEqual(self.transport.calls, requests)

    def test_interrupt_reports_content_free_schedule_recovery(self) -> None:
        identifier = self.save()
        with patch.object(self.service_class, "tick", side_effect=KeyboardInterrupt):
            code, _, events = self.tick(identifier)
        self.assertEqual(code, 130)
        self.assertEqual(json.loads(events.splitlines()[-1])["outcome"], "schedule_outcome_unknown")
        self.assertIn("may already", json.loads(events.splitlines()[-1])["recovery"])

    def test_active_lease_is_pending_without_claiming_shared_failure(self) -> None:
        from grounded_apply.services.schedules import ScheduleLeaseActiveError
        identifier = self.save()
        before = resolve_runtime_paths().database.read_bytes()
        with patch.object(self.service_class, "tick", side_effect=ScheduleLeaseActiveError(identifier)):
            code, result, _ = self.tick(identifier)
        self.assertEqual(code, 2, result)
        self.assertEqual(result["data"]["stop_reason"], "lease_active")
        self.assertFalse(result["data"]["executed"])
        self.assertNotEqual(result["data"]["status"], "failed")
        self.assertIsNone(result.get("error"))
        self.assertEqual(resolve_runtime_paths().database.read_bytes(), before)
        self.assertEqual(self.transport.calls, [])

    def test_shared_failure_recovers_validated_progress_and_never_cached_private_output(self) -> None:
        identifier = self.save(max_items=1)
        self.now += timedelta(hours=2)
        _, initial, _ = self.tick(identifier)
        with patch.object(self.service_class, "tick", side_effect=RuntimeError("fictional private failure")):
            code, result, events = self.tick(identifier)
        self.assertEqual(code, 2, result)
        self.assertEqual(result["data"]["run_id"], initial["data"]["run_id"])
        self.assertEqual(result["data"]["child"]["counts"]["draft"], 1)
        self.assertEqual(result["data"]["stop_reason"], "shared_failure")
        self.assertNotIn("fictional private", json.dumps(result) + events)
        with patch.object(self.service_class, "tick", side_effect=RuntimeError("private")), \
             patch.object(self.service_class, "get", side_effect=ValueError("private")):
            code, unreadable, _ = self.tick(identifier)
        self.assertEqual(code, 2)
        self.assertFalse(unreadable["data"]["review_available"])
        self.assertNotIn("child", unreadable["data"])

    def test_failure_before_child_creation_notifies_with_schedule_only_review(self) -> None:
        identifier = self.save()
        self.now += timedelta(hours=2)
        with patch("grounded_apply.services.searches.SearchService.run", side_effect=RuntimeError("private pre-child failure")):
            code, result, events = self.tick(identifier)
        self.assertEqual(code, 2, result)
        self.assertTrue(result["data"]["notify"])
        self.assertIsNone(result["data"]["run_id"])
        self.assertIsNone(result["data"]["child"])
        self.assertIsNone(result["data"]["notification"]["run_id"])
        self.assertEqual(result["data"]["schedule_id"], identifier)
        self.assertNotIn("private pre-child", json.dumps(result) + events)
        self.assertEqual(self.transport.calls, [])


if __name__ == "__main__":
    unittest.main()
