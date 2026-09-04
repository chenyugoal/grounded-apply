from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from grounded_apply.cli import main
from grounded_apply.diagnostics import (
    CommandDiagnostics,
    DiagnosticCommand,
    DiagnosticOutcome,
)


FIXTURES = Path(__file__).parent / "fixtures" / "synthetic_profile"
PRIVATE = "SYNTHETIC_PRIVATE_VALUE /private/example/resume.txt password=fictional"


class DiagnosticTests(unittest.TestCase):
    def invoke(self, *args: str) -> tuple[int, str, str]:
        output, events = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(events):
            result = main(args)
        return result, output.getvalue(), events.getvalue()

    def assert_events(self, text: str, command: str, outcome: str) -> None:
        records = [json.loads(line) for line in text.splitlines()]
        self.assertEqual(len(records), 2)
        self.assertEqual([record["outcome"] for record in records], ["started", outcome])
        self.assertEqual(records[0]["run_id"], records[1]["run_id"])
        for record in records:
            self.assertEqual(set(record), {
                "schema_version", "event", "run_id", "at", "command", "outcome", "recovery",
            })
            self.assertEqual(record["schema_version"], 1)
            self.assertEqual(record["event"], "cli.command")
            self.assertEqual(record["command"], command)
            if record["outcome"] == "decision_outcome_unknown":
                self.assertIn("exact same confirmed request", record["recovery"])
                self.assertIn("No external action was taken", record["recovery"])
            else:
                self.assertIsNone(record["recovery"])
            self.assertEqual(UUID(record["run_id"]).version, 4)
            self.assertEqual(datetime.fromisoformat(record["at"]).utcoffset().seconds, 0)
        self.assertNotIn(PRIVATE, text)

    def test_boundary_rejects_arbitrary_fields_and_enum_lookalikes(self) -> None:
        sink = io.StringIO()
        for command in (PRIVATE, "paths", {"command": "paths"}, None):
            with self.subTest(command=type(command)), self.assertRaises(TypeError):
                CommandDiagnostics(sink, command)  # type: ignore[arg-type]
        log = CommandDiagnostics(sink, DiagnosticCommand.PATHS)
        for outcome in (PRIVATE, "failed", ValueError(PRIVATE), None):
            with self.subTest(outcome=type(outcome)), self.assertRaises(TypeError):
                log.emit(outcome)  # type: ignore[arg-type]
        for field in (
            "message", "error", "source_text", "value", "credentials", "answer",
            "review_token", "idempotency_key", "path", "metadata", "run_id",
        ):
            with self.subTest(field=field), self.assertRaises(TypeError):
                log.emit(DiagnosticOutcome.FAILED, **{field: PRIVATE})
        self.assertEqual(sink.getvalue(), "")

    def test_logging_is_disabled_by_default_and_does_not_create_runtime_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "unused"
            with patch.dict(os.environ, {"GROUNDED_APPLY_HOME": str(root)}):
                code, output, events = self.invoke("paths", "--json")
                self.assertEqual(code, 0)
                self.assertEqual(events, "")
                self.assertIn(str(root), output)
                code, _, events = self.invoke("--log-events", "doctor", "--json")
                self.assertEqual(code, 0)
                self.assert_events(events, "doctor", "succeeded")
            self.assertFalse(root.exists())

    def test_unknown_arguments_errors_and_warnings_never_enter_event_stream(self) -> None:
        for args in ((PRIVATE,), ("profile", "import", "--source-file", PRIVATE)):
            code, _, events = self.invoke("--log-events", *args)
            self.assertEqual(code, 2)
            self.assert_events(events, "unknown" if len(args) == 1 else "profile.import", "failed")
        with patch("grounded_apply.cli._command_paths", side_effect=ValueError(PRIVATE)):
            code, _, events = self.invoke("--log-events", "paths", "--json")
            self.assertEqual(code, 2)
            self.assert_events(events, "paths", "failed")

    def test_source_values_restricted_answers_tokens_and_keys_are_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"GROUNDED_APPLY_HOME": directory}
        ):
            self.assertEqual(self.invoke("profile", "init", "--json")[0], 0)
            source = FIXTURES / "resume.txt"
            manifest = FIXTURES / "import_proposals.json"
            import_key = "synthetic-private-import-retry"
            code, _, events = self.invoke(
                "--log-events", "profile", "import", "--source-file", str(source),
                "--proposals-file", str(manifest), "--idempotency-key", import_key, "--json",
            )
            self.assertEqual(code, 0)
            self.assert_events(events, "profile.import", "succeeded")
            code, review, review_events = self.invoke("--log-events", "profile", "review", "--json")
            self.assertEqual(code, 0)
            self.assert_events(review_events, "profile.review", "succeeded")
            item = json.loads(review)["data"]["items"][0]
            token = item["review_token"]
            decision_key = "synthetic-private-decision-retry"
            code, _, decision_events = self.invoke(
                "--log-events", "profile", "decide", "--claim-id", item["claim"]["id"],
                "--review-token", token, "--actor-id", "synthetic-private-actor",
                "--decision", "approve", "--idempotency-key", decision_key,
                "--confirm", "--json",
            )
            self.assertEqual(code, 0)
            self.assert_events(decision_events, "profile.decide", "succeeded")
            combined = events + review_events + decision_events
            for value in (directory, str(source), import_key, decision_key, token,
                          item["claim"]["canonical_text"], "synthetic-private-actor"):
                self.assertNotIn(value, combined)
            # Even rejected sensitive input and hostile exception text remain
            # outside the event schema; no classifier is needed to redact it.
            with patch("sys.stdin", io.StringIO("work authorization: SYNTHETIC_SECRET_ANSWER")):
                code, _, events = self.invoke(
                    "--log-events", "profile", "import", "--source-file", "-",
                    "--proposals-file", str(manifest), "--idempotency-key", import_key, "--json",
                )
            self.assertEqual(code, 2)
            self.assert_events(events, "profile.import", "failed")
            self.assertNotIn("SYNTHETIC_SECRET_ANSWER", events)

    def test_interrupt_and_help_have_content_free_terminal_events(self) -> None:
        with patch("grounded_apply.cli._command_paths", side_effect=KeyboardInterrupt(PRIVATE)):
            code, _, events = self.invoke("--log-events", "paths", "--json")
            self.assertEqual(code, 130)
            self.assert_events(events, "paths", "interrupted")
        sink = io.StringIO()
        with redirect_stderr(sink), redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            main(["--log-events", "--help"])
        self.assert_events(sink.getvalue(), "unknown", "succeeded")

    def test_sink_failure_never_changes_or_retries_a_command(self) -> None:
        class FailingSink(io.StringIO):
            def write(self, text: str) -> int:
                raise OSError(PRIVATE)

        with patch("grounded_apply.cli._command_paths", return_value=0) as command:
            with redirect_stderr(FailingSink()):
                self.assertEqual(main(["--log-events", "paths"]), 0)
            command.assert_called_once()
        for failure in (OSError(PRIVATE), KeyboardInterrupt(PRIVATE)):
            sink = io.StringIO()
            log = CommandDiagnostics(sink, DiagnosticCommand.PATHS)
            with patch.object(sink, "flush", side_effect=failure):
                log.emit(DiagnosticOutcome.SUCCEEDED)
            self.assertNotIn(PRIVATE, sink.getvalue())

    def test_post_commit_failure_keeps_recovery_guidance_in_the_event_schema(self) -> None:
        from grounded_apply.cli import PostCommitOutputError

        for failure in (PostCommitOutputError(PRIVATE), KeyboardInterrupt(PRIVATE)):
            with patch("grounded_apply.cli._command_profile_decide", side_effect=failure):
                code, _, events = self.invoke(
                    "--log-events", "profile", "decide", "--claim-id", PRIVATE,
                    "--review-token", PRIVATE, "--actor-id", PRIVATE,
                    "--decision", "approve", "--idempotency-key", PRIVATE,
                    "--confirm", "--json",
                )
            self.assertEqual(code, 130 if isinstance(failure, KeyboardInterrupt) else 2)
            self.assert_events(events, "profile.decide", "decision_outcome_unknown")

    def test_parser_output_failure_does_not_escape_as_a_private_traceback(self) -> None:
        with patch("grounded_apply.cli._emit", side_effect=OSError(PRIVATE)):
            code, output, events = self.invoke("--log-events", PRIVATE, "--json")
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assert_events(events, "unknown", "failed")


if __name__ == "__main__":
    unittest.main()
