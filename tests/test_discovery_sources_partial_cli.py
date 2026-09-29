from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from grounded_apply.cli import main
from tests.test_workable_cli import WorkableTransport
from scripts.check_workable import INPUT_URLS, assert_source_gaps


EVENT_FIELDS = {"schema_version", "event", "run_id", "at", "command", "outcome", "recovery"}
STRICT_FIELDS = {"manifest", "inputs", "automatic_source_count", "manual_source_count",
                 "network_requests", "storage_changed", "live_boards_verified", "scope"}
PARTIAL_FIELDS = {"setup_status", "input_count", "accepted_input_count", "rejected_inputs"}
VALID = "https://jobs.lever.co/fictional"
INVALID = "http://example.com/PRIVATE_REJECTED_LINK"


class KeepValidSourcesCliTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="gapply-fictional-keep-valid-")
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.home = self.directory / "unused-profile"
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {"GROUNDED_APPLY_HOME": str(self.home)}))
        for target in ("grounded_apply.cli.resolve_runtime_paths", "grounded_apply.cli._open_initialized_profile_repository",
                       "sqlite3.connect", "socket.getaddrinfo", "socket.create_connection"):
            self.stack.enter_context(patch(target, side_effect=AssertionError("Unexpected resource access")))

    def invoke(self, *arguments: str, expected: int = 0, stdin: str = "") -> dict:
        output, errors = io.StringIO(), io.StringIO()
        with patch("sys.stdin", io.StringIO(stdin)), redirect_stdout(output), redirect_stderr(errors):
            code = main(["--log-events", "jobs", "sources", *arguments, "--json"])
        self.assertEqual(code, expected, output.getvalue())
        envelope = json.loads(output.getvalue())
        self.assertEqual(envelope["ok"], expected == 0)
        self.assertEqual(envelope["command"], "jobs.sources")
        events = [json.loads(line) for line in errors.getvalue().splitlines()]
        self.assertEqual(len(events), 2)
        for event in events:
            self.assertEqual(set(event), EVENT_FIELDS)
            self.assertEqual(event["command"], "jobs.sources")
        self.assertEqual(events[-1]["outcome"], "succeeded" if expected == 0 else "failed")
        for private in ("PRIVATE", "fictional", "example.com", str(self.home)):
            self.assertNotIn(private, errors.getvalue())
        self.assertNotIn("PRIVATE", output.getvalue())
        self.assertFalse(self.home.exists())
        return envelope

    def test_opt_in_preserves_strict_fields_and_exact_success_payload(self) -> None:
        arguments = ("--url", VALID, "--url", VALID + "/post-1", "--url", "https://example.com/careers")
        strict = self.invoke(*arguments)["data"]
        self.assertEqual(set(strict), STRICT_FIELDS)
        tolerant = self.invoke(*arguments, "--keep-valid")["data"]
        self.assertEqual(set(tolerant), STRICT_FIELDS | PARTIAL_FIELDS)
        self.assertEqual({key: tolerant[key] for key in STRICT_FIELDS}, strict)
        self.assertEqual(tolerant["setup_status"], "complete")
        self.assertEqual(tolerant["input_count"], 3)
        self.assertEqual(tolerant["accepted_input_count"], 3)
        self.assertEqual(tolerant["rejected_inputs"], [])

    def test_mixed_results_keep_original_positions_and_manifest_with_fixed_error(self) -> None:
        arguments = ("--url", INVALID, "--url", VALID, "--url", "https://example.com/careers?PRIVATE=token",
                     "--url", VALID + "/post-1")
        strict = self.invoke(*arguments, expected=2)
        self.assertIsNone(strict["data"])
        result = self.invoke(*arguments, "--keep-valid", expected=2)
        data = result["data"]
        self.assertEqual(result["error"]["type"], "IncompleteSourceSetup")
        self.assertEqual(data["setup_status"], "partial")
        self.assertEqual((data["input_count"], data["accepted_input_count"]), (4, 2))
        self.assertEqual([row["position"] for row in data["inputs"]], [2, 4])
        self.assertEqual([row["duplicate"] for row in data["inputs"]], [False, True])
        self.assertEqual(data["rejected_inputs"], [{"position": 1, "error": "invalid_url"}, {"position": 3, "error": "invalid_url"}])
        self.assertEqual(data["manifest"], self.invoke("--url", VALID)["data"]["manifest"])

    def test_all_invalid_has_null_manifest_and_complete_rejection_accounting(self) -> None:
        result = self.invoke("--keep-valid", "--url", INVALID, "--url", "https://PRIVATE:secret@example.com", expected=2)
        data = result["data"]
        self.assertEqual(data["setup_status"], "failed")
        self.assertIsNone(data["manifest"])
        self.assertEqual(data["inputs"], [])
        self.assertEqual((data["input_count"], data["accepted_input_count"]), (2, 0))
        self.assertEqual((data["automatic_source_count"], data["manual_source_count"]), (0, 0))
        self.assertEqual(data["rejected_inputs"], [{"position": i, "error": "invalid_url"} for i in (1, 2)])

    def test_manual_only_is_complete_setup_without_implying_live_coverage(self) -> None:
        data = self.invoke("--keep-valid", "--url", "https://apply.workable.com/j/ABCDEF1234")["data"]
        self.assertEqual(data["setup_status"], "complete")
        self.assertEqual(data["manual_source_count"], 1)
        self.assertFalse(data["inputs"][0]["automatic"])
        self.assertFalse(data["live_boards_verified"])
        self.assertFalse(data["storage_changed"])
        self.assertEqual(data["network_requests"], 0)

    def test_cap_rejects_new_sources_but_retains_later_duplicate_positions(self) -> None:
        urls = [f"https://jobs.lever.co/fictional-{i}" for i in range(32)]
        urls.extend(["https://jobs.lever.co/fictional-over-cap", INVALID, urls[0]])
        data = self.invoke("--keep-valid", "--urls-file", "-", stdin="\n".join(urls), expected=2)["data"]
        self.assertEqual(len(data["manifest"]["sources"]), 32)
        self.assertEqual(data["accepted_input_count"], 33)
        self.assertEqual(data["inputs"][-1]["position"], 35)
        self.assertTrue(data["inputs"][-1]["duplicate"])
        self.assertEqual(data["rejected_inputs"], [{"position": 33, "error": "source_limit_reached"},
                                                  {"position": 34, "error": "invalid_url"}])

    def test_file_and_stdin_positions_index_nonblank_entries_without_rewriting_input(self) -> None:
        content = f"\n  {VALID}  \n\n{INVALID}\n\nhttps://example.com/careers\n"
        path = self.directory / "fictional-links.txt"
        path.write_text(content)
        from_file = self.invoke("--urls-file", str(path), "--keep-valid", expected=2)["data"]
        from_stdin = self.invoke("--urls-file", "-", "--keep-valid", stdin=content, expected=2)["data"]
        self.assertEqual(from_file, from_stdin)
        self.assertEqual(from_file["input_count"], 3)
        self.assertEqual([row["position"] for row in from_file["inputs"]], [1, 3])
        self.assertEqual(from_file["rejected_inputs"], [{"position": 2, "error": "invalid_url"}])
        self.assertEqual(path.read_text(), content)

    def test_empty_or_excess_input_and_usage_errors_remain_top_level(self) -> None:
        for stdin in ("\n \n", "\n".join([VALID] * 257)):
            self.assertIsNone(self.invoke("--keep-valid", "--urls-file", "-", stdin=stdin, expected=2)["data"])
        for arguments in (("--keep-valid",), ("--url", VALID, "--keep-val"),
                          ("--url", VALID, "--urls-file", "-", "--keep-valid")):
            result = self.invoke(*arguments, expected=2)
            self.assertEqual(result["error"]["type"], "CliUsageError")
            self.assertIsNone(result["data"])

    def test_human_failure_reports_positions_and_no_manifest_without_echoing_bad_urls(self) -> None:
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = main(["jobs", "sources", "--keep-valid", "--url", INVALID])
        self.assertEqual(code, 2)
        self.assertIn("Source setup failed: 0 of 1 links accepted", output.getvalue())
        self.assertIn("Input 1: invalid_url", output.getvalue())
        self.assertIn("No usable source manifest", output.getvalue())
        self.assertNotIn("PRIVATE", output.getvalue() + errors.getvalue())

    def test_output_failure_sanitizes_private_exception(self) -> None:
        from grounded_apply.cli import _emit

        def fail_output(args, **kwargs):
            if kwargs.get("data") is not None:
                raise OSError("PRIVATE_OUTPUT_FAILURE")
            return _emit(args, **kwargs)

        with patch("grounded_apply.cli._emit", side_effect=fail_output):
            result = self.invoke("--keep-valid", "--url", VALID, expected=2)
        self.assertEqual(result["error"]["message"], "Source setup output failed; no state was changed")

    def test_retained_manifest_previews_feeds_with_separate_setup_and_source_gaps(self) -> None:
        urls = [*INPUT_URLS, INVALID]
        setup = self.invoke("--keep-valid", "--urls-file", "-", stdin="\n".join(urls), expected=2)["data"]
        transport = WorkableTransport()
        output, errors = io.StringIO(), io.StringIO()
        with patch("grounded_apply.repositories.discovery_http.PublicJobHTTPTransport", return_value=transport), \
             patch("sys.stdin", io.StringIO(json.dumps(setup["manifest"]))), redirect_stdout(output), redirect_stderr(errors):
            code = main(["jobs", "discover", "--sources-file", "-", "--dry-run", "--json"])
        self.assertEqual(code, 2)
        preview = json.loads(output.getvalue())["data"]
        assert_source_gaps(preview["sources"])
        self.assertEqual(len(preview["jobs"]), 2)
        self.assertEqual(preview["captures"], [])
        self.assertEqual(setup["rejected_inputs"], [{"position": 5, "error": "invalid_url"}])
        self.assertFalse(self.home.exists())

    def test_fresh_process_preserves_valid_setup_without_profile_files(self) -> None:
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
        result = subprocess.run([sys.executable, "-W", "error", "-m", "grounded_apply.cli", "--log-events", "jobs", "sources",
                                 "--url", VALID, "--url", INVALID, "--keep-valid", "--json"],
                                env=environment, capture_output=True, text=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["data"]["accepted_input_count"], 1)
        self.assertNotIn("PRIVATE", result.stdout + result.stderr)
        self.assertNotIn("fictional", result.stderr)
        self.assertFalse(self.home.exists())


if __name__ == "__main__":
    unittest.main()
