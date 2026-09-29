from __future__ import annotations

import contextlib
import io
import json
import os
import shlex
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from grounded_apply.cli import main
from scripts.check_onboarding import EVENT_FIELDS, PLAIN, runtime_snapshot


class ProfileReviewPagesCliTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="gapply-fictional-review-pages-")
        self.addCleanup(directory.cleanup)
        self.workspace = Path(directory.name).resolve()
        self.home = self.workspace / "runtime"
        environment = patch.dict(os.environ, {"GROUNDED_APPLY_HOME": str(self.home)})
        environment.start()
        self.addCleanup(environment.stop)

    def invoke(self, *args: str, expected: int = 0) -> dict:
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = main(["--log-events", *args, "--json"])
        envelope = json.loads(output.getvalue())
        self.assertEqual(result, expected, envelope)
        self.assertEqual(envelope["ok"], expected == 0)
        events = [json.loads(line) for line in errors.getvalue().splitlines()]
        self.assertTrue(events)
        self.assertTrue(all(set(event) == EVENT_FIELDS for event in events))
        for private in ("Avery", "Quill", "example.com", str(self.workspace), "private-cursor-marker"):
            self.assertNotIn(private, errors.getvalue())
        return envelope

    def text_review(self, *args: str, expected: int = 0) -> tuple[str, str]:
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = main(["profile", "review", *args])
        self.assertEqual(result, expected)
        return output.getvalue(), errors.getvalue()

    def seed(self) -> list[dict]:
        self.invoke("profile", "init")
        source = self.workspace / "fictional-profile.txt"
        source.write_text(PLAIN, encoding="utf-8")
        extracted = self.invoke("profile", "extract", "--source-file", str(source))["data"]
        self.invoke("profile", "onboard", "--source-file", str(source),
                    "--source-sha256", extracted["source_sha256"], "--select", "all",
                    "--idempotency-key", "fictional-paged-profile")
        return self.invoke("profile", "review")["data"]["items"]

    def page(self, limit: int, after: str | None = None) -> dict:
        arguments = ["profile", "review", "--limit", str(limit)]
        if after is not None:
            arguments += ["--after", after]
        before = runtime_snapshot(self.home)
        result = self.invoke(*arguments)["data"]
        self.assertEqual(runtime_snapshot(self.home), before)
        self.assertTrue(result["read_only"])
        page = result["page"]
        self.assertEqual(page["returned_count"], len(result["items"]))
        self.assertEqual(result["pending_count"], page["returned_count"] +
                         page["pending_before_count"] + page["pending_after_count"])
        self.assertEqual(set(page), {"limit", "returned_count", "pending_before_count",
                                    "pending_after_count", "next_after"})
        return result

    def test_default_contract_and_evidence_are_unchanged_while_page_minimizes_display(self) -> None:
        full = self.seed()
        baseline = self.invoke("profile", "review")
        self.assertEqual(set(baseline["data"]), {"items", "pending_count", "read_only"})
        first = self.page(2)
        self.assertEqual(first["pending_count"], len(full))
        self.assertEqual(first["items"], full[:2])
        self.assertEqual(first["page"], {"limit": 2, "returned_count": 2,
            "pending_before_count": 0, "pending_after_count": len(full) - 2,
            "next_after": full[1]["claim"]["id"]})
        paged = self.invoke("profile", "review", "--limit", "2")
        self.assertEqual(paged["warnings"], baseline["warnings"])
        for item in full[2:]:
            self.assertNotIn(item["claim"]["id"], json.dumps(first))
        self.assertEqual(self.invoke("profile", "review"), baseline)
        text, _ = self.text_review()
        self.assertTrue(text.startswith(f"{len(full)} profile claim(s) await review (read-only):"))
        text, _ = self.text_review("--limit", "2")
        self.assertIn(f"Showing 2 of {len(full)} pending", text)
        self.assertIn("Continue with gapply profile review --limit 2 --after=", text)

    def assert_decided_anchor(self, decision: str) -> None:
        full = self.seed()
        first = self.page(2)
        anchor = first["items"][-1]
        self.invoke("profile", "decide", "--claim-id", anchor["claim"]["id"],
                    "--review-token", anchor["review_token"], "--decision", decision,
                    "--actor-id", "synthetic-reviewer", "--idempotency-key", "fictional-page-decision", "--confirm")
        continued = self.page(2, first["page"]["next_after"])
        self.assertEqual(continued["items"], full[2:4])
        self.assertEqual(continued["pending_count"], len(full) - 1)
        self.assertEqual(continued["page"]["pending_before_count"], 1)
        self.assertEqual(self.page(1)["items"], full[:1])

    def test_approved_anchor_still_continues_without_losing_an_earlier_skipped_fact(self) -> None:
        self.assert_decided_anchor("approve")

    def test_rejected_anchor_still_continues_without_losing_an_earlier_skipped_fact(self) -> None:
        self.assert_decided_anchor("reject")

    def test_end_of_pass_with_skips_is_not_reported_as_completed_review(self) -> None:
        full = self.seed()
        first = self.page(3)
        second = self.page(3, first["page"]["next_after"])
        last = self.page(3, second["page"]["next_after"])
        self.assertEqual(first["items"] + second["items"] + last["items"], full)
        self.assertIsNone(last["page"]["next_after"])
        self.assertEqual(last["page"]["pending_before_count"], 6)
        text, _ = self.text_review("--limit", "3", "--after", second["page"]["next_after"])
        self.assertIn("6 earlier fact(s) still await review", text)
        self.assertIn("Restart without --after", text)
        self.assertNotIn("No profile claims are awaiting review", text)
        empty = self.page(3, full[-1]["claim"]["id"])
        self.assertEqual(empty["items"], [])
        self.assertEqual(empty["page"]["pending_before_count"], len(full))
        self.assertEqual(self.page(3)["items"], full[:3])

    def test_empty_profile_page_and_legacy_empty_message(self) -> None:
        self.invoke("profile", "init")
        result = self.page(5)
        self.assertEqual(result["pending_count"], 0)
        self.assertEqual(result["items"], [])
        self.assertIsNone(result["page"]["next_after"])
        text, _ = self.text_review()
        self.assertEqual(text, "No profile claims are awaiting review. No records were changed.\n")
        text, _ = self.text_review("--limit", "5")
        self.assertIn("No profile claims are awaiting review", text)

    def test_invalid_page_syntax_fails_before_runtime_access_and_does_not_echo_values(self) -> None:
        for arguments in (("--after", "private-cursor-marker"), ("--limit", "0"),
                          ("--limit", "51"), ("--limit", "private-cursor-marker"),
                          ("--limit", "3", "--after", ""), ("--limit", "3", "--after", " \t "),
                          ("--after-json", '"private-cursor-marker"'),
                          ("--limit", "3", "--after-json", "private-cursor-marker"),
                          ("--limit", "3", "--after-json", "null"),
                          ("--limit", "3", "--after-json", "[]"),
                          ("--limit", "3", "--after-json", '" "'),
                          ("--limit", "3", "--after-json", '"private-cursor-marker\\ud800"'),
                          ("--limit", "3", "--after", "private-cursor-marker", "--after-json", '"other"')):
            with self.subTest(arguments=arguments), patch("grounded_apply.cli.resolve_runtime_paths") as paths:
                result = self.invoke("profile", "review", *arguments, expected=2)
                self.assertIsNone(result["data"])
                self.assertNotIn("private-cursor-marker", json.dumps(result))
                paths.assert_not_called()
            self.assertFalse(self.home.exists())
        text, errors = self.text_review("--limit", "private-cursor-marker", expected=2)
        self.assertNotIn("private-cursor-marker", text + errors)

    def test_unknown_anchor_has_fixed_recovery_and_preserves_runtime(self) -> None:
        self.seed()
        before = runtime_snapshot(self.home)
        result = self.invoke("profile", "review", "--limit", "3", "--after", "private-cursor-marker", expected=2)
        self.assertIsNone(result["data"])
        self.assertNotIn("private-cursor-marker", json.dumps(result))
        self.assertEqual(runtime_snapshot(self.home), before)
        text, errors = self.text_review("--limit", "3", "--after", "private-cursor-marker", expected=2)
        self.assertNotIn("private-cursor-marker", text + errors)
        self.assertEqual(runtime_snapshot(self.home), before)

    def generic(self, identifier: str, day: int) -> None:
        from grounded_apply.config import resolve_runtime_paths
        from grounded_apply.domain import SourceType
        from grounded_apply.repositories import SQLiteRepository
        from grounded_apply.services import CreateClaim, ProfileService
        with SQLiteRepository(resolve_runtime_paths().database).initialize() as repository:
            ProfileService(repository).create_claim(CreateClaim(claim_id=identifier,
                claim_type="skill_use", value="Fictional manual skill",
                canonical_text="Used a fictional manual skill.", source_type=SourceType.USER_STATEMENT,
                source_ref="user-statement://fictional/review"),
                now=datetime(2026, 9, 1, tzinfo=UTC) + timedelta(days=day))

    def test_human_continuation_quotes_legacy_generic_ids_without_shell_execution(self) -> None:
        self.invoke("profile", "init")
        self.generic("fictional-oldest", 0)
        identifiers = ("fictional/space id", "-fictional'$(not-a-command);#é", "fictional-" + "x" * 300)
        for day, identifier in enumerate(identifiers, 1):
            with self.subTest(identifier=identifier):
                self.generic(identifier, day)
                self.assertEqual(self.page(1)["page"]["next_after"], identifier)
                text, _ = self.text_review("--limit", "1")
                line = next(line for line in text.splitlines() if line.startswith("Continue with "))
                # Parsing shell words is safe; no displayed command is executed.
                command = shlex.split(line.removeprefix("Continue with "))
                self.assertEqual(command[-1], "--after=" + identifier)
                result = self.invoke(*command[1:])["data"]
                self.assertNotEqual(result["items"][0]["claim"]["id"], identifier)
                self.assertEqual(result["page"]["pending_before_count"], 1)

    def test_nonprinting_generic_anchor_uses_exact_json_and_safe_human_guidance(self) -> None:
        self.invoke("profile", "init")
        self.generic("fictional-oldest", 0)
        identifier = "fictional\npage\x1b[31m\x00"
        self.generic(identifier, 1)
        page = self.page(1)
        self.assertEqual(page["page"]["next_after"], identifier)
        text, _ = self.text_review("--limit", "1")
        self.assertNotIn(identifier, text)
        self.assertNotIn("\x1b", text)
        line = next(line for line in text.splitlines() if line.startswith("Continue with "))
        command = shlex.split(line.removeprefix("Continue with "))
        self.assertTrue(command[-1].startswith("--after-json="))
        self.assertEqual(json.loads(command[-1].split("=", 1)[1]), identifier)
        self.assertTrue(all("\x00" not in argument for argument in command))
        before = runtime_snapshot(self.home)
        result = self.invoke(*command[1:])["data"]
        self.assertEqual(result["items"][0]["claim"]["id"], "fictional-oldest")
        child = subprocess.run([str(Path(__file__).resolve().parents[1] / "scripts" / "gapply"),
                                *command[1:], "--json"],
            capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"), check=False)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(json.loads(child.stdout)["data"], result)
        self.assertEqual(runtime_snapshot(self.home), before)


if __name__ == "__main__":
    unittest.main()
