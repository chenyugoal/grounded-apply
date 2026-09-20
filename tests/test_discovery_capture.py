from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from grounded_apply.cli import main
from grounded_apply.repositories import RepositoryError, SQLiteRepository
from grounded_apply.services.discovery import DiscoveryService, validate_source_manifest
from grounded_apply.services.jobs import DiscoveryCapacityError, JobService
from grounded_apply.services.workflow import canonical, digest


MANIFEST = {"schema_version": 1, "sources": [
    {"id": "fictional-labs", "provider": "greenhouse", "board": "fictional-labs"}]}


class Feed:
    def __init__(self, *, changed: bool = False) -> None:
        self.calls = 0
        self.body = {"jobs": [{"id": 123, "title": "Fictional Research Engineer",
            "location": {"name": "Fictional City"},
            "absolute_url": "https://job-boards.greenhouse.io/fictional-labs/jobs/123",
            "content": "<h2>Requirements</h2><p>Experience with Python" +
                (" and testing" if changed else "") + ".</p>"}], "meta": {"total": 1}}

    def get(self, url: str, *, max_bytes: int, timeout: float) -> bytes:
        self.calls += 1
        return json.dumps(self.body).encode()


def discovered(*, changed: bool = False):
    return DiscoveryService(Feed(changed=changed)).discover(
        validate_source_manifest(MANIFEST)).jobs[0]


class DiscoveryCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repository = SQLiteRepository(Path(directory.name) / "synthetic.db").initialize()
        self.addCleanup(self.repository.close)
        self.service = JobService(self.repository)

    def test_same_source_version_is_reused_and_changed_content_keeps_history(self) -> None:
        job = discovered()
        first = self.service.capture_discovered(job)
        retry = self.service.capture_discovered(job)
        self.assertTrue(retry["replayed"])
        self.assertEqual(first["job_id"], retry["job_id"])
        original = self.service.get(first["job_id"])
        self.assertEqual(original.capture_method, "public_ats_feed")
        self.assertFalse(original.live_page_verified)
        self.assertEqual(original.discovery["provider"], "greenhouse")
        self.assertEqual(original.source_text, job.source_text)
        updated = self.service.capture_discovered(discovered(changed=True))
        self.assertNotEqual(first["job_id"], updated["job_id"])
        self.assertEqual(original, self.service.get(first["job_id"]))
        self.assertEqual(len(self.service.list()), 2)
        for requirement in original.requirements:
            self.assertEqual(original.source_text[requirement.start:requirement.end], requirement.quote)

    def test_changed_hash_and_unsafe_url_are_rejected_before_storage(self) -> None:
        for job in (replace(discovered(), content_sha256="0" * 64),
                    replace(discovered(), source_url="https://example.com/other")):
            with self.assertRaises(ValueError):
                self.service.capture_discovered(job)
        self.assertEqual(self.repository.list_job_snapshots(), [])
        self.assertEqual(self.repository.list_workflow_runs(), [])

    def test_capacity_failure_rolls_back_every_row_and_allows_exact_retry(self) -> None:
        feed = Feed()
        feed.body["jobs"][0]["content"] = "<p>Fictional engineering work. " + "Python " * 8000 + "</p>"
        job = DiscoveryService(feed).discover(validate_source_manifest(MANIFEST)).jobs[0]
        before = self.repository.database_size_bytes()
        with patch("grounded_apply.services.jobs.MAX_SNAPSHOT_BYTES", before):
            with self.assertRaises(DiscoveryCapacityError):
                self.service.capture_discovered(job)
        self.assertEqual(self.repository.list_job_snapshots(), [])
        self.assertEqual(self.repository.list_workflow_runs(), [])
        self.assertFalse(self.service.capture_discovered(job)["replayed"])

    def test_provenance_edit_fails_even_when_audit_input_digest_is_recomputed(self) -> None:
        result = self.service.capture_discovered(discovered())
        workflow = self.repository.list_workflow_runs()[0]
        data = json.loads(workflow["input_json"])
        data["discovery"]["board"] = "different-fictional-board"
        # Controlled corruption of synthetic state exercises fail-closed review.
        self.repository._connection.execute(
            "UPDATE workflow_runs SET input_json=?, input_hash_sha256=? WHERE id=?",
            (canonical(data), digest(data), workflow["id"]))
        self.repository._connection.commit()
        with self.assertRaises(RepositoryError):
            self.service.get(result["job_id"])

    def test_manual_capture_keeps_its_existing_identity_and_provenance(self) -> None:
        original = self.service.add("https://example.com/jobs/1", "Requirements\nPython",
                                    idempotency_key="manual-synthetic")
        self.assertEqual(self.service.get(original["job_id"]).capture_method, "user_supplied_text")
        self.assertIsNone(self.service.get(original["job_id"]).discovery)
        self.assertTrue(self.service.add("https://example.com/jobs/1", "Requirements\nPython",
                                        idempotency_key="manual-synthetic")["replayed"])


class DiscoveryCliTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / "private-home"
        self.environment = patch.dict(os.environ, {"GROUNDED_APPLY_HOME": str(self.root)})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def invoke(self, *args: str, manifest: object = MANIFEST):
        output, errors = io.StringIO(), io.StringIO()
        with patch("sys.stdin", io.StringIO(json.dumps(manifest))), redirect_stdout(output), redirect_stderr(errors):
            code = main(list(args))
        return code, json.loads(output.getvalue()), errors.getvalue()

    def discover(self, *args: str, manifest: object = MANIFEST):
        return self.invoke("--log-events", "jobs", "discover", "--sources-file", "-", *args,
                           "--json", manifest=manifest)

    def test_preview_uses_network_without_profile_and_keeps_diagnostics_content_free(self) -> None:
        feed = Feed()
        with patch("grounded_apply.repositories.discovery_http.PublicJobHTTPTransport", return_value=feed):
            code, result, events = self.discover("--dry-run")
        self.assertEqual(code, 0, result)
        self.assertEqual(feed.calls, 1)
        self.assertFalse(self.root.exists())
        self.assertFalse(result["data"]["storage_checked"])
        self.assertEqual(result["data"]["captures"], [])
        self.assertEqual(len(result["data"]["jobs"]), 1)
        records = [json.loads(line) for line in events.splitlines()]
        self.assertEqual([r["command"] for r in records], ["jobs.discover"] * 2)
        self.assertEqual(records[-1]["outcome"], "succeeded")
        for private in ("fictional-labs", "Fictional", "Python", str(self.root)):
            self.assertNotIn(private, events)

    def test_capture_requires_initialized_storage_before_network(self) -> None:
        feed = Feed()
        with patch("grounded_apply.repositories.discovery_http.PublicJobHTTPTransport", return_value=feed):
            code, _, _ = self.discover()
        self.assertEqual(code, 2)
        self.assertEqual(feed.calls, 0)
        self.assertFalse(self.root.exists())

    def test_capture_replay_and_private_show_use_existing_job_contract(self) -> None:
        self.assertEqual(self.invoke("profile", "init", "--json")[0], 0)
        with patch("grounded_apply.repositories.discovery_http.PublicJobHTTPTransport", return_value=Feed()):
            code, first, _ = self.discover()
            retry_code, second, _ = self.discover()
        self.assertEqual((code, retry_code), (0, 0), (first, second))
        saved = first["data"]["captures"][0]
        self.assertEqual(saved["job_id"], second["data"]["captures"][0]["job_id"])
        self.assertTrue(second["data"]["captures"][0]["replayed"])
        self.assertNotIn("source_text", first["data"]["jobs"][0])
        show_code, show, _ = self.invoke("jobs", "show", "--job-id", saved["job_id"], "--json")
        self.assertEqual(show_code, 0)
        self.assertEqual(show["data"]["capture_method"], "public_ats_feed")
        _, listing, _ = self.invoke("jobs", "list", "--json")
        self.assertEqual(len(listing["data"]["jobs"]), 1)

    def test_manual_source_is_a_visible_gap_not_empty_success(self) -> None:
        manifest = {"schema_version": 1, "sources": [
            {"id": "fictional-custom", "provider": "manual", "careers_url": "https://example.com/careers"}]}
        with patch("grounded_apply.repositories.discovery_http.PublicJobHTTPTransport", return_value=Feed()):
            code, result, events = self.discover("--dry-run", manifest=manifest)
        self.assertEqual(code, 2)
        self.assertFalse(result["ok"])
        self.assertEqual(result["data"]["sources"][0]["status"], "manual_required")
        self.assertEqual(result["data"]["sources"][0]["careers_url"], "https://example.com/careers")
        self.assertEqual(json.loads(events.splitlines()[-1])["outcome"], "failed")
        self.assertFalse(self.root.exists())

    def test_invalid_manifest_never_contacts_sources(self) -> None:
        feed = Feed()
        with patch("grounded_apply.repositories.discovery_http.PublicJobHTTPTransport", return_value=feed):
            code, _, _ = self.discover("--dry-run", manifest={**MANIFEST, "send_profile": True})
        self.assertEqual(code, 2)
        self.assertEqual(feed.calls, 0)
        self.assertFalse(self.root.exists())

    def test_one_unsupported_snapshot_does_not_stop_the_next_capture(self) -> None:
        self.invoke("profile", "init", "--json")
        feed = Feed()
        bad = dict(feed.body["jobs"][0])
        bad.update(id=1, content="<h2>Requirements</h2>" + "<p>Fictional Python requirement.</p>" * 201)
        feed.body["jobs"].insert(0, bad)
        feed.body["meta"]["total"] = 2
        with patch("grounded_apply.repositories.discovery_http.PublicJobHTTPTransport", return_value=feed):
            code, result, _ = self.discover()
        self.assertEqual(code, 2)
        self.assertEqual(len(result["data"]["captures"]), 1)
        self.assertEqual(result["data"]["captures"][0]["external_id"], "123")
        self.assertEqual(result["data"]["capture_blockers"][0]["reason"], "snapshot_rejected")
        self.assertEqual(result["data"]["uncaptured_count"], 1)
        self.assertIsNone(result["data"]["storage_error"])

    def test_output_failure_warns_of_saved_captures_and_retry_reuses_them(self) -> None:
        self.invoke("profile", "init", "--json")
        output, errors = io.StringIO(), io.StringIO()
        with patch("sys.stdin", io.StringIO(json.dumps(MANIFEST))), redirect_stdout(output), redirect_stderr(errors), \
             patch("grounded_apply.repositories.discovery_http.PublicJobHTTPTransport", return_value=Feed()), \
             patch("grounded_apply.cli._emit", side_effect=RuntimeError("synthetic-output-failure")):
            code = main(["jobs", "discover", "--sources-file", "-", "--json"])
        self.assertEqual(code, 2)
        self.assertIn("snapshots may already be saved", errors.getvalue())
        self.assertIn("Repeat discovery", errors.getvalue())
        self.assertNotIn("synthetic-output-failure", errors.getvalue())
        with patch("grounded_apply.repositories.discovery_http.PublicJobHTTPTransport", return_value=Feed()):
            code, retry, _ = self.discover()
        self.assertEqual(code, 0)
        self.assertTrue(retry["data"]["captures"][0]["replayed"])

    def test_storage_drift_after_fetch_blocks_capture(self) -> None:
        self.invoke("profile", "init", "--json")
        root = self.root
        class Drift(Feed):
            def get(self, url: str, *, max_bytes: int, timeout: float) -> bytes:
                result = super().get(url, max_bytes=max_bytes, timeout=timeout)
                (root / "data").chmod(0o755)
                return result
        try:
            with patch("grounded_apply.repositories.discovery_http.PublicJobHTTPTransport", return_value=Drift()):
                code, result, _ = self.discover()
            self.assertEqual(code, 2)
            self.assertEqual(result["data"]["storage_error"], "capture_failed_retry_discovery")
            self.assertEqual(result["data"]["captures"], [])
        finally:
            (root / "data").chmod(0o700)


if __name__ == "__main__":
    unittest.main()
