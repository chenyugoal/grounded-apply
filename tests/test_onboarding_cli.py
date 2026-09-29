from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from grounded_apply.cli import main
from scripts.check_onboarding import (
    EVENT_FIELDS, FACTS, LATEX, PARTIAL_LATEX, UNCLASSIFIED_LATEX,
    classified_manifest, runtime_snapshot, synthetic_pdf,
)


class OnboardingCLITests(unittest.TestCase):
    def setUp(self) -> None:
        workspace = tempfile.TemporaryDirectory(prefix="grounded-apply-onboarding-test-")
        self.addCleanup(workspace.cleanup)
        self.workspace = Path(workspace.name).resolve()
        self.runtime = self.workspace / "runtime"
        environment = patch.dict(os.environ, {"GROUNDED_APPLY_HOME": str(self.runtime)})
        environment.start()
        self.addCleanup(environment.stop)

    def invoke(self, *args: str, expected: int = 0) -> dict:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["--log-events", *args, "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, expected, payload)
        self.assertIs(payload["ok"], expected == 0)
        events = [json.loads(line) for line in stderr.getvalue().splitlines()]
        self.assertTrue(events)
        self.assertTrue(all(set(event) == EVENT_FIELDS for event in events))
        for forbidden in ("Avery", "Quill", "example.com", "fictional-intake", str(self.workspace),
                          "Unsupported contribution", "synthetic-private-cursor"):
            self.assertNotIn(forbidden, stderr.getvalue())
        return payload["data"] if expected == 0 else payload

    def source(self, text: str = LATEX, filename: str = "fictional-resume.tex") -> Path:
        path = self.workspace / filename
        path.write_text(text, encoding="utf-8")
        return path

    def onboarding(self, source: Path, extraction: dict, key: str = "fictional-intake") -> list[str]:
        return ["profile", "onboard", "--source-file", str(source), "--select", "all",
                "--source-sha256", extraction["source_sha256"],
                "--document-sha256", extraction["document"]["document_sha256"],
                "--idempotency-key", key]

    def test_tex_extraction_and_all_fact_preview_need_no_export_or_runtime(self) -> None:
        source = self.source()
        before = source.read_bytes(), source.stat().st_mtime_ns
        result = self.invoke("profile", "extract", "--source-file", str(source))
        self.assertEqual(result["document"]["format"], "latex")
        self.assertEqual(result["document"]["document_sha256"], sha256(before[0]).hexdigest())
        self.assertEqual(result["document"]["extracted_text_sha256"], result["source_sha256"])
        self.assertFalse(result["document"]["incomplete"])
        self.assertEqual([p["canonical_text"] for p in result["proposals"]], list(FACTS))
        self.assertEqual(sum(line["status"] == "proposed" for line in result["inventory"]), len(FACTS))
        preview = self.invoke(*self.onboarding(source, result), "--dry-run")
        self.assertEqual(preview["selected_count"], len(FACTS))
        self.assertEqual(preview["unselected_proposal_count"], 0)
        self.assertEqual(preview["content_policy_version"], 3)
        self.assertFalse(preview["storage_checked"])
        self.assertTrue(preview["review_required"])
        self.assertFalse(self.runtime.exists())
        self.assertEqual(set(self.workspace.iterdir()), {source})
        self.assertEqual((source.read_bytes(), source.stat().st_mtime_ns), before)

    def test_document_and_source_digest_mismatches_fail_before_storage(self) -> None:
        source = self.source()
        result = self.invoke("profile", "extract", "--source-file", str(source))
        args = self.onboarding(source, result)
        for option in ("--source-sha256", "--document-sha256"):
            changed = args.copy()
            changed[changed.index(option) + 1] = "0" * 64
            with self.subTest(option=option):
                self.invoke(*changed, "--dry-run", expected=2)
        index = args.index("--document-sha256")
        del args[index:index + 2]
        self.invoke(*args, "--dry-run", expected=2)
        self.assertFalse(self.runtime.exists())

    def test_all_facts_remain_pending_then_explicit_approval_preserves_exact_values(self) -> None:
        source = self.source()
        extracted = self.invoke("profile", "extract", "--source-file", str(source))
        args = self.onboarding(source, extracted)
        self.invoke("profile", "init")
        before = runtime_snapshot(self.runtime)
        self.invoke(*args, "--dry-run")
        self.assertEqual(runtime_snapshot(self.runtime), before)
        imported = self.invoke(*args)
        review = self.invoke("profile", "review")
        self.assertEqual(review["pending_count"], len(FACTS))
        self.assertEqual({item["claim"]["canonical_text"] for item in review["items"]}, set(FACTS))
        self.assertTrue(all(item["claim"]["status"] == "needs_review" and
                            item["claim"]["approval_status"] == "pending" for item in review["items"]))
        before = runtime_snapshot(self.runtime)
        self.assertEqual(self.invoke(*args), imported)
        self.assertEqual(runtime_snapshot(self.runtime), before)
        for item in review["items"]:
            decision = ("profile", "decide", "--claim-id", item["claim"]["id"],
                        "--review-token", item["review_token"], "--decision", "approve",
                        "--actor-id", "synthetic-reviewer", "--idempotency-key", "approve-" + item["claim"]["id"])
            self.assertFalse(self.invoke(*decision)["decision_recorded"])
            self.assertTrue(self.invoke(*decision, "--confirm")["decision_recorded"])
        before = runtime_snapshot(self.runtime)
        profile = self.invoke("profile", "show")
        self.assertEqual({claim["canonical_text"] for claim in profile["claims"]}, set(FACTS))
        self.assertTrue(all(claim["status"] == "verified" and claim["approval_status"] == "approved"
                            for claim in profile["claims"]))
        self.assertEqual(self.invoke("profile", "review")["pending_count"], 0)
        replay = self.invoke(*args)
        self.assertEqual(replay["claim_ids"], imported["claim_ids"])
        self.assertEqual(replay["workflow_run_id"], imported["workflow_run_id"])
        self.assertFalse(replay["review_required"])
        self.assertEqual(runtime_snapshot(self.runtime), before)

    def test_incomplete_tex_refuses_import_until_partial_retention_is_explicit(self) -> None:
        source = self.source(PARTIAL_LATEX)
        extraction = self.invoke("profile", "extract", "--source-file", str(source))
        self.assertTrue(extraction["document"]["incomplete"])
        self.assertTrue(extraction["document"]["issues"])
        self.assertEqual(len(extraction["proposals"]), 3)
        self.assertNotIn("Unsupported contribution", json.dumps(extraction["proposals"]))
        args = self.onboarding(source, extraction)
        self.invoke(*args, "--dry-run", expected=2)
        self.invoke(*args, expected=2)
        self.assertFalse(self.runtime.exists())
        preview = self.invoke(*args, "--allow-partial", "--dry-run")
        self.assertEqual(preview["selected_count"], 3)
        self.assertTrue(preview["document"]["incomplete"])
        self.assertFalse(self.runtime.exists())
        self.invoke("profile", "init")
        imported = self.invoke(*args, "--allow-partial")
        self.assertTrue(imported["review_required"])
        self.assertEqual(self.invoke("profile", "review")["pending_count"], 3)

    def test_indexed_selection_requires_the_displayed_extractor_version(self) -> None:
        source = self.source()
        extraction = self.invoke("profile", "extract", "--source-file", str(source))
        args = self.onboarding(source, extraction)
        args[args.index("--select") + 1] = "0"
        self.invoke(*args, "--dry-run", expected=2)
        preview = self.invoke(*args, "--extractor-version", str(extraction["extractor_version"]), "--dry-run")
        self.assertEqual(preview["selected_count"], 1)
        self.assertEqual(preview["unselected_proposal_count"], len(FACTS) - 1)
        self.assertEqual(preview["content_policy_version"], 2)
        self.assertFalse(self.runtime.exists())

    def test_changed_document_with_identical_extracted_facts_is_not_replayed(self) -> None:
        source = self.source()
        extraction = self.invoke("profile", "extract", "--source-file", str(source))
        self.invoke("profile", "init")
        args = self.onboarding(source, extraction)
        self.invoke(*args)
        before = runtime_snapshot(self.runtime)
        source.write_text(LATEX + "% Added fictional comment.\n", encoding="utf-8")
        refreshed = self.invoke("profile", "extract", "--source-file", str(source))
        self.assertEqual(refreshed["source_sha256"], extraction["source_sha256"])
        self.assertNotEqual(refreshed["document"]["document_sha256"], extraction["document"]["document_sha256"])
        self.invoke(*args, expected=2)
        self.assertEqual(runtime_snapshot(self.runtime), before)

    def test_unclassified_document_span_can_be_imported_without_a_plain_text_export(self) -> None:
        source = self.source(UNCLASSIFIED_LATEX)
        extracted = self.invoke("profile", "extract", "--source-file", str(source))
        self.assertEqual(extracted["proposals"], [])
        self.assertEqual(extracted["inventory"][0]["status"], "unclassified")
        manifest = self.workspace / "fictional-classification.json"
        manifest.write_text(json.dumps(classified_manifest(extracted)), encoding="utf-8")
        args = ["profile", "import", "--source-file", str(source), "--source-format", "auto",
                "--document-sha256", extracted["document"]["document_sha256"],
                "--proposals-file", str(manifest), "--retain-all-facts",
                "--idempotency-key", "fictional-classification"]
        self.assertEqual(self.invoke(*args, "--dry-run")["proposal_count"], 1)
        bad = args.copy()
        bad[bad.index("--document-sha256") + 1] = "0" * 64
        self.invoke(*bad, "--dry-run", expected=2)
        self.assertEqual(set(self.workspace.iterdir()), {source, manifest})
        self.invoke("profile", "init")
        self.assertTrue(self.invoke(*args)["review_required"])
        review = self.invoke("profile", "review")
        self.assertEqual(review["pending_count"], 1)
        self.assertEqual(review["items"][0]["claim"]["canonical_text"], extracted["inventory"][0]["text"])
        self.assertEqual(review["items"][0]["claim"]["status"], "needs_review")

    @unittest.skipUnless(importlib.util.find_spec("pypdf"), "PDF intake requires the optional materials extra")
    def test_actual_pdf_extracts_all_facts_and_imports_only_pending_claims(self) -> None:
        source = self.workspace / "fictional-resume.pdf"
        raw = synthetic_pdf()
        source.write_bytes(raw)
        extracted = self.invoke("profile", "extract", "--source-file", str(source))
        self.assertEqual(extracted["document"]["format"], "pdf")
        self.assertEqual(extracted["document"]["page_count"], 1)
        self.assertEqual(extracted["document"]["document_sha256"], sha256(raw).hexdigest())
        self.assertEqual([p["canonical_text"] for p in extracted["proposals"]], list(FACTS))
        self.assertFalse(self.runtime.exists())
        args = self.onboarding(source, extracted)
        self.assertEqual(self.invoke(*args, "--dry-run")["selected_count"], len(FACTS))
        self.assertFalse(self.runtime.exists())
        self.invoke("profile", "init")
        self.assertTrue(self.invoke(*args)["review_required"])
        self.assertEqual(self.invoke("profile", "review")["pending_count"], len(FACTS))
        self.assertEqual(source.read_bytes(), raw)

    def test_interview_cursor_and_stop_guidance_do_not_create_runtime(self) -> None:
        with patch("socket.create_connection", side_effect=AssertionError("Unexpected network")):
            first = self.invoke("profile", "interview", "--topic", "research", "--depth", "2", "--limit", "1")
            next_page = self.invoke("profile", "interview", "--topic", "research", "--depth", "2",
                                    "--after", first["next_cursor"])
            self.invoke("profile", "interview", "--topic", "skills", "--depth", "2",
                        "--after", first["next_cursor"], expected=2)
            self.invoke("profile", "interview", "--after", "synthetic-private-cursor", expected=2)
        self.assertNotEqual(first["questions"][0]["id"], next_page["questions"][0]["id"])
        self.assertTrue(next_page["finished"])
        self.assertFalse(first["answers_stored"])
        self.assertFalse(first["profile_read"])
        self.assertIn("stop", " ".join(first["guidance"]))
        self.assertEqual(list(self.workspace.iterdir()), [])

    def test_source_links_build_an_offline_manifest_with_explicit_manual_gaps(self) -> None:
        with patch("socket.getaddrinfo", side_effect=AssertionError("Unexpected DNS")), \
                patch("socket.create_connection", side_effect=AssertionError("Unexpected network")):
            result = self.invoke("jobs", "sources", "--url", "https://boards.greenhouse.io/fictional-intake",
                "--url", "https://boards.greenhouse.io/fictional-intake/jobs/123",
                "--url", "https://example.com/fictional-careers")
        self.assertEqual(result["automatic_source_count"], 1)
        self.assertEqual(result["manual_source_count"], 1)
        self.assertEqual(result["network_requests"], 0)
        self.assertFalse(result["storage_changed"])
        self.assertFalse(result["live_boards_verified"])
        self.assertEqual(result["scope"], "entire_boards")
        self.assertTrue(result["inputs"][1]["duplicate"])
        self.assertEqual([source["provider"] for source in result["manifest"]["sources"]], ["greenhouse", "manual"])
        self.assertEqual(list(self.workspace.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
