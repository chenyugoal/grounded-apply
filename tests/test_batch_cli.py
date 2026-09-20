from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from grounded_apply.cli import main
from grounded_apply.config import resolve_runtime_paths
from grounded_apply.repositories import SQLiteRepository
from grounded_apply.services.jobs import JobService
from tests.test_materials import SyntheticRenderer, approved_fixture


class BatchCliTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name) / "fictional-home"
        environment = patch.dict(os.environ, {"GROUNDED_APPLY_HOME": str(self.home)})
        environment.start()
        self.addCleanup(environment.stop)
        renderer = patch("grounded_apply.repositories.latex_renderer.LatexResumeRenderer",
                         return_value=SyntheticRenderer())
        renderer.start()
        self.addCleanup(renderer.stop)

    def invoke(self, *args: str, spec: object = None) -> tuple[int, dict, str]:
        output, errors = io.StringIO(), io.StringIO()
        with patch("sys.stdin", io.StringIO(json.dumps(spec))), redirect_stdout(output), redirect_stderr(errors):
            code = main(["--log-events", *args, "--json"])
        return code, json.loads(output.getvalue()) if output.getvalue() else {}, errors.getvalue()

    def initialize(self, *, blocked: bool = False) -> dict:
        self.assertEqual(self.invoke("profile", "init")[0], 0)
        with SQLiteRepository(resolve_runtime_paths().database).initialize() as repository:
            first, claim_ids = approved_fixture(repository)
            second = JobService(repository).add("https://example.com/jobs/second",
                "Fictional Engineer\nRequirements\nPython", idempotency_key="fictional-second")["job_id"]
        jobs = [{"job_id": first}, {"job_id": second}]
        if blocked:
            jobs[0]["claim_ids"] = ["fictional-missing-claim"]
        return {"schema_version": 1, "claim_ids": list(claim_ids), "jobs": jobs}

    def prepare(self, spec: dict, *extra: str) -> tuple[int, dict, str]:
        return self.invoke("batches", "prepare", "--spec-file", "-",
                           "--idempotency-key", "fictional-batch", *extra, spec=spec)

    def test_invalid_input_and_budgets_do_not_open_runtime(self) -> None:
        for spec, extra in (({}, ()), ({"schema_version": True}, ()),
            ({"schema_version": 1, "claim_ids": ["fictional-claim"],
              "jobs": [{"job_id": "fictional-job"}]}, ("--max-items", "0"))):
            with self.subTest(spec=spec), patch("grounded_apply.cli._open_initialized_profile_repository") as opened:
                code, result, _ = self.prepare(spec, *extra)
                self.assertEqual(code, 2)
                self.assertFalse(result["ok"])
                opened.assert_not_called()
        self.assertFalse(self.home.exists())

    def test_preview_checks_evidence_without_rendering_or_writes(self) -> None:
        spec = self.initialize()
        database = resolve_runtime_paths().database
        before = database.read_bytes()
        with patch.object(SyntheticRenderer, "render", side_effect=AssertionError("must not render")):
            code, result, _ = self.prepare(spec, "--dry-run")
        self.assertEqual(code, 0, result)
        self.assertTrue(result["data"]["dry_run"])
        self.assertEqual(database.read_bytes(), before)
        self.assertEqual(self.invoke("batches", "list")[1]["data"]["batches"], [])

    def test_blocked_item_does_not_stop_later_job_and_review_is_read_only(self) -> None:
        spec = self.initialize(blocked=True)
        code, result, events = self.prepare(spec)
        self.assertEqual(code, 3, result)
        self.assertEqual(result["data"]["counts"]["draft"], 1)
        self.assertEqual(result["data"]["counts"]["blocked"], 1)
        self.assertFalse(result["data"]["external_action_taken"])
        self.assertFalse(result["data"]["approvals_recorded"])
        before = resolve_runtime_paths().database.read_bytes()
        show_code, reviewed, _ = self.invoke("batches", "show", "--batch-id", result["data"]["batch_id"])
        self.assertEqual(show_code, 0, reviewed)
        self.assertEqual(resolve_runtime_paths().database.read_bytes(), before)
        for forbidden in ("Avery", "Quill", "example.com", str(self.home), "fictional-missing-claim"):
            self.assertNotIn(forbidden, events)

    def test_budget_stop_resume_and_repeated_prepare_reuse_materials(self) -> None:
        spec = self.initialize()
        code, first, _ = self.prepare(spec, "--max-items", "1")
        self.assertEqual(code, 2, first)
        self.assertEqual(first["data"]["remaining_count"], 1)
        batch_id = first["data"]["batch_id"]
        code, resumed, _ = self.invoke("batches", "resume", "--batch-id", batch_id)
        self.assertEqual(code, 0, resumed)
        original = [item["material_id"] for item in resumed["data"]["items"]]
        code, replay, _ = self.prepare(spec)
        self.assertEqual(code, 0, replay)
        self.assertEqual(replay["data"]["batch_id"], batch_id)
        self.assertEqual([item["material_id"] for item in replay["data"]["items"]], original)
        with SQLiteRepository(resolve_runtime_paths().database, read_only=True).initialize() as repository:
            self.assertEqual(len(repository.list_material_ids()), 2)

    def test_output_failure_preserves_content_free_recovery_and_exact_retry(self) -> None:
        from grounded_apply.cli import _emit
        spec = self.initialize()
        failed = False

        def fail_once(args, **kwargs):
            nonlocal failed
            if kwargs["command"] == "batches.prepare" and kwargs.get("error") is None and not failed:
                failed = True
                raise OSError("fictional private output failure")
            return _emit(args, **kwargs)

        with patch("grounded_apply.cli._emit", side_effect=fail_once):
            code, result, events = self.prepare(spec)
        self.assertEqual(code, 2)
        self.assertIn("same spec", result["error"]["message"])
        self.assertNotIn("fictional private", json.dumps(result) + events)
        self.assertEqual(json.loads(events.splitlines()[-1])["outcome"], "batch_outcome_unknown")
        self.assertEqual(self.prepare(spec)[0], 0)
        with SQLiteRepository(resolve_runtime_paths().database, read_only=True).initialize() as repository:
            self.assertEqual(len(repository.list_material_ids()), 2)

    def test_interrupt_recovery_does_not_claim_no_progress(self) -> None:
        spec = self.initialize()
        with patch("grounded_apply.services.batches.BatchService.run", side_effect=KeyboardInterrupt):
            code, _, events = self.prepare(spec)
        self.assertEqual(code, 130)
        event = json.loads(events.splitlines()[-1])
        self.assertEqual(event["outcome"], "batch_outcome_unknown")
        self.assertIn("may already be saved", event["recovery"])
        self.assertEqual(self.prepare(spec)[0], 0)

    def test_shared_failure_returns_validated_progress_and_resumes(self) -> None:
        from grounded_apply.services.materials import MaterialDependencyError, MaterialService
        spec = self.initialize()
        original = MaterialService.build
        calls = 0

        def fail_second(service, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise MaterialDependencyError("fictional private dependency path")
            return original(service, *args, **kwargs)

        with patch.object(MaterialService, "build", fail_second):
            code, result, events = self.prepare(spec)
        self.assertEqual(code, 2, result)
        self.assertFalse(result["ok"])
        self.assertEqual(result["data"]["counts"]["draft"], 1)
        self.assertEqual(result["data"]["remaining_count"], 1)
        self.assertEqual(result["data"]["stop_reason"], "shared_failure")
        self.assertIn("PDF environment", result["error"]["message"])
        self.assertNotIn("fictional private", json.dumps(result) + events)
        code, resumed, _ = self.invoke("batches", "resume", "--batch-id", result["data"]["batch_id"])
        self.assertEqual(code, 0, resumed)
        self.assertEqual(resumed["data"]["counts"]["draft"], 2)
        self.assertEqual(resumed["data"]["items"][0]["material_id"], result["data"]["items"][0]["material_id"])

    def test_shared_integrity_failure_never_returns_unvalidated_items(self) -> None:
        from grounded_apply.services.batches import BatchService
        from grounded_apply.repositories import RepositoryError
        spec = self.initialize()
        # Create normally, then simulate an adapter that can no longer validate
        # the persisted state. Recovery must not expose the last cached report.
        _, created, _ = self.prepare(spec, "--max-items", "1")
        with patch.object(BatchService, "run", side_effect=RepositoryError("fictional private failure")), \
             patch.object(BatchService, "get", side_effect=RepositoryError("fictional private corruption")):
            code, result, events = self.invoke("batches", "resume", "--batch-id", created["data"]["batch_id"])
        self.assertEqual(code, 2, result)
        self.assertEqual(result["data"]["batch_id"], created["data"]["batch_id"])
        self.assertFalse(result["data"]["review_available"])
        self.assertFalse(result["data"]["application_ready"])
        self.assertNotIn("items", result["data"])
        self.assertNotIn("fictional private", json.dumps(result) + events)


if __name__ == "__main__":
    unittest.main()
