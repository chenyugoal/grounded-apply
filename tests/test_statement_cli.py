from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from scripts.check_onboarding import FACTS, runtime_snapshot, statement_fixture
from tests import test_onboarding_cli as onboarding


class StatementCliTests(unittest.TestCase):
    setUp = onboarding.OnboardingCLITests.setUp
    invoke = onboarding.OnboardingCLITests.invoke

    def arguments(self, *, kind: str | None = "user-statement") -> list[str]:
        source, manifest = statement_fixture()
        self.source_path = self.workspace / "fictional-answers.txt"
        self.manifest_path = self.workspace / "fictional-proposals.json"
        self.source_path.write_text(source, encoding="utf-8")
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        arguments = ["profile", "import", "--source-file", str(self.source_path),
            "--proposals-file", str(self.manifest_path), "--retain-all-facts",
            "--idempotency-key", "fictional-answer-retention"]
        if kind is not None:
            arguments += ["--source-kind", kind]
        return arguments

    def test_exact_answers_preview_without_storage_then_remain_pending(self) -> None:
        arguments = self.arguments()
        before = runtime_snapshot(self.workspace)
        preview = self.invoke(*arguments, "--dry-run")
        self.assertEqual(preview["source_type"], "user_statement")
        self.assertEqual(preview["proposal_count"], len(FACTS))
        self.assertTrue(preview["review_required"])
        self.assertFalse(preview["storage_checked"])
        self.assertEqual(runtime_snapshot(self.workspace), before)
        self.invoke("profile", "init")
        retained = self.invoke(*arguments)
        self.assertEqual(retained["source_type"], "user_statement")
        before = runtime_snapshot(self.runtime)
        review = self.invoke("profile", "review")
        self.assertEqual(review["pending_count"], len(FACTS))
        self.assertEqual([item["claim"]["canonical_text"] for item in review["items"]], list(FACTS))
        for item in review["items"]:
            self.assertEqual(item["claim"]["source_type"], "user_statement")
            self.assertEqual(item["claim"]["status"], "needs_review")
            self.assertEqual(item["claim"]["approval_status"], "pending")
            self.assertTrue(all(evidence["source_type"] == "user_statement" for evidence in item["evidence"]))
        self.assertEqual(self.invoke(*arguments), retained)
        self.assertEqual(runtime_snapshot(self.runtime), before)

    def test_explicit_decision_does_not_approve_other_answers_and_keeps_anchor(self) -> None:
        arguments = self.arguments()
        self.invoke("profile", "init")
        self.invoke(*arguments)
        page = self.invoke("profile", "review", "--limit", "2")
        item = page["items"][-1]
        decision = ["profile", "decide", "--claim-id", item["claim"]["id"],
            "--review-token", item["review_token"], "--decision", "approve",
            "--actor-id", "synthetic-reviewer", "--idempotency-key", "fictional-answer-approval"]
        before = runtime_snapshot(self.runtime)
        self.assertFalse(self.invoke(*decision)["decision_recorded"])
        self.assertEqual(runtime_snapshot(self.runtime), before)
        approved = self.invoke(*decision, "--confirm")
        self.assertEqual(approved["claim_approval_status"], "approved")
        before = runtime_snapshot(self.runtime)
        resumed = self.invoke("profile", "review", "--limit", "2", "--after", page["page"]["next_after"])
        self.assertEqual(resumed["pending_count"], len(FACTS) - 1)
        self.assertEqual(resumed["page"]["pending_before_count"], 1)
        self.assertEqual([item["claim"]["canonical_text"] for item in resumed["items"]], list(FACTS[2:4]))
        self.assertEqual(self.invoke(*decision, "--confirm"), approved)
        self.assertTrue(self.invoke(*arguments)["review_required"])
        self.assertEqual(runtime_snapshot(self.runtime), before)

    def test_resume_default_keeps_its_response_and_replay_contract(self) -> None:
        arguments = self.arguments(kind=None)
        self.invoke("profile", "init")
        result = self.invoke(*arguments)
        self.assertNotIn("source_type", result)
        before = runtime_snapshot(self.runtime)
        self.assertEqual(self.invoke(*arguments, "--source-kind", "resume"), result)
        self.assertNotIn("source_type", self.invoke(*arguments, "--dry-run"))
        self.invoke(*arguments, "--source-kind", "user-statement", expected=2)
        self.assertEqual(runtime_snapshot(self.runtime), before)
        self.assertTrue(all(item["claim"]["source_type"] == "imported_resume"
                            for item in self.invoke("profile", "review")["items"]))

    def test_statement_key_cannot_be_replayed_as_a_resume(self) -> None:
        arguments = self.arguments()
        self.invoke("profile", "init")
        retained = self.invoke(*arguments)
        before = runtime_snapshot(self.runtime)
        wrong = arguments.copy()
        wrong[wrong.index("--source-kind") + 1] = "resume"
        self.invoke(*wrong, expected=2)
        self.assertEqual(self.invoke(*arguments), retained)
        self.assertEqual(runtime_snapshot(self.runtime), before)

    def test_each_input_can_use_stdin_but_two_cannot(self) -> None:
        arguments = self.arguments()
        for option, text in (("--source-file", self.source_path.read_text()),
                             ("--proposals-file", self.manifest_path.read_text())):
            changed = arguments.copy()
            changed[changed.index(option) + 1] = "-"
            with self.subTest(option=option), patch("sys.stdin", io.StringIO(text)):
                self.assertEqual(self.invoke(*changed, "--dry-run")["source_type"], "user_statement")
        for option in ("--source-file", "--proposals-file"):
            arguments[arguments.index(option) + 1] = "-"
        with patch("grounded_apply.cli._read_utf8_input") as read:
            self.invoke(*arguments, "--dry-run", expected=2)
            read.assert_not_called()
        self.assertFalse(self.runtime.exists())

    def test_document_options_and_unknown_origins_fail_before_reading_inputs(self) -> None:
        arguments = self.arguments()
        for extra in (("--source-format", "auto"), ("--source-format", "latex"),
                      ("--source-format", "pdf"), ("--document-sha256", "0" * 64),
                      ("--allow-partial",), ("--source-kind", "inferred")):
            with self.subTest(extra=extra), patch("grounded_apply.cli._read_utf8_input") as text_read, \
                    patch("grounded_apply.cli._read_resume_input") as document_read:
                self.invoke(*arguments, *extra, "--dry-run", expected=2)
                text_read.assert_not_called()
                document_read.assert_not_called()
        self.assertFalse(self.runtime.exists())

    def test_manifest_cannot_set_origin_or_approval_authority(self) -> None:
        arguments = self.arguments()
        _, manifest = statement_fixture()
        for field, value in (("source_type", "user_statement"), ("approval_status", "approved"),
                             ("verified_by", "synthetic-reviewer")):
            changed = json.loads(json.dumps(manifest))
            changed["proposals"][0][field] = value
            self.manifest_path.write_text(json.dumps(changed), encoding="utf-8")
            self.invoke(*arguments, "--dry-run", expected=2)
        self.assertFalse(self.runtime.exists())

    def test_changed_answer_or_sensitive_text_cannot_be_stored(self) -> None:
        arguments = self.arguments()
        self.source_path.write_text("Changed exact fictional answer.\n", encoding="utf-8")
        self.invoke(*arguments, "--dry-run", expected=2)
        source = "My passport number is X12345678."
        from hashlib import sha256
        manifest = {"schema_version": 2, "source_sha256": sha256(source.encode()).hexdigest(),
            "span_index_base": 0, "span_unit": "unicode_codepoint", "span_end": "exclusive",
            "proposals": [{"claim_type": "research_description", "value": source,
                "canonical_text": source, "span": {"start": 0, "end": len(source), "text": source}}]}
        self.source_path.write_text(source, encoding="utf-8")
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.invoke(*arguments, "--dry-run", expected=2)
        self.assertFalse(self.runtime.exists())


if __name__ == "__main__":
    unittest.main()
