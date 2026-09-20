from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from grounded_apply.cli import _batch_message, _schedule_message, _search_message, main
from grounded_apply.config import resolve_runtime_paths
from grounded_apply.repositories import SQLiteRepository
from scripts.check_search_filters import FEED, FILTERS, SOURCES, fixture_responses
from tests.test_materials import SyntheticRenderer, approved_fixture


class FixtureTransport:
    def __init__(self) -> None:
        self.responses = fixture_responses()
        self.calls: list[str] = []

    def get(self, url: str, *, max_bytes: int, timeout: float) -> bytes:
        self.calls.append(url)
        if url not in self.responses:
            raise AssertionError("Unexpected synthetic filter request")
        payload = json.dumps(self.responses[url]["body"]).encode()
        assert len(payload) <= max_bytes and timeout > 0
        return payload


class SearchFilterCliTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="gapply-synthetic-filter-cli-")
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name) / "fictional-home"
        environment = patch.dict(os.environ, {"GROUNDED_APPLY_HOME": str(self.home)})
        environment.start()
        self.addCleanup(environment.stop)
        self.transport = FixtureTransport()
        for boundary, replacement in (
            ("grounded_apply.repositories.discovery_http.PublicJobHTTPTransport", self.transport),
            ("grounded_apply.repositories.latex_renderer.LatexResumeRenderer", SyntheticRenderer()),
        ):
            patched = patch(boundary, return_value=replacement)
            patched.start()
            self.addCleanup(patched.stop)

    def invoke(self, *args: str, spec: object = None) -> tuple[int, dict, str]:
        output, errors = io.StringIO(), io.StringIO()
        with patch("sys.stdin", io.StringIO(json.dumps(spec))), redirect_stdout(output), redirect_stderr(errors):
            code = main(["--log-events", *args, "--json"])
        return code, json.loads(output.getvalue()) if output.getvalue() else {}, errors.getvalue()

    def manifest(self, *, initialized: bool = False) -> dict:
        claims = ("fictional-claim",)
        if initialized:
            self.assertEqual(self.invoke("profile", "init")[0], 0)
            with SQLiteRepository(resolve_runtime_paths().database).initialize() as repository:
                _, claims = approved_fixture(repository)
        return {"schema_version": 2, "sources": SOURCES, "claim_ids": list(claims),
            "title_contains": ["Python Engineer"], "preparation_filters": dict(FILTERS), "max_jobs": 1}

    def configure(self, spec: dict, *extra: str, key: str = "filter-scope") -> tuple[int, dict, str]:
        return self.invoke("searches", "configure", "--spec-file", "-", "--idempotency-key", key, *extra, spec=spec)

    def run_spec(self, spec: dict, *, key: str = "filter-scope") -> tuple[str, dict, str]:
        code, configured, _ = self.configure(spec, key=key)
        self.assertEqual(code, 0, configured)
        identifier = configured["data"]["search_id"]
        code, result, events = self.invoke("searches", "run", "--search-id", identifier,
            "--idempotency-key", key + "-run")
        self.assertEqual(code, 0, result)
        return identifier, result["data"], events

    def test_malformed_filters_are_rejected_before_runtime_and_network(self) -> None:
        base = self.manifest()
        invalid = [None, [], {"extra": True}, {"missing_location": True}, {"missing_location": "guess"},
            {"title_excludes": "senior"}, {"location_contains": [True]}, {"location_excludes": [" Remote"]},
            {"location_contains": ["x" * 129]}, {"title_excludes": ["x"] * 21}, {"location_contains": ["x\ny"]}]
        for filters in invalid:
            with self.subTest(filters=filters), patch("grounded_apply.cli._open_initialized_profile_repository") as opened:
                code, result, _ = self.configure({**base, "preparation_filters": filters})
                self.assertEqual(code, 2)
                self.assertFalse(result["ok"])
                opened.assert_not_called()
        with patch("grounded_apply.cli._open_initialized_profile_repository") as opened:
            self.assertEqual(self.configure({**base, "schema_version": 1})[0], 2)
            opened.assert_not_called()
        self.assertEqual(self.transport.calls, [])
        self.assertFalse(self.home.exists())

    def test_v2_preview_defaults_are_closed_and_v1_shape_stays_unchanged(self) -> None:
        spec = self.manifest()
        del spec["preparation_filters"]
        code, result, _ = self.configure(spec, "--dry-run")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["data"]["manifest"]["preparation_filters"], {
            "title_excludes": [], "location_contains": [], "location_excludes": [], "missing_location": "include"})
        code, legacy, _ = self.configure({**spec, "schema_version": 1}, "--dry-run")
        self.assertEqual(code, 0, legacy)
        self.assertNotIn("preparation_filters", legacy["data"]["manifest"])
        self.assertFalse(self.home.exists())
        self.assertEqual(self.transport.calls, [])

    def test_filters_precede_capture_cap_and_keep_diagnostics_private(self) -> None:
        _, report, events = self.run_spec(self.manifest(initialized=True))
        self.assertEqual(report["counts"]["draft"], 1)
        self.assertTrue(report["selection"][0]["title"].endswith("Preferred"))
        self.assertEqual(report["selection"][0]["location"], "Fictional ReMoTe North")
        for reason in ("title_excluded", "location_excluded", "location_not_matched", "location_unknown_excluded"):
            self.assertEqual(report["skipped"][reason], 1)
            self.assertEqual(report["sources"][0]["skipped"][reason], 1)
        self.assertEqual(report["sources"][0]["captured_count"], 1)
        self.assertEqual(report["sources"][0]["report"]["filtered_count"], 0)
        self.assertTrue(report["coverage_complete"])
        self.assertFalse(report["application_ready"])
        self.assertFalse(report["approvals_recorded"])
        self.assertEqual(report["items"][0]["questionnaire_coverage"], "unknown")
        for forbidden in ("Avery", "Quill", "fictional-filters", "example.com", "Remote", str(self.home)):
            self.assertNotIn(forbidden, events)
        with SQLiteRepository(resolve_runtime_paths().database, read_only=True).initialize() as repository:
            self.assertEqual(repository.list_applications(), [])
            self.assertTrue(all(repository.get_material_approval(item) is None for item in repository.list_material_ids()))

    def test_explicit_unknown_include_bypasses_only_location_text_filters(self) -> None:
        spec = self.manifest(initialized=True)
        spec["preparation_filters"]["missing_location"] = "include"
        _, report, _ = self.run_spec(spec)
        self.assertTrue(report["selection"][0]["title"].endswith("Unknown"))
        self.assertIn("location", report["selection"][0])
        self.assertIsNone(report["selection"][0]["location"])
        self.assertEqual(report["skipped"]["location_unknown_excluded"], 0)
        self.assertEqual(report["skipped"]["title_excluded"], 1)

    def test_v1_selection_is_unchanged_and_v2_replay_is_immutable_read_only(self) -> None:
        spec = self.manifest(initialized=True)
        legacy = {key: value for key, value in spec.items() if key != "preparation_filters"}
        legacy["schema_version"] = 1
        _, old, _ = self.run_spec(legacy, key="legacy-scope")
        self.assertIn("Senior", old["selection"][0]["title"])
        self.assertNotIn("title_excluded", old["skipped"])
        identifier, current, _ = self.run_spec(spec)
        calls = list(self.transport.calls)
        database = resolve_runtime_paths().database
        before = database.read_bytes()
        self.assertEqual(self.configure({**spec, "preparation_filters": {**FILTERS, "missing_location": "include"}})[0], 2)
        self.assertEqual(database.read_bytes(), before)
        code, replay, _ = self.invoke("searches", "run", "--search-id", identifier, "--idempotency-key", "filter-scope-run")
        self.assertEqual(code, 0, replay)
        self.assertEqual(replay["data"]["selection"], current["selection"])
        self.assertEqual(replay["data"]["items"][0]["material_id"], current["items"][0]["material_id"])
        before = database.read_bytes()
        self.assertEqual(self.invoke("searches", "show", "--run-id", current["run_id"])[0], 0)
        self.assertEqual(self.invoke("searches", "list", "--search-id", identifier)[0], 0)
        self.assertEqual(self.invoke("searches", "scopes")[0], 0)
        self.assertEqual(database.read_bytes(), before)
        self.assertEqual(self.transport.calls, calls)

    def test_location_matching_is_unicode_casefold_literal_without_geographic_inference(self) -> None:
        spec = self.manifest(initialized=True)
        job = self.transport.responses[FEED]["body"]["jobs"][-1]
        job["location"]["name"] = "Fictional Straße; Not Remote"
        self.transport.responses[FEED]["body"] = {"jobs": [job], "meta": {"total": 1}}
        spec["preparation_filters"]["location_contains"] = ["STRASSE"]
        _, report, _ = self.run_spec(spec)
        self.assertEqual(report["selection"][0]["location"], "Fictional Straße; Not Remote")
        spec["preparation_filters"]["location_contains"] = ["Remote"]
        _, literal, _ = self.run_spec(spec, key="literal-scope")
        self.assertEqual(literal["selection"][0]["location"], "Fictional Straße; Not Remote")

    def test_human_review_shows_unknown_locations_filter_counts_and_parent_recovery(self) -> None:
        batch = {"stop_reason": "item_budget", "items": [{"job_id": "fictional-job", "status": "queued"}]}
        search = {"batch": batch, "selection": [{"job_id": "fictional-job", "location": None}],
            "skipped": {"location_unknown_excluded": 2}}
        standalone = _batch_message(batch)
        saved = _search_message(search)
        scheduled = _schedule_message({"child": search})
        self.assertIn("Resume this batch to continue.", standalone)
        self.assertIn("Resume this saved search run to continue.", saved)
        self.assertNotIn("Resume this batch", saved)
        self.assertIn("Tick this schedule to continue.", scheduled)
        self.assertNotIn("Resume this saved search", scheduled)
        for message in (saved, scheduled):
            self.assertIn("Published location: not provided; review required", message)
            self.assertIn("2 missing location excluded", message)


if __name__ == "__main__":
    unittest.main()
