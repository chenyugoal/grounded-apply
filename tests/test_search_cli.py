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
from grounded_apply.repositories.discovery_http import PublicJobTransportError
from scripts.check_search import ASHBY, FAILED_SOURCE, GREENHOUSE, SOURCES, fixture_responses
from tests.test_materials import SyntheticRenderer, approved_fixture


class FixtureTransport:
    def __init__(self) -> None:
        self.responses = fixture_responses()
        self.responses[GREENHOUSE]["body"]["jobs"] = self.responses[GREENHOUSE]["body"]["jobs"][:2]
        self.responses[GREENHOUSE]["body"]["meta"]["total"] = 2
        self.responses[ASHBY]["body"]["jobs"] = self.responses[ASHBY]["body"]["jobs"][:2]
        self.calls: list[str] = []

    def get(self, url: str, *, max_bytes: int, timeout: float) -> bytes:
        self.calls.append(url)
        if url not in self.responses:
            raise AssertionError("Unexpected synthetic source request")
        response = self.responses[url]
        if "error" in response:
            raise PublicJobTransportError(response["error"])
        payload = json.dumps(response["body"]).encode()
        assert len(payload) <= max_bytes and timeout > 0
        return payload


class SearchCliTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="gapply-synthetic-search-cli-")
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name) / "fictional-home"
        environment = patch.dict(os.environ, {"GROUNDED_APPLY_HOME": str(self.home)})
        environment.start()
        self.addCleanup(environment.stop)
        self.transport = FixtureTransport()
        transport = patch("grounded_apply.repositories.discovery_http.PublicJobHTTPTransport", return_value=self.transport)
        transport.start()
        self.addCleanup(transport.stop)
        renderer = patch("grounded_apply.repositories.latex_renderer.LatexResumeRenderer", return_value=SyntheticRenderer())
        renderer.start()
        self.addCleanup(renderer.stop)

    def invoke(self, *args: str, spec: object = None) -> tuple[int, dict, str]:
        output, errors = io.StringIO(), io.StringIO()
        with patch("sys.stdin", io.StringIO(json.dumps(spec))), redirect_stdout(output), redirect_stderr(errors):
            code = main(["--log-events", *args, "--json"])
        return code, json.loads(output.getvalue()) if output.getvalue() else {}, errors.getvalue()

    def manifest(self, claim_ids: tuple[str, ...] = ("fictional-claim",), *, partial: bool = False) -> dict:
        return {"schema_version": 1, "sources": SOURCES if partial else SOURCES[:2],
            "claim_ids": list(claim_ids), "title_contains": ["Python Engineer"], "max_jobs": 2}

    def initialize(self, *, partial: bool = False) -> dict:
        self.assertEqual(self.invoke("profile", "init")[0], 0)
        with SQLiteRepository(resolve_runtime_paths().database).initialize() as repository:
            _, claim_ids = approved_fixture(repository)
        return self.manifest(claim_ids, partial=partial)

    def configure(self, spec: dict, *extra: str, key: str = "fictional-search-scope") -> tuple[int, dict, str]:
        return self.invoke("searches", "configure", "--spec-file", "-", "--idempotency-key", key, *extra, spec=spec)

    def save(self, *, partial: bool = False) -> str:
        code, result, _ = self.configure(self.initialize(partial=partial))
        self.assertEqual(code, 0, result)
        return result["data"]["search_id"]

    def run_search(self, search_id: str, *extra: str, key: str = "fictional-search-run") -> tuple[int, dict, str]:
        return self.invoke("searches", "run", "--search-id", search_id, "--idempotency-key", key, *extra)

    def test_invalid_input_and_budgets_precede_runtime_and_network(self) -> None:
        for spec in ({}, {**self.manifest(), "schema_version": True},
                     {**self.manifest(), "max_jobs": 51}, {**self.manifest(), "claim_ids": []},
                     {**self.manifest(), "sensitive_answers": {"sponsorship": False}}):
            with self.subTest(spec=spec), patch("grounded_apply.cli._open_initialized_profile_repository") as opened:
                code, result, _ = self.configure(spec)
                self.assertEqual(code, 2)
                self.assertFalse(result["ok"])
                opened.assert_not_called()
        for arguments in (("--max-items", "0"), ("--max-seconds", "3601")):
            with patch("grounded_apply.cli._open_initialized_profile_repository") as opened:
                self.assertEqual(self.run_search("fictional-search", *arguments)[0], 2)
                opened.assert_not_called()
        self.assertEqual(self.transport.calls, [])
        self.assertFalse(self.home.exists())

    def test_configuration_preview_needs_no_runtime_and_does_not_fetch(self) -> None:
        with patch("grounded_apply.cli._open_initialized_profile_repository") as opened:
            code, result, _ = self.configure(self.manifest(), "--dry-run")
        self.assertEqual(code, 0, result)
        self.assertTrue(result["data"]["dry_run"])
        opened.assert_not_called()
        self.assertEqual(self.transport.calls, [])
        self.assertFalse(self.home.exists())

    def test_scope_replay_is_immutable_and_scopes_are_read_only(self) -> None:
        spec = self.initialize()
        code, original, _ = self.configure(spec)
        self.assertEqual(code, 0, original)
        self.assertEqual(self.configure(spec)[1]["data"]["search_id"], original["data"]["search_id"])
        changed = {**spec, "max_jobs": 3}
        self.assertEqual(self.configure(changed)[0], 2)
        before = resolve_runtime_paths().database.read_bytes()
        code, listed, _ = self.invoke("searches", "scopes")
        self.assertEqual(code, 0)
        self.assertEqual(len(listed["data"]["searches"]), 1)
        self.assertEqual(resolve_runtime_paths().database.read_bytes(), before)
        self.assertEqual(self.transport.calls, [])

    def test_real_parsers_partial_source_and_unknown_question_coverage(self) -> None:
        search_id = self.save(partial=True)
        code, result, events = self.run_search(search_id)
        self.assertEqual(code, 2, result)
        data = result["data"]
        self.assertFalse(data["coverage_complete"])
        self.assertEqual({source["report"]["status"] for source in data["sources"]}, {"successful", "failed"})
        self.assertEqual(data["batch"]["counts"]["draft"], 2)
        self.assertTrue(all(item["questionnaire_coverage"] == "unknown" for item in data["batch"]["items"]))
        self.assertFalse(data["application_ready"])
        self.assertFalse(data["external_action_taken"])
        self.assertIn(FAILED_SOURCE, self.transport.calls)
        with SQLiteRepository(resolve_runtime_paths().database, read_only=True).initialize() as repository:
            self.assertEqual(len(repository.list_job_snapshots()), 5)
            self.assertEqual(repository.list_applications(), [])
            self.assertTrue(all(repository.get_material_approval(identifier) is None for identifier in repository.list_material_ids()))
        for forbidden in ("Avery", "Quill", "fictional-search", "example.com", str(self.home), "source_text"):
            self.assertNotIn(forbidden, events)

    def test_budget_resume_and_same_run_replay_do_not_refetch(self) -> None:
        search_id = self.save()
        code, first, _ = self.run_search(search_id, "--max-items", "1")
        self.assertEqual(code, 2, first)
        self.assertEqual(first["data"]["batch"]["remaining_count"], 1)
        requests = list(self.transport.calls)
        run_id = first["data"]["run_id"]
        code, resumed, _ = self.invoke("searches", "resume", "--run-id", run_id)
        self.assertEqual(code, 0, resumed)
        self.assertEqual(resumed["data"]["batch"]["counts"]["draft"], 2)
        self.assertEqual(self.transport.calls, requests)
        code, repeated, _ = self.run_search(search_id)
        self.assertEqual(code, 0, repeated)
        self.assertEqual(repeated["data"]["run_id"], run_id)
        self.assertEqual(self.transport.calls, requests)
        self.assertEqual([item["material_id"] for item in repeated["data"]["batch"]["items"]],
                         [item["material_id"] for item in resumed["data"]["batch"]["items"]])

    def test_new_run_moves_past_prior_drafts_before_selection_cap(self) -> None:
        search_id = self.save()
        code, first, _ = self.run_search(search_id)
        self.assertEqual(code, 0, first)
        code, second, _ = self.run_search(search_id, key="fictional-second-run")
        self.assertEqual(code, 0, second)
        first_jobs = {item["job_id"] for item in first["data"]["batch"]["items"]}
        second_jobs = {item["job_id"] for item in second["data"]["batch"]["items"]}
        self.assertEqual(len(first_jobs), 2)
        self.assertEqual(len(second_jobs), 2)
        self.assertTrue(first_jobs.isdisjoint(second_jobs))
        with SQLiteRepository(resolve_runtime_paths().database, read_only=True).initialize() as repository:
            self.assertEqual(len(repository.list_material_ids()), 4)

    def test_show_and_list_validate_without_writes_or_network(self) -> None:
        search_id = self.save(partial=True)
        _, result, _ = self.run_search(search_id)
        before, requests = resolve_runtime_paths().database.read_bytes(), list(self.transport.calls)
        self.assertEqual(self.invoke("searches", "show", "--run-id", result["data"]["run_id"])[0], 0)
        code, listed, _ = self.invoke("searches", "list", "--search-id", search_id)
        self.assertEqual(code, 0)
        self.assertEqual(len(listed["data"]["runs"]), 1)
        self.assertEqual(resolve_runtime_paths().database.read_bytes(), before)
        self.assertEqual(self.transport.calls, requests)

    def test_review_export_is_read_only_and_diagnostic_events_are_fixed(self) -> None:
        search_id = self.save(partial=True)
        _, report, _ = self.run_search(search_id)
        run_id = report["data"]["run_id"]
        destination = self.home.parent / "private-review"
        arguments = ("searches", "export", "--run-id", run_id, "--output-dir", str(destination))
        before, requests = resolve_runtime_paths().database.read_bytes(), list(self.transport.calls)
        self.assertEqual(self.invoke(*arguments, "--dry-run")[0], 0)
        self.assertFalse(destination.exists())
        code, result, events = self.invoke(*arguments)
        self.assertEqual(code, 0, result)
        self.assertEqual(result["data"]["material_count"], 2)
        self.assertFalse(result["data"]["application_ready"])
        index = json.loads((destination / "review.json").read_text())
        self.assertFalse(index["coverage_complete"])
        self.assertTrue(all(item["requires_approval"] for item in index["items"]))
        self.assertTrue(self.invoke(*arguments)[1]["data"]["replayed"])
        self.assertEqual(resolve_runtime_paths().database.read_bytes(), before)
        self.assertEqual(self.transport.calls, requests)
        self.assertEqual(json.loads(events.splitlines()[-1])["command"], "searches.export")
        for forbidden in ("Avery", "Quill", "fictional-search", "example.com", str(destination)):
            self.assertNotIn(forbidden, events)

    def test_review_export_output_loss_recovers_exact_folder_without_search_resume(self) -> None:
        from grounded_apply.cli import _emit
        search_id = self.save()
        _, report, _ = self.run_search(search_id)
        destination = self.home.parent / "private-review"
        arguments = ("searches", "export", "--run-id", report["data"]["run_id"],
                     "--output-dir", str(destination))
        failed = False

        def fail_once(args, **kwargs):
            nonlocal failed
            if kwargs["command"] == "searches.export" and kwargs.get("error") is None and not failed:
                failed = True
                raise OSError("fictional private output failure")
            return _emit(args, **kwargs)

        with patch("grounded_apply.cli._emit", side_effect=fail_once):
            code, result, events = self.invoke(*arguments)
        self.assertEqual(code, 2)
        self.assertNotIn("fictional private", json.dumps(result) + events)
        self.assertIn("Retry the same run and destination", result["error"]["message"])
        self.assertEqual(json.loads(events.splitlines()[-1])["outcome"], "failed")
        self.assertTrue(self.invoke(*arguments)[1]["data"]["replayed"])

    def test_review_export_rejects_runtime_destination_without_changes(self) -> None:
        search_id = self.save()
        _, report, _ = self.run_search(search_id)
        destination = self.home / "private-review"
        before = resolve_runtime_paths().database.read_bytes()
        code, result, _ = self.invoke("searches", "export", "--run-id", report["data"]["run_id"],
                                      "--output-dir", str(destination))
        self.assertEqual(code, 2, result)
        self.assertFalse(destination.exists())
        self.assertEqual(resolve_runtime_paths().database.read_bytes(), before)

    def test_output_failure_has_fixed_recovery_and_exact_retry(self) -> None:
        from grounded_apply.cli import _emit
        search_id = self.save()
        failed = False

        def fail_once(args, **kwargs):
            nonlocal failed
            if kwargs["command"] == "searches.run" and kwargs.get("error") is None and not failed:
                failed = True
                raise OSError("fictional private output failure")
            return _emit(args, **kwargs)

        with patch("grounded_apply.cli._emit", side_effect=fail_once):
            code, result, events = self.run_search(search_id)
        self.assertEqual(code, 2)
        self.assertNotIn("fictional private", json.dumps(result) + events)
        self.assertEqual(json.loads(events.splitlines()[-1])["outcome"], "search_outcome_unknown")
        requests = list(self.transport.calls)
        self.assertEqual(self.run_search(search_id)[0], 0)
        self.assertEqual(self.transport.calls, requests)

    def test_interruption_preserves_content_free_recovery(self) -> None:
        search_id = self.save()
        with patch("grounded_apply.services.searches.SearchService.run", side_effect=KeyboardInterrupt):
            code, _, events = self.run_search(search_id)
        self.assertEqual(code, 130)
        self.assertEqual(json.loads(events.splitlines()[-1])["outcome"], "search_outcome_unknown")
        self.assertIn("may already", json.loads(events.splitlines()[-1])["recovery"])

    def test_overlapping_invocation_reports_lease_without_claiming_shared_failure(self) -> None:
        from grounded_apply.services.searches import SearchLeaseActiveError, SearchService
        search_id = self.save()
        _, first, _ = self.run_search(search_id, "--max-items", "1")
        run_id = first["data"]["run_id"]
        before, requests = resolve_runtime_paths().database.read_bytes(), list(self.transport.calls)
        with patch.object(SearchService, "resume", side_effect=SearchLeaseActiveError(run_id)):
            code, result, _ = self.invoke("searches", "resume", "--run-id", run_id)
        self.assertEqual(code, 2, result)
        self.assertEqual(result["data"]["run_id"], run_id)
        self.assertEqual(result["data"]["stop_reason"], "lease_active")
        self.assertNotEqual(result["data"]["status"], "failed")
        self.assertIsNone(result.get("error"))
        self.assertEqual(resolve_runtime_paths().database.read_bytes(), before)
        self.assertEqual(self.transport.calls, requests)

    def test_shared_failure_preserves_validated_progress_for_resume(self) -> None:
        from grounded_apply.services.materials import MaterialDependencyError, MaterialService
        search_id = self.save()
        original, calls = MaterialService.build, 0

        def fail_second(service, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise MaterialDependencyError("fictional private dependency path")
            return original(service, *args, **kwargs)

        with patch.object(MaterialService, "build", fail_second):
            code, result, events = self.run_search(search_id)
        self.assertEqual(code, 2, result)
        self.assertEqual(result["data"]["batch"]["counts"]["draft"], 1)
        self.assertNotIn("fictional private", json.dumps(result) + events)
        requests = list(self.transport.calls)
        code, resumed, _ = self.invoke("searches", "resume", "--run-id", result["data"]["run_id"])
        self.assertEqual(code, 0, resumed)
        self.assertEqual(resumed["data"]["batch"]["counts"]["draft"], 2)
        self.assertEqual(self.transport.calls, requests)

    def test_shared_integrity_failure_does_not_return_cached_items(self) -> None:
        from grounded_apply.services.searches import SearchService
        from grounded_apply.repositories import RepositoryError
        search_id = self.save()
        _, first, _ = self.run_search(search_id, "--max-items", "1")
        run_id = first["data"]["run_id"]
        with patch.object(SearchService, "resume", side_effect=RepositoryError("fictional private failure")), \
             patch.object(SearchService, "get", side_effect=RepositoryError("fictional private corruption")):
            code, result, events = self.invoke("searches", "resume", "--run-id", run_id)
        self.assertEqual(code, 2, result)
        self.assertEqual(result["data"]["run_id"], run_id)
        self.assertFalse(result["data"]["review_available"])
        self.assertNotIn("batch", result["data"])
        self.assertNotIn("fictional private", json.dumps(result) + events)


if __name__ == "__main__":
    unittest.main()
