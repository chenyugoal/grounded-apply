from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.check_onboarding import runtime_snapshot
from tests import test_onboarding_cli as onboarding


TEXT = """Name: Avery Quill
Publications
Quill, A. Fictional Widgets; submitted, not accepted.
Research Interests
Interested in synthetic widget evaluation.
Education
PhD in Computer Science, Example University, expected May 2027.
Academic Research
Contributed fictional evaluation fixtures; did not lead the study.
Research
Built synthetic Python evaluation tools.
Selected Research
Studied fictional widget examples.
Publications
Quill, A. More Fictional Widgets; under review.
Professional Memberships
Member of Fictional Widget Society.
Skills
Python
"""
HEADINGS = ("Research Interests", "Academic Research", "Selected Research", "Professional Memberships")
UNCLASSIFIED = (
    "Interested in synthetic widget evaluation.",
    "Contributed fictional evaluation fixtures; did not lead the study.",
    "Studied fictional widget examples.",
    "Member of Fictional Widget Society.",
)


class NeutralSectionCliTests(unittest.TestCase):
    setUp = onboarding.OnboardingCLITests.setUp
    invoke = onboarding.OnboardingCLITests.invoke
    source = onboarding.OnboardingCLITests.source
    onboarding = onboarding.OnboardingCLITests.onboarding

    def extract(self, source: Path, version: str = "4") -> dict:
        return self.invoke("profile", "extract", "--source-file", str(source), "--extractor-version", version)

    def test_new_inventory_exposes_classification_gaps_without_changing_document_identity(self) -> None:
        source = self.source(TEXT, "fictional-neutral.txt")
        before = source.read_bytes(), source.stat().st_mtime_ns
        old = self.extract(source, "3")
        new = self.extract(source)
        self.assertEqual(new["extractor"], "grounded-apply.exact-resume-lines@4")
        self.assertEqual(new["extractor_version"], 4)
        self.assertEqual(new["document"], old["document"])
        self.assertEqual(new["source_sha256"], old["source_sha256"])
        self.assertFalse(new["document"]["incomplete"])
        self.assertEqual(new["skipped_lines"], 4)
        self.assertEqual(len(new["proposals"]), 6)
        self.assertEqual(len(old["proposals"]), 14)
        self.assertEqual(len(new["inventory"]), len(TEXT.splitlines()))
        for line in new["inventory"]:
            self.assertEqual(TEXT[line["start"]:line["end"]], line["text"])
            if line["text"] in HEADINGS:
                self.assertEqual(line["status"], "heading")
                self.assertIsNone(line["proposal_index"])
            if line["text"] in UNCLASSIFIED:
                self.assertEqual(line["status"], "unclassified")
                self.assertIsNone(line["proposal_index"])
        self.assertEqual((source.read_bytes(), source.stat().st_mtime_ns), before)
        self.assertFalse(self.runtime.exists())

    def test_complete_intake_keeps_unknown_lines_visible_and_supported_facts_pending(self) -> None:
        source = self.source(TEXT, "fictional-neutral.txt")
        extraction = self.extract(source)
        args = self.onboarding(source, extraction) + ["--extractor-version", "4"]
        preview = self.invoke(*args, "--dry-run")
        self.assertEqual(preview["selected_count"], 6)
        self.assertEqual(preview["unselected_proposal_count"], 0)
        self.assertEqual(preview["lines_needing_attention"], 4)
        self.assertFalse(self.runtime.exists())
        self.invoke("profile", "init")
        retained = self.invoke(*args)
        review = self.invoke("profile", "review")
        self.assertEqual(review["pending_count"], 6)
        self.assertEqual({i["claim"]["canonical_text"] for i in review["items"]},
                         {p["canonical_text"] for p in extraction["proposals"]})
        self.assertTrue(all(i["claim"]["status"] == "needs_review" and
                            i["claim"]["approval_status"] == "pending" for i in review["items"]))
        before = runtime_snapshot(self.runtime)
        self.assertEqual(self.invoke(*args), retained)
        legacy = args.copy()
        legacy[-1] = "3"
        self.invoke(*legacy, expected=2)
        self.assertEqual(runtime_snapshot(self.runtime), before)
        self.assertEqual(self.invoke("profile", "review"), review)

    def test_indexed_selection_uses_displayed_four_and_preserves_legacy_replay(self) -> None:
        source = self.source(TEXT, "fictional-neutral.txt")
        old = self.extract(source, "3")
        legacy = self.onboarding(source, old, "fictional-old-selection") + ["--extractor-version", "3"]
        self.invoke("profile", "init")
        retained = self.invoke(*legacy)
        before = runtime_snapshot(self.runtime)
        current = self.extract(source)
        args = self.onboarding(source, current, "fictional-current-selection")
        args[args.index("--select") + 1] = "5"
        self.invoke(*args, "--dry-run", expected=2)
        args += ["--extractor-version", "4", "--retain-all-facts"]
        self.assertEqual(self.invoke(*args, "--dry-run")["selected_count"], 1)
        self.assertEqual(self.invoke(*legacy), retained)
        self.assertEqual(runtime_snapshot(self.runtime), before)
        selected = self.invoke(*args)
        review = self.invoke("profile", "review")
        selected_item = next(i for i in review["items"] if i["claim"]["id"] in selected["claim_ids"])
        self.assertEqual(selected_item["claim"]["canonical_text"], "Python")
        self.assertEqual(selected_item["claim"]["claim_type"], "skill_use")
        self.assertEqual(selected_item["claim"]["approval_status"], "pending")

    def test_exact_unclassified_tex_fact_can_be_explicitly_classified_without_text_export(self) -> None:
        text = "\\section{Publications}\nQuill, A. Fictional Widgets; submitted, not accepted.\n" \
               "\\section{Academic Research}\n" + UNCLASSIFIED[1] + "\n"
        source = self.source(text, "fictional-neutral.tex")
        extraction = self.extract(source)
        line = next(i for i in extraction["inventory"] if i["status"] == "unclassified")
        proposal = {"claim_type": "research_description", "value": line["text"],
                    "canonical_text": line["text"],
                    "span": {k: line[k] for k in ("start", "end", "text")}}
        manifest = self.workspace / "fictional-chosen-classification.json"
        manifest.write_text(json.dumps({"schema_version": 2, "source_sha256": extraction["source_sha256"],
            "span_index_base": 0, "span_unit": "unicode_codepoint", "span_end": "exclusive",
            "proposals": [proposal]}), encoding="utf-8")
        args = ["profile", "import", "--source-file", str(source), "--source-format", "auto",
                "--document-sha256", extraction["document"]["document_sha256"],
                "--proposals-file", str(manifest), "--retain-all-facts", "--idempotency-key", "fictional-chosen-classification"]
        self.assertEqual(self.invoke(*args, "--dry-run")["proposal_count"], 1)
        self.assertFalse(self.runtime.exists())
        self.invoke("profile", "init")
        self.assertTrue(self.invoke(*args)["review_required"])
        review = self.invoke("profile", "review")
        self.assertEqual(review["pending_count"], 1)
        self.assertEqual(review["items"][0]["claim"]["canonical_text"], UNCLASSIFIED[1])
        self.assertEqual(review["items"][0]["claim"]["claim_type"], "research_description")
        self.assertEqual(review["items"][0]["claim"]["approval_status"], "pending")

    def test_unsupported_versions_fail_before_document_or_runtime_access(self) -> None:
        for version in ("0", "5", "999", "false"):
            for verb in ("extract", "onboard"):
                with self.subTest(version=version, verb=verb), patch("grounded_apply.cli._read_resume_input") as read:
                    arguments = ["profile", verb, "--source-file", "fictional-not-opened.tex", "--extractor-version", version]
                    if verb == "onboard":
                        arguments += ["--select", "all", "--source-sha256", "0" * 64, "--idempotency-key", "fictional-refused"]
                    self.invoke(*arguments, expected=2)
                    read.assert_not_called()
        self.assertFalse(self.runtime.exists())


if __name__ == "__main__":
    unittest.main()
