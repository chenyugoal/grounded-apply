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


EVENT_FIELDS = {"schema_version", "event", "run_id", "at", "command", "outcome", "recovery"}


class DiscoveryPlanCliTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="gapply-fictional-search-plan-")
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name) / "unused-profile"
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {"GROUNDED_APPLY_HOME": str(self.home)}))
        for target in (
            "grounded_apply.cli.resolve_runtime_paths",
            "grounded_apply.cli._open_initialized_profile_repository",
            "grounded_apply.cli._read_utf8_input",
            "sqlite3.connect", "socket.getaddrinfo", "socket.create_connection",
        ):
            self.stack.enter_context(patch(target, side_effect=AssertionError("Unexpected resource access")))

    def invoke(self, *arguments: str, expected: int = 0, logged: bool = True) -> dict:
        output, errors = io.StringIO(), io.StringIO()
        prefix = ["--log-events"] if logged else []
        with redirect_stdout(output), redirect_stderr(errors):
            result = main([*prefix, "jobs", "plan-search", *arguments, "--json"])
        self.assertEqual(result, expected, output.getvalue())
        envelope = json.loads(output.getvalue())
        self.assertEqual(envelope["command"], "jobs.plan-search")
        self.assertEqual(envelope["ok"], expected == 0)
        if logged:
            events = [json.loads(line) for line in errors.getvalue().splitlines()]
            self.assertEqual(len(events), 2)
            for event in events:
                self.assertEqual(set(event), EVENT_FIELDS)
                self.assertEqual(event["command"], "jobs.plan-search")
            self.assertEqual(events[-1]["outcome"], "succeeded" if expected == 0 else "failed")
        else:
            self.assertEqual(errors.getvalue(), "")
        for private in (*arguments, str(self.home)):
            if len(private) > 4:
                self.assertNotIn(private, errors.getvalue())
        self.assertFalse(self.home.exists())
        return envelope

    def test_roles_locations_and_punctuation_are_literal_private_data_without_runtime(self) -> None:
        data = self.invoke("--role", 'C++ / R&D "Engineer"', "--role", "研究員 C# .NET",
                           "--location", "Fictional Remote Region")["data"]
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["query_count"], 4)
        self.assertEqual([row["terms"] for row in data["searches"]], [
            ['C++ / R&D "Engineer"'], ['C++ / R&D "Engineer"', "Fictional Remote Region"],
            ["研究員 C# .NET"], ["研究員 C# .NET", "Fictional Remote Region"],
        ])
        self.assertEqual(data["network_requests"], 0)
        for flag in ("storage_changed", "profile_read", "coverage_established"):
            self.assertIs(data[flag], False)
        self.assertEqual(data["max_queries"], 9)
        self.assertEqual(data["max_distinct_links"], 18)
        self.assertNotIn("manifest", data)
        self.assertNotIn("jobs", data)
        self.assertEqual(len(data["searches"][0]["domains"]), 7)

    def test_complete_maximum_and_exact_replay_do_not_silently_drop_inputs(self) -> None:
        arguments = ("--role", "Fictional Researcher", "--role", "Fictional Scientist",
                     "--role", "Fictional Engineer", "--location", "Fictional East",
                     "--location", "Fictional West")
        first = self.invoke(*arguments)["data"]
        self.assertEqual(first, self.invoke(*arguments)["data"])
        self.assertEqual(first["query_count"], 9)
        self.assertEqual([row["position"] for row in first["searches"]], list(range(1, 10)))

    def test_search_intent_words_are_not_treated_as_candidate_sensitive_facts(self) -> None:
        terms = ("Security clearance analyst", "Visa sponsorship policy researcher", "Veteran services engineer")
        data = self.invoke(*(part for term in terms for part in ("--role", term)))["data"]
        self.assertEqual(data["roles"], list(terms))
        self.assertEqual(data["query_count"], 3)

    def test_bad_terms_and_excess_input_fail_without_echoing_content_or_access(self) -> None:
        cases = [("--role", value) for value in ("", " ", "PRIVATE\nROLE", "\tPRIVATE", "PRIVATE\x00", "PRIVATE\ud800", "x" * 129)]
        cases.extend([
            tuple(part for i in range(4) for part in ("--role", f"PRIVATE_ROLE_{i}")),
            ("--role", "PRIVATE_ROLE", *(part for i in range(3) for part in ("--location", f"PRIVATE_LOCATION_{i}"))),
        ])
        for case in cases:
            with self.subTest(case=ascii(case)):
                envelope = self.invoke(*case, expected=2)
                self.assertIsNone(envelope["data"])
                self.assertNotIn("PRIVATE", json.dumps(envelope))

    def test_usage_errors_are_fixed_and_registered_in_diagnostics(self) -> None:
        for arguments in ((), ("--role", "PRIVATE_ROLE", "--unknown", "PRIVATE_VALUE"), ("--ro", "PRIVATE_ROLE")):
            with self.subTest(arguments=arguments):
                envelope = self.invoke(*arguments, expected=2)
                self.assertEqual(envelope["error"]["type"], "CliUsageError")
                self.assertNotIn("PRIVATE", json.dumps(envelope))

    def test_human_output_explains_plan_limits_and_preserves_terms(self) -> None:
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            result = main(["jobs", "plan-search", "--role", "C++ Fictional Engineer"])
        self.assertEqual(result, 0)
        text = output.getvalue()
        self.assertIn('Terms: ["C++ Fictional Engineer"]', text)
        self.assertIn("No sites checked", text)
        self.assertIn("no job-market coverage", text)
        self.assertEqual(errors.getvalue(), "")
        self.assertFalse(self.home.exists())

    def test_output_failure_does_not_leak_exception_content_or_request_recovery(self) -> None:
        from grounded_apply.cli import _emit

        def fail_private_output(args, **kwargs):
            if kwargs.get("data") is not None:
                raise OSError("PRIVATE_ROLE_OUTPUT")
            return _emit(args, **kwargs)

        with patch("grounded_apply.cli._emit", side_effect=fail_private_output):
            envelope = self.invoke("--role", "PRIVATE_ROLE", expected=2)
        self.assertEqual(envelope["error"]["message"], "Search plan output failed; no state was changed")
        self.assertNotIn("PRIVATE", json.dumps(envelope))

    def test_unicode_format_marks_remain_literal_data_but_are_escaped_in_human_output(self) -> None:
        role = "Fictional\u202e Engineer"
        self.assertEqual(self.invoke("--role", role)["data"]["roles"], [role])
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            self.assertEqual(main(["jobs", "plan-search", "--role", role]), 0)
        self.assertNotIn("\u202e", output.getvalue())
        self.assertIn("\\u202e", output.getvalue())
        self.assertFalse(self.home.exists())

    def test_fresh_process_without_profile_builds_a_plan_and_creates_no_files(self) -> None:
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
        result = subprocess.run([sys.executable, "-W", "error", "-m", "grounded_apply.cli", "--log-events", "jobs", "plan-search",
                                 "--role", "Fictional Robotics Researcher", "--location", "Fictional City", "--json"],
                                env=environment, capture_output=True, text=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["data"]["query_count"], 2)
        self.assertNotIn("Fictional", result.stderr)
        self.assertFalse(self.home.exists())


if __name__ == "__main__":
    unittest.main()
