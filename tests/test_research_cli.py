from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.check_onboarding import FACTS, runtime_snapshot
from tests import test_onboarding_cli as onboarding


class ResearchCliTests(unittest.TestCase):
    setUp = onboarding.OnboardingCLITests.setUp
    invoke = onboarding.OnboardingCLITests.invoke
    source = onboarding.OnboardingCLITests.source
    onboarding = onboarding.OnboardingCLITests.onboarding

    def test_explicit_extractor_three_preserves_words_spans_and_legacy_default(self) -> None:
        source = self.source()
        before = source.read_bytes(), source.stat().st_mtime_ns
        legacy = self.invoke("profile", "extract", "--source-file", str(source))
        current = self.invoke("profile", "extract", "--source-file", str(source), "--extractor-version", "3")
        self.assertEqual(legacy["extractor_version"], 2)
        self.assertEqual(current["extractor_version"], 3)
        self.assertEqual(current["source_sha256"], legacy["source_sha256"])
        self.assertEqual(current["document"], legacy["document"])
        self.assertEqual(current["inventory"], legacy["inventory"])
        self.assertEqual([p["canonical_text"] for p in current["proposals"]], list(FACTS))
        for old, new in zip(legacy["proposals"], current["proposals"], strict=True):
            self.assertEqual({k: v for k, v in old.items() if k != "claim_type"},
                             {k: v for k, v in new.items() if k != "claim_type"})
        self.assertEqual([p["claim_type"] for p in current["proposals"]][2:4], ["research_description"] * 2)
        self.assertEqual([p["claim_type"] for p in legacy["proposals"]][2:4], ["employment_description"] * 2)
        self.assertFalse(self.runtime.exists())
        self.assertEqual((source.read_bytes(), source.stat().st_mtime_ns), before)

    def test_all_fact_intake_is_pending_and_replay_cannot_silently_reclassify(self) -> None:
        source = self.source()
        extracted = self.invoke("profile", "extract", "--source-file", str(source), "--extractor-version", "3")
        args = self.onboarding(source, extracted) + ["--extractor-version", "3"]
        self.assertEqual(self.invoke(*args, "--dry-run")["selected_count"], len(FACTS))
        self.assertFalse(self.runtime.exists())
        self.invoke("profile", "init")
        retained = self.invoke(*args)
        before = runtime_snapshot(self.runtime)
        review = self.invoke("profile", "review")
        self.assertEqual(review["pending_count"], len(FACTS))
        self.assertEqual(sum(item["claim"]["claim_type"] == "research_description" for item in review["items"]), 2)
        self.assertTrue(all(item["claim"]["status"] == "needs_review" and
                            item["claim"]["approval_status"] == "pending" for item in review["items"]))
        self.assertEqual(self.invoke(*args), retained)
        legacy = args.copy()
        legacy[-1] = "2"
        self.invoke(*legacy, expected=2)
        self.assertEqual(runtime_snapshot(self.runtime), before)
        self.assertEqual(self.invoke("profile", "review"), review)

    def test_numeric_research_selection_uses_the_displayed_version(self) -> None:
        source = self.source()
        extracted = self.invoke("profile", "extract", "--source-file", str(source), "--extractor-version", "3")
        args = self.onboarding(source, extracted)
        args[args.index("--select") + 1] = "2"
        self.invoke(*args, "--dry-run", expected=2)
        self.assertFalse(self.runtime.exists())
        args += ["--extractor-version", "3", "--retain-all-facts"]
        self.assertEqual(self.invoke(*args, "--dry-run")["selected_count"], 1)
        self.invoke("profile", "init")
        self.invoke(*args)
        review = self.invoke("profile", "review")
        self.assertEqual(review["pending_count"], 1)
        self.assertEqual(review["items"][0]["claim"]["claim_type"], "research_description")
        self.assertEqual(review["items"][0]["claim"]["canonical_text"], FACTS[2])

    def test_unsupported_extractor_versions_fail_before_runtime_or_document_reads(self) -> None:
        for version in ("0", "5", "999", "false"):
            with self.subTest(version=version), patch("grounded_apply.cli._read_resume_input") as read:
                self.invoke("profile", "extract", "--source-file", "fictional-not-opened.tex",
                            "--extractor-version", version, expected=2)
                read.assert_not_called()
        self.assertFalse(self.runtime.exists())


if __name__ == "__main__":
    unittest.main()
