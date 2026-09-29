from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from grounded_apply.cli import main
from scripts.check_onboarding import runtime_snapshot
from tests import test_profile_review_pages_cli as page_fixtures


class ProfileReviewSelectionCliTests(unittest.TestCase):
    # Reuse only fixture helpers, not the earlier test methods.
    setUp = page_fixtures.ProfileReviewPagesCliTests.setUp
    invoke = page_fixtures.ProfileReviewPagesCliTests.invoke
    seed = page_fixtures.ProfileReviewPagesCliTests.seed
    generic = page_fixtures.ProfileReviewPagesCliTests.generic
    text_review = page_fixtures.ProfileReviewPagesCliTests.text_review

    def selected(self, identifier: str, *, encoded: bool = False) -> dict:
        before = runtime_snapshot(self.home)
        arguments = ("--claim-id-json", json.dumps(identifier)) if encoded else ("--claim-id", identifier)
        result = self.invoke("profile", "review", *arguments)["data"]
        self.assertEqual(runtime_snapshot(self.home), before)
        self.assertEqual(set(result), {"items", "pending_count", "read_only"})
        self.assertTrue(result["read_only"])
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["claim"]["id"], identifier)
        return result

    def test_selection_preserves_exact_evidence_tokens_total_and_legacy_outputs(self) -> None:
        full = self.seed()
        baseline = self.invoke("profile", "review")
        page = self.invoke("profile", "review", "--limit", "2")
        human, warnings = self.text_review()
        for index in (0, 3, len(full) - 1):
            with self.subTest(index=index):
                selected = self.selected(full[index]["claim"]["id"])
                self.assertEqual(selected["items"], [full[index]])
                self.assertEqual(selected["pending_count"], len(full))
                self.assertEqual(self.selected(full[index]["claim"]["id"], encoded=True), selected)
                for sibling in full[:index] + full[index + 1:]:
                    self.assertNotIn(sibling["claim"]["id"], json.dumps(selected))
        self.assertEqual(self.invoke("profile", "review"), baseline)
        self.assertEqual(self.invoke("profile", "review", "--limit", "2"), page)
        self.assertEqual(self.text_review(), (human, warnings))
        selected_envelope = self.invoke("profile", "review", "--claim-id", full[3]["claim"]["id"])
        self.assertEqual(selected_envelope["warnings"], baseline["warnings"])

    def test_inventory_choice_opens_evidence_without_a_queue_predecessor(self) -> None:
        full = self.seed()
        inventory = self.invoke("profile", "inventory", "--topic", "publications", "--limit", "1")["data"]
        identifier = inventory["items"][0]["id"]
        expected = next(item for item in full if item["claim"]["id"] == identifier)
        self.assertEqual(self.selected(identifier)["items"], [expected])
        text, errors = self.text_review("--claim-id", identifier)
        self.assertIn(f"Showing one selected fact of {len(full)} pending", text)
        self.assertIn(expected["claim"]["canonical_text"], text)
        self.assertIn("Evidence:", text)
        self.assertIn("No records were changed", text)
        self.assertNotIn("Continue with", text)
        self.assertNotIn("End of this pass", text)
        for item in full:
            if item is not expected:
                self.assertNotIn(item["claim"]["id"], text + errors)

    def test_invalid_selection_fails_before_runtime_and_never_echoes_input(self) -> None:
        marker = "private-cursor-marker"
        cases = [
            ("--claim-id", ""), ("--claim-id", " \t "),
            ("--claim-id", marker + "\ud800"),
            ("--claim-id-json", marker), ("--claim-id-json", "null"),
            ("--claim-id-json", "true"), ("--claim-id-json", "2"),
            ("--claim-id-json", "{}"), ("--claim-id-json", "[]"),
            ("--claim-id-json", '""'), ("--claim-id-json", '" "'),
            ("--claim-id-json", '"private-cursor-marker\\ud800"'),
            ("--claim-id-json", '"private-cursor-marker" trailing'),
            ("--claim-id-json", "[" * 1100),
            ("--claim-id", marker, "--claim-id-json", json.dumps(marker)),
        ]
        for flag in ("--claim-id", "--claim-id-json"):
            value = marker if flag == "--claim-id" else json.dumps(marker)
            for pagination in (("--limit", "1"), ("--after", marker), ("--after-json", json.dumps(marker))):
                cases.append((flag, value, *pagination))
        for arguments in cases:
            with self.subTest(arguments=arguments), patch("grounded_apply.cli.resolve_runtime_paths") as paths:
                result = self.invoke("profile", "review", *arguments, expected=2)
                self.assertIsNone(result["data"])
                self.assertNotIn(marker, json.dumps(result))
                paths.assert_not_called()
            self.assertFalse(self.home.exists())

    def test_empty_missing_and_decided_claims_have_one_fixed_private_refusal(self) -> None:
        self.invoke("profile", "init")
        before = runtime_snapshot(self.home)
        empty = self.invoke("profile", "review", "--claim-id", "private-cursor-marker", expected=2)
        self.assertEqual(runtime_snapshot(self.home), before)
        full = self.seed()
        for index, decision in enumerate(("approve", "reject")):
            item = full[index]
            self.invoke("profile", "decide", "--claim-id", item["claim"]["id"],
                "--review-token", item["review_token"], "--decision", decision,
                "--actor-id", "synthetic-reviewer", "--idempotency-key", "fictional-selected-" + decision,
                "--confirm")
            before = runtime_snapshot(self.home)
            result = self.invoke("profile", "review", "--claim-id", item["claim"]["id"], expected=2)
            self.assertEqual(result, empty)
            self.assertEqual(runtime_snapshot(self.home), before)
            self.assertNotIn(item["claim"]["id"], json.dumps(result))
        self.assertEqual(self.selected(full[-1]["claim"]["id"])["pending_count"], len(full) - 2)

    def test_uninitialized_runtime_is_not_created(self) -> None:
        result = self.invoke("profile", "review", "--claim-id", "private-cursor-marker", expected=2)
        self.assertIsNone(result["data"])
        self.assertNotIn(str(self.home), json.dumps(result))
        self.assertFalse(self.home.exists())

    def test_generic_ids_preserve_exact_legacy_identity_and_null_token(self) -> None:
        self.invoke("profile", "init")
        identifiers = ("fictional/space id", "-fictional'$(not-a-command);#é",
                       "fictional-" + "x" * 300, "fictional\nline\x1b[31m\x00\u2028")
        for day, identifier in enumerate(identifiers):
            self.generic(identifier, day)
            result = self.selected(identifier, encoded=True)
            item = result["items"][0]
            self.assertIsNone(item["review_token"])
            self.assertIsNone(item["import_workflow_run_id"])
            self.assertEqual(item["evidence"], [])
            text, _ = self.text_review("--claim-id-json", json.dumps(identifier))
            self.assertNotIn("\x1b", text)
            self.assertNotIn("\x00", text)
            self.assertNotIn("\u2028", text)
        ordinary = self.invoke("profile", "review", "--claim-id=" + identifiers[1])["data"]
        self.assertEqual(ordinary["items"][0]["claim"]["id"], identifiers[1])

    def test_nonprinting_id_roundtrips_through_actual_process_argument(self) -> None:
        self.invoke("profile", "init")
        identifier = "fictional\nselected\x1b[31m\x00"
        self.generic(identifier, 0)
        expected = self.selected(identifier, encoded=True)
        before = runtime_snapshot(self.home)
        child = subprocess.run([str(Path(__file__).resolve().parents[1] / "scripts" / "gapply"),
            "profile", "review", "--claim-id-json=" + json.dumps(identifier), "--json"],
            capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"), check=False)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(json.loads(child.stdout)["data"], expected)
        self.assertEqual(runtime_snapshot(self.home), before)

    def test_private_read_conversion_and_formatting_errors_are_fixed(self) -> None:
        full = self.seed()
        identifier = full[-1]["claim"]["id"]
        targets = ("grounded_apply.cli.resolve_runtime_paths",
                   "grounded_apply.cli._open_initialized_profile_repository",
                   "grounded_apply.services.ProfileService.get_review_item",
                   "grounded_apply.domain.to_jsonable", "grounded_apply.cli._review_message")
        before = runtime_snapshot(self.home)
        errors = []
        for target in targets:
            with self.subTest(target=target), patch(target, side_effect=RuntimeError("private-cursor-marker " + identifier)):
                result = self.invoke("profile", "review", "--claim-id", identifier, expected=2)
                errors.append(result["error"])
                self.assertIsNone(result["data"])
                self.assertNotIn(identifier, json.dumps(result))
                self.assertNotIn("private-cursor-marker", json.dumps(result))
            self.assertEqual(runtime_snapshot(self.home), before)
        self.assertTrue(all(error == errors[0] for error in errors))

    def test_output_failure_is_private_and_retry_is_read_only(self) -> None:
        import grounded_apply.cli as cli
        full = self.seed()
        identifier = full[-1]["claim"]["id"]
        before = runtime_snapshot(self.home)
        original = cli._emit

        def fail_success(*args: object, **kwargs: object) -> None:
            if kwargs.get("ok", True):
                raise RuntimeError("private-cursor-marker " + identifier)
            original(*args, **kwargs)

        with patch.object(cli, "_emit", side_effect=fail_success):
            result = self.invoke("profile", "review", "--claim-id", identifier, expected=2)
        self.assertIsNone(result["data"])
        self.assertEqual(result["error"]["message"], "Selected profile review output failed; no state was changed")
        self.assertNotIn(identifier, json.dumps(result))
        self.assertEqual(runtime_snapshot(self.home), before)
        self.assertEqual(self.selected(identifier)["items"], [full[-1]])

    def test_interrupt_preserves_runtime_and_fixed_diagnostics(self) -> None:
        full = self.seed()
        before = runtime_snapshot(self.home)
        output, errors = io.StringIO(), io.StringIO()
        with patch("grounded_apply.services.ProfileService.get_review_item", side_effect=KeyboardInterrupt("private-cursor-marker")), contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = main(["--log-events", "profile", "review", "--claim-id", full[0]["claim"]["id"], "--json"])
        self.assertEqual(result, 130)
        self.assertEqual(output.getvalue(), "")
        events = [json.loads(line) for line in errors.getvalue().splitlines()]
        self.assertEqual(events[-1]["outcome"], "interrupted")
        self.assertNotIn("private-cursor-marker", errors.getvalue())
        self.assertEqual(runtime_snapshot(self.home), before)


if __name__ == "__main__":
    unittest.main()
