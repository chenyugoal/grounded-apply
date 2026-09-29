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
from grounded_apply.repositories.discovery_http import _check_status
from scripts.check_onboarding import runtime_snapshot
from scripts.check_workable import (
    BOARD, EVENT_FIELDS, FAILED_FEED_URL, FEED_URL, INPUT_URLS,
    assert_source_gaps, fixture_responses,
)
from tests.test_materials import SyntheticRenderer, approved_fixture


class WorkableTransport:
    def __init__(self) -> None:
        self.responses = fixture_responses()
        self.calls: list[str] = []

    def get(self, url: str, *, max_bytes: int, timeout: float) -> bytes:
        self.calls.append(url)
        if url not in self.responses:
            raise AssertionError("Unexpected synthetic source request")
        response = self.responses[url]
        if "status" in response:
            _check_status(response["status"])
        payload = json.dumps(response["body"]).encode()
        assert len(payload) <= max_bytes and timeout > 0
        return payload


class WorkableCliTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="gapply-synthetic-workable-cli-")
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name) / "fictional-home"
        environment = patch.dict(os.environ, {"GROUNDED_APPLY_HOME": str(self.home)})
        environment.start()
        self.addCleanup(environment.stop)
        self.transport = WorkableTransport()
        transport = patch("grounded_apply.repositories.discovery_http.PublicJobHTTPTransport", return_value=self.transport)
        transport.start()
        self.addCleanup(transport.stop)
        renderer = patch("grounded_apply.repositories.latex_renderer.LatexResumeRenderer", return_value=SyntheticRenderer())
        renderer.start()
        self.addCleanup(renderer.stop)
        network = patch("socket.getaddrinfo", side_effect=AssertionError("Unexpected real network"))
        network.start()
        self.addCleanup(network.stop)

    def invoke(self, *arguments: str, spec: object = None, expected: int = 0) -> dict:
        output, errors = io.StringIO(), io.StringIO()
        with patch("sys.stdin", io.StringIO(json.dumps(spec))), redirect_stdout(output), redirect_stderr(errors):
            code = main(["--log-events", *arguments, "--json"])
        envelope = json.loads(output.getvalue())
        self.assertEqual(code, expected, envelope)
        self.assertEqual(envelope["ok"], expected == 0)
        for line in errors.getvalue().splitlines():
            self.assertEqual(set(json.loads(line)), EVENT_FIELDS)
        for private in ("Avery", "Quill", "example.com", "fictional-workable", "private-", str(self.home), "source_text"):
            self.assertNotIn(private, errors.getvalue())
        return envelope

    def sources(self) -> dict:
        arguments = [value for url in INPUT_URLS for value in ("--url", url)]
        return self.invoke("jobs", "sources", *arguments)["data"]

    def discover(self, manifest: dict, *extra: str) -> dict:
        return self.invoke("jobs", "discover", "--sources-file", "-", *extra, spec=manifest, expected=2)["data"]

    def save_search(self) -> str:
        manifest = self.sources()["manifest"]
        self.invoke("profile", "init")
        with SQLiteRepository(resolve_runtime_paths().database).initialize() as repository:
            _, claim_ids = approved_fixture(repository)
        spec = {"schema_version": 1, "sources": manifest["sources"], "claim_ids": list(claim_ids),
                "title_contains": ["Python Engineer"], "max_jobs": 2}
        return self.invoke("searches", "configure", "--spec-file", "-", "--idempotency-key", "fictional-workable-scope",
                           spec=spec)["data"]["search_id"]

    def run_search(self, search_id: str, *extra: str, key: str = "fictional-workable-run") -> dict:
        return self.invoke("searches", "run", "--search-id", search_id, "--idempotency-key", key,
                           *extra, expected=2)["data"]

    def test_links_deduplicate_and_boardless_job_remains_an_offline_manual_gap(self) -> None:
        report = self.sources()
        self.assertEqual(report["manifest"]["schema_version"], 1)
        self.assertEqual(report["automatic_source_count"], 2)
        self.assertEqual(report["manual_source_count"], 1)
        self.assertTrue(report["inputs"][1]["duplicate"])
        self.assertFalse(report["inputs"][3]["automatic"])
        self.assertEqual(report["network_requests"], 0)
        self.assertFalse(report["storage_changed"])
        self.assertFalse(report["live_boards_verified"])
        self.assertEqual(self.transport.calls, [])
        self.assertFalse(self.home.exists())

    def test_preview_preserves_siblings_geographic_state_and_location_visibility(self) -> None:
        data = self.discover(self.sources()["manifest"], "--dry-run")
        assert_source_gaps(data["sources"])
        self.assertEqual(len(data["jobs"]), 2)
        self.assertEqual(data["captures"], [])
        first, second = data["jobs"]
        self.assertIn("Fictional State", first["location"])
        self.assertIn("Fictional Region", second["location"])
        self.assertIn("Remote", second["location"])
        self.assertNotIn("private-", json.dumps(data))
        self.assertEqual(set(self.transport.calls), {FEED_URL, FAILED_FEED_URL})
        self.assertFalse(self.home.exists())

    def test_capture_requires_runtime_before_attempting_the_widget_request(self) -> None:
        manifest = self.sources()["manifest"]
        result = self.invoke("jobs", "discover", "--sources-file", "-", spec=manifest, expected=2)
        self.assertIsNone(result["data"])
        self.assertEqual(self.transport.calls, [])
        self.assertFalse(self.home.exists())

    def test_changed_record_creates_one_new_version_and_does_not_rewrite_originals(self) -> None:
        manifest = self.sources()["manifest"]
        self.invoke("profile", "init")
        original = self.discover(manifest)
        assert_source_gaps(original["sources"])
        identifiers = {item["external_id"]: item["job_id"] for item in original["captures"]}
        observations = {key: self.invoke("jobs", "show", "--job-id", value)["data"] for key, value in identifiers.items()}
        replay = self.discover(manifest)
        self.assertTrue(all(item["replayed"] and identifiers[item["external_id"]] == item["job_id"] for item in replay["captures"]))
        self.transport.responses = fixture_responses(changed=True)
        changed = self.discover(manifest)
        current = {item["external_id"]: item["job_id"] for item in changed["captures"]}
        self.assertNotEqual(current["A000000001"], identifiers["A000000001"])
        self.assertEqual(current["A000000002"], identifiers["A000000002"])
        self.assertEqual(len(self.invoke("jobs", "list")["data"]["jobs"]), 3)
        for key, identifier in identifiers.items():
            self.assertEqual(self.invoke("jobs", "show", "--job-id", identifier)["data"], observations[key])
        self.assertEqual(observations["A000000001"]["discovery"]["board"], BOARD)

    def test_budget_restart_and_new_run_keep_two_drafts_without_refetching_completed_work(self) -> None:
        search_id = self.save_search()
        first = self.run_search(search_id, "--max-items", "1")
        self.assertEqual(first["counts"]["draft"], 1)
        self.assertEqual(first["remaining_count"], 1)
        requests = list(self.transport.calls)
        before = runtime_snapshot(self.home)
        self.invoke("searches", "show", "--run-id", first["run_id"])
        self.assertEqual(runtime_snapshot(self.home), before)
        resumed = self.invoke("searches", "resume", "--run-id", first["run_id"], expected=2)["data"]
        assert_source_gaps(resumed["sources"])
        self.assertEqual(resumed["counts"]["draft"], 2)
        self.assertEqual(resumed["remaining_count"], 0)
        self.assertEqual(resumed["status"], "completed_with_gaps")
        self.assertEqual(self.transport.calls, requests)
        material_ids = {item["material_id"] for item in resumed["items"]}
        replay = self.run_search(search_id)
        self.assertEqual({item["material_id"] for item in replay["items"]}, material_ids)
        self.assertEqual(self.transport.calls, requests)
        self.assertEqual(len(self.invoke("materials", "list")["data"]["materials"]), 2)
        quiet = self.run_search(search_id, key="fictional-workable-refresh")
        self.assertEqual(quiet["counts"]["total"], 0)
        self.assertEqual(quiet["skipped"]["unchanged_draft"], 2)
        self.assertFalse(quiet["coverage_complete"])
        self.assertFalse(quiet["application_ready"])
        self.assertFalse(quiet["external_action_taken"])
        self.assertFalse(quiet["approvals_recorded"])
        self.assertEqual(len(self.transport.calls), len(requests) + 2)
        self.assertEqual(self.invoke("applications", "list")["data"]["applications"], [])

    def test_output_failure_recovers_saved_run_without_duplicate_drafts(self) -> None:
        from grounded_apply.cli import _emit
        search_id = self.save_search()
        failed = False

        def fail_once(args, **kwargs):
            nonlocal failed
            if kwargs["command"] == "searches.run" and kwargs.get("data") is not None and not failed:
                failed = True
                raise OSError("private-fictional-output-failure")
            return _emit(args, **kwargs)

        with patch("grounded_apply.cli._emit", side_effect=fail_once):
            lost = self.run_search(search_id)
        self.assertIsNone(lost)
        requests = list(self.transport.calls)
        recovered = self.run_search(search_id)
        self.assertEqual(recovered["counts"]["draft"], 2)
        self.assertEqual(self.transport.calls, requests)
        self.assertEqual(len(self.invoke("materials", "list")["data"]["materials"]), 2)


if __name__ == "__main__":
    unittest.main()
