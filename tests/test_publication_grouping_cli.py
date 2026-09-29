from __future__ import annotations

import importlib.util
import io
import json
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from grounded_apply.cli import main
from scripts.check_onboarding import EVENT_FIELDS, runtime_snapshot, synthetic_pdf
from tests import test_onboarding_cli as onboarding


TITLE = "- Quill, A. Fictional Evaluation Protocols."
STATUS = "Submitted September 2026; under review, not accepted."
TEXT = ("Name: Avery Quill\nResearch\nContributed fictional evaluation; did not lead the study.\n"
        "Publications\n" + TITLE + "\n  " + STATUS + "\n"
        "Quill, A. Fictional Taxonomy; in preparation, not submitted.\n"
        "Research Interests\nReliable fictional evaluation.\nSkills\nPython\n")
TEX = ("\\begin{document}\nName: Avery Quill\n\\section{Research}\n"
       "Contributed fictional evaluation; did not lead the study.\n"
       "\\section{Publications}\n" + TITLE + "\\\\\n  " + STATUS + "\\\\\n"
       "Quill, A. Fictional Taxonomy; in preparation, not submitted.\n"
       "\\section{Research Interests}\nReliable fictional evaluation.\\\\\n"
       "\\section{Skills}\nPython\n\\end{document}\n")
SECOND_TITLE = "Quill, A. Fictional Taxonomy."
SECOND_STATUS = "Manuscript in preparation; not submitted."
MULTI_TEXT = TEXT.replace("Quill, A. Fictional Taxonomy; in preparation, not submitted.",
                          SECOND_TITLE + "\n  " + SECOND_STATUS)


class PublicationGroupingCliTests(unittest.TestCase):
    setUp = onboarding.OnboardingCLITests.setUp
    invoke = onboarding.OnboardingCLITests.invoke
    source = onboarding.OnboardingCLITests.source

    def extract(self, source: Path, version: str = "4") -> dict:
        return self.invoke("profile", "extract", "--source-file", str(source), "--extractor-version", version)

    def grouping(self, source: Path, extraction: dict, indexes: str = "2,3") -> list[str]:
        return ["profile", "group-publication", "--source-file", str(source),
                "--source-sha256", extraction["source_sha256"],
                "--document-sha256", extraction["document"]["document_sha256"],
                "--extractor-version", str(extraction["extractor_version"]), "--indexes", indexes]

    def no_runtime(self) -> ExitStack:
        stack = ExitStack()
        for target in ("grounded_apply.cli.resolve_runtime_paths",
                       "grounded_apply.cli._open_initialized_profile_repository", "sqlite3.connect",
                       "socket.getaddrinfo", "socket.create_connection"):
            stack.enter_context(patch(target, side_effect=AssertionError("PRIVATE_UNEXPECTED_ACCESS")))
        return stack

    def assert_complete_group(self, result: dict, extracted: dict) -> None:
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["grouped_indexes"], [2, 3])
        self.assertEqual(result["grouped_manifest_index"], 2)
        self.assertEqual(result["original_proposal_count"], 6)
        self.assertEqual(result["manifest_proposal_count"], 5)
        self.assertEqual(result["index_mapping"], [
            {"original_index": i, "manifest_index": new} for i, new in enumerate((0, 1, 2, 2, 3, 4))])
        self.assertEqual(result["inventory"], extracted["inventory"])
        self.assertEqual(result["skipped_lines"], 1)
        self.assertEqual(result["unclassified_count"], 1)
        self.assertEqual(result["blocked_count"], 0)
        self.assertTrue(result["read_only"] and result["review_required"])
        self.assertFalse(result["storage_changed"] or result["profile_read"])
        self.assertEqual(result["network_requests"], 0)
        self.assertEqual(result["content_trust"], "untrusted")
        self.assertEqual(result["document"], extracted["document"])
        manifest = result["manifest"]
        self.assertEqual(set(manifest), {"schema_version", "source_sha256", "span_index_base",
                                        "span_unit", "span_end", "proposals"})
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["source_sha256"], extracted["source_sha256"])
        self.assertEqual(manifest["proposals"][2]["canonical_text"], TITLE + " " + STATUS)
        self.assertEqual(manifest["proposals"][2]["value"], TITLE + " " + STATUS)
        for old, new in ((0, 0), (1, 1), (4, 3), (5, 4)):
            for field in ("claim_type", "canonical_text", "value", "span", "confidence"):
                self.assertEqual(manifest["proposals"][new][field], extracted["proposals"][old][field])

    def test_text_groups_exact_whitespace_and_imports_all_facts_only_pending_with_quiet_replay(self) -> None:
        source = self.source(TEXT, "fictional-wrapped.txt")
        extracted = self.extract(source)
        before_source = source.read_bytes(), source.stat().st_mtime_ns, source.stat().st_ino
        with self.no_runtime(), patch("subprocess.Popen", side_effect=AssertionError("PRIVATE_PROCESS")):
            grouped = self.invoke(*self.grouping(source, extracted))
        self.assert_complete_group(grouped, extracted)
        span = grouped["manifest"]["proposals"][2]["span"]
        self.assertEqual(span["text"], TITLE + "\n  " + STATUS)
        self.assertEqual(TEXT[span["start"]:span["end"]], span["text"])
        self.assertFalse(self.runtime.exists())
        self.assertEqual(set(self.workspace.iterdir()), {source})
        manifest = self.workspace / "fictional-grouped.json"
        manifest.write_text(json.dumps(grouped["manifest"]), encoding="utf-8")
        args = ["profile", "import", "--source-file", str(source), "--source-format", "auto",
                "--document-sha256", extracted["document"]["document_sha256"],
                "--proposals-file", str(manifest), "--retain-all-facts", "--idempotency-key", "fictional-grouped"]
        self.assertEqual(self.invoke(*args, "--dry-run")["proposal_count"], 5)
        self.assertFalse(self.runtime.exists())
        self.invoke("profile", "init")
        retained = self.invoke(*args)
        saved = runtime_snapshot(self.runtime)
        review = self.invoke("profile", "review")
        self.assertEqual(review["pending_count"], 5)
        self.assertTrue(all(i["claim"]["status"] == "needs_review" and
                            i["claim"]["approval_status"] == "pending" and not i["usable"]
                            for i in review["items"]))
        item = next(i for i in review["items"] if i["claim"]["canonical_text"] == TITLE + " " + STATUS)
        self.assertEqual(item["evidence"][0]["source_text"], span["text"])
        self.assertEqual(item["evidence"][0]["locator"]["start"], span["start"])
        self.assertEqual(item["evidence"][0]["locator"]["end"], span["end"])
        self.assertEqual(self.invoke(*args), retained)
        self.assertEqual(self.invoke(*self.grouping(source, extracted)), grouped)
        self.assertEqual(runtime_snapshot(self.runtime), saved)
        self.assertEqual((source.read_bytes(), source.stat().st_mtime_ns, source.stat().st_ino), before_source)

    @unittest.skipUnless(importlib.util.find_spec("pypdf"), "pypdf is required")
    def test_actual_pdf_keeps_gap_bytes_and_other_proposals_without_export_or_runtime(self) -> None:
        source = self.workspace / "fictional-wrapped.pdf"
        source.write_bytes(synthetic_pdf(TEXT))
        extracted = self.extract(source)
        before = source.read_bytes(), source.stat().st_mtime_ns
        with self.no_runtime():
            grouped = self.invoke(*self.grouping(source, extracted))
        self.assert_complete_group(grouped, extracted)
        self.assertEqual(grouped["manifest"]["proposals"][2]["span"]["text"], TITLE + "\n  " + STATUS)
        self.assertEqual((source.read_bytes(), source.stat().st_mtime_ns), before)
        self.assertEqual(set(self.workspace.iterdir()), {source})

    def test_tex_gaps_require_acknowledgement_without_executing_or_guessing_content(self) -> None:
        source = self.source(TEX.replace("\\section{Skills}", "\\input{PRIVATE_INSTRUCTION}\n\\section{Skills}"))
        extracted = self.extract(source)
        self.assertTrue(extracted["document"]["incomplete"])
        with self.no_runtime(), patch("subprocess.Popen", side_effect=AssertionError("PRIVATE_EXECUTION")):
            refusal = self.invoke(*self.grouping(source, extracted), expected=2)
            grouped = self.invoke(*self.grouping(source, extracted), "--allow-partial")
        self.assertIn("incomplete", refusal["error"]["message"])
        self.assert_complete_group(grouped, extracted)
        self.assertTrue(grouped["document"]["issues"])
        self.assertFalse(grouped["document"]["original_document_provenance_stored"])
        self.assertFalse(self.runtime.exists())

    def test_valid_text_stdin_uses_the_same_hash_bound_manifest_and_creates_nothing(self) -> None:
        source = self.source(TEXT, "fictional-wrapped.txt")
        extracted = self.extract(source)
        args = self.grouping(Path("-"), extracted) + ["--source-format", "text"]
        with self.no_runtime(), patch("sys.stdin", io.StringIO(TEXT)):
            grouped = self.invoke(*args)
        self.assert_complete_group(grouped, extracted)
        self.assertEqual(set(self.workspace.iterdir()), {source})

    def test_invalid_shapes_and_required_flags_fail_before_reading_any_input(self) -> None:
        base = self.grouping(Path("PRIVATE_SOURCE"), {"source_sha256": "a" * 64,
                             "document": {"document_sha256": "b" * 64}, "extractor_version": 4})
        cases = []
        for option, value in (("--source-sha256", "PRIVATE_HASH"), ("--document-sha256", "PRIVATE_HASH"),
                              ("--indexes", "PRIVATE_INDEX"), ("--indexes", "2"), ("--indexes", "3,2"),
                              ("--indexes", "2,2"), ("--indexes", "2,4"), ("--indexes", "-1,0"),
                              ("--indexes", "2,1000"), ("--indexes", "2,٣"), ("--indexes", "1," + "7" * 7000),
                              ("--extractor-version", "5"), ("--source-format", "PRIVATE_FORMAT")):
            args = base.copy()
            if option in args:
                args[args.index(option) + 1] = value
            else:
                args.extend((option, value))
            cases.append(args)
        for required in ("--document-sha256", "--extractor-version", "--source-sha256", "--indexes"):
            args = base.copy()
            start = args.index(required)
            del args[start:start + 2]
            cases.append(args)
        cases.append(base + ["--unknown", "PRIVATE_VALUE"])
        with self.no_runtime(), patch("grounded_apply.cli._read_resume_input") as read:
            for args in cases:
                with self.subTest(args=args[-2:]):
                    envelope = self.invoke(*args, expected=2)
                    self.assertIsNone(envelope["data"])
                    self.assertNotIn("PRIVATE", json.dumps(envelope))
            read.assert_not_called()

    def test_stale_original_or_text_hash_refuses_without_touching_existing_profile(self) -> None:
        source = self.source(TEXT, "fictional-wrapped.txt")
        extracted = self.extract(source)
        self.invoke("profile", "init")
        before = runtime_snapshot(self.runtime)
        for option in ("--source-sha256", "--document-sha256"):
            args = self.grouping(source, extracted)
            args[args.index(option) + 1] = "0" * 64
            with self.no_runtime():
                self.invoke(*args, expected=2)
        self.assertEqual(runtime_snapshot(self.runtime), before)

    def test_human_output_keeps_blocked_content_private_and_explains_inventory_and_approval(self) -> None:
        source = self.source(TEXT.replace("Reliable fictional evaluation.", "Reliable\u202e PRIVATE_MARKER."), "fictional-wrapped.txt")
        extracted = self.extract(source)
        output, errors = io.StringIO(), io.StringIO()
        with self.no_runtime(), redirect_stdout(output), redirect_stderr(errors):
            code = main(self.grouping(source, extracted))
        self.assertEqual(code, 0, errors.getvalue())
        self.assertNotIn("\u202e", output.getvalue())
        self.assertNotIn("PRIVATE_MARKER", output.getvalue())
        self.assertIn("1 blocked", output.getvalue())
        for wording in ("all other proposals remain", "unclassified", "No profile was read",
                        "explicit approval", "under review, not accepted"):
            self.assertIn(wording, output.getvalue())
        self.assertFalse(self.runtime.exists())

    def test_private_unexpected_read_and_output_errors_are_fixed(self) -> None:
        from grounded_apply.cli import _emit
        source = self.source(TEXT, "fictional-wrapped.txt")
        extracted = self.extract(source)
        args = self.grouping(source, extracted)
        def fail_output(namespace, **kwargs):
            if kwargs.get("data") is not None:
                raise OSError("PRIVATE_SOURCE_OUTPUT")
            return _emit(namespace, **kwargs)
        for target, failure, expected in (
            ("grounded_apply.cli._read_resume_input", OSError("PRIVATE_SOURCE_READ"), "Publication grouping failed"),
            ("grounded_apply.cli._emit", fail_output, "Publication grouping output failed"),
            ("grounded_apply.domain.to_jsonable", ValueError("PRIVATE_SERIALIZER"), "Publication grouping output failed"),
            ("grounded_apply.cli._terminal_safe", ValueError("PRIVATE_FORMATTER"), "Publication grouping output failed"),
        ):
            with self.subTest(target=target), patch(target, side_effect=failure):
                envelope = self.invoke(*args, expected=2)
            self.assertIn(expected, envelope["error"]["message"])
            self.assertNotIn("PRIVATE", json.dumps(envelope))
        self.assertFalse(self.runtime.exists())

    def test_human_usage_failures_do_not_echo_private_input(self) -> None:
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = main(["profile", "group-publication", "--source-file", "PRIVATE_FILENAME", "--indexes", "PRIVATE_INDEX"])
        self.assertEqual(code, 2)
        self.assertNotIn("PRIVATE", output.getvalue() + errors.getvalue())
        self.assertIn("Invalid command arguments", errors.getvalue())

    def test_grouping_diagnostics_use_only_the_registered_fixed_command(self) -> None:
        source = self.source(TEXT, "fictional-wrapped.txt")
        extracted = self.extract(source)
        output, errors = io.StringIO(), io.StringIO()
        with self.no_runtime(), redirect_stdout(output), redirect_stderr(errors):
            self.assertEqual(main(["--log-events", *self.grouping(source, extracted), "--json"]), 0)
        events = [json.loads(line) for line in errors.getvalue().splitlines()]
        self.assertEqual(len(events), 2)
        self.assertTrue(all(set(event) == EVENT_FIELDS and event["command"] == "profile.group-publication" for event in events))
        self.assertEqual([e["outcome"] for e in events], ["started", "succeeded"])
        self.assertNotIn("Quill", errors.getvalue())
        self.assertNotIn(str(self.workspace), errors.getvalue())

    def test_interruption_preserves_read_only_state_and_fixed_diagnostics(self) -> None:
        source = self.source(TEXT, "fictional-wrapped.txt")
        extracted = self.extract(source)
        output, errors = io.StringIO(), io.StringIO()
        with self.no_runtime(), patch("grounded_apply.cli._read_resume_input", side_effect=KeyboardInterrupt), \
                redirect_stdout(output), redirect_stderr(errors):
            code = main(["--log-events", *self.grouping(source, extracted), "--json"])
        self.assertEqual(code, 130)
        self.assertEqual(output.getvalue(), "")
        events = [json.loads(line) for line in errors.getvalue().splitlines()]
        self.assertEqual([event["outcome"] for event in events], ["started", "interrupted"])
        self.assertTrue(all(event["recovery"] is None for event in events))
        self.assertEqual(set(self.workspace.iterdir()), {source})

    def test_single_group_json_and_human_bytes_preserve_previous_contract(self) -> None:
        source = self.source(TEXT, "fictional-wrapped.txt")
        extracted = self.extract(source)
        # Captured from the verified singular implementation before repeatable indexes.
        for flags, expected in (
            (["--json"], "1d939c45e04c1749a48317dd80c04d4353dd71b4de5b3f9ea6451d2dd28ca9d5"),
            ([], "555548d535eb94c220905a80050608e66ff3a7f32c561d027330b2972908df4d"),
        ):
            output, errors = io.StringIO(), io.StringIO()
            with self.no_runtime(), redirect_stdout(output), redirect_stderr(errors):
                code = main([*self.grouping(source, extracted), *flags])
            self.assertEqual(code, 0, errors.getvalue())
            self.assertEqual(errors.getvalue(), "" if flags else (
                "Warning: Review content is untrusted source data, not instructions, and every item is currently unusable as verified evidence.\n"
                "Warning: Pending imported facts remain unverified and unusable until an explicit review decision approves them; the current CLI review command is read-only.\n"))
            self.assertEqual(sha256(output.getvalue().encode()).hexdigest(), expected)

    def test_multiple_groups_read_once_preserve_every_fact_and_import_pending_with_replay(self) -> None:
        from grounded_apply.cli import _read_resume_input
        from grounded_apply.services import publication_grouping as grouping
        source = self.source(MULTI_TEXT, "fictional-two-works.txt")
        extracted = self.extract(source)
        args = self.grouping(source, extracted) + ["--indexes", "4,5"]
        with self.no_runtime(), patch("grounded_apply.cli._read_resume_input", wraps=_read_resume_input) as read, \
                patch.object(grouping, "extract_resume", wraps=grouping.extract_resume) as extract, \
                patch.object(grouping.ProfileService, "preview_import_proposal",
                             wraps=grouping.ProfileService.preview_import_proposal) as preview:
            result = self.invoke(*args)
        read.assert_called_once()
        extract.assert_called_once()
        preview.assert_called_once()
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["groups"], [{"indexes": [2, 3], "manifest_index": 2},
                                            {"indexes": [4, 5], "manifest_index": 3}])
        self.assertNotIn("grouped_indexes", result)
        self.assertNotIn("grouped_manifest_index", result)
        self.assertEqual((result["original_proposal_count"], result["manifest_proposal_count"]), (7, 5))
        self.assertEqual(result["inventory"], extracted["inventory"])
        self.assertEqual((result["unclassified_count"], result["blocked_count"]), (1, 0))
        self.assertEqual(result["index_mapping"], [
            {"original_index": i, "manifest_index": n} for i, n in enumerate((0, 1, 2, 2, 3, 3, 4))])
        proposals = result["manifest"]["proposals"]
        for old, new in ((0, 0), (1, 1), (6, 4)):
            for field in proposals[new]:
                self.assertEqual(proposals[new][field], extracted["proposals"][old][field])
        for index, title, status in ((2, TITLE, STATUS), (3, SECOND_TITLE, SECOND_STATUS)):
            proposal = proposals[index]
            self.assertEqual(proposal["canonical_text"], title + " " + status)
            self.assertEqual(proposal["value"], proposal["canonical_text"])
            self.assertEqual(proposal["span"]["text"], title + "\n  " + status)
            self.assertEqual(MULTI_TEXT[proposal["span"]["start"]:proposal["span"]["end"]], proposal["span"]["text"])
        reversed_result = self.invoke(*self.grouping(source, extracted, "4,5"), "--indexes", "2,3")
        self.assertEqual(reversed_result["groups"], list(reversed(result["groups"])))
        self.assertEqual(reversed_result["manifest"], result["manifest"])
        self.assertEqual(reversed_result["index_mapping"], result["index_mapping"])
        self.assertFalse(self.runtime.exists())
        self.assertEqual(set(self.workspace.iterdir()), {source})
        manifest = self.workspace / "fictional-two-works.json"
        manifest.write_text(json.dumps(result["manifest"]), encoding="utf-8")
        import_args = ["profile", "import", "--source-file", str(source), "--source-format", "auto",
                       "--document-sha256", extracted["document"]["document_sha256"],
                       "--proposals-file", str(manifest), "--retain-all-facts", "--idempotency-key", "fictional-multiple"]
        self.assertEqual(self.invoke(*import_args, "--dry-run")["proposal_count"], 5)
        self.assertFalse(self.runtime.exists())
        self.invoke("profile", "init")
        retained = self.invoke(*import_args)
        saved = runtime_snapshot(self.runtime)
        review = self.invoke("profile", "review")
        self.assertEqual(review["pending_count"], 5)
        self.assertTrue(all(i["claim"]["status"] == "needs_review" and
                            i["claim"]["approval_status"] == "pending" and not i["usable"]
                            for i in review["items"]))
        for proposal in proposals:
            item = next(i for i in review["items"] if i["claim"]["canonical_text"] == proposal["canonical_text"])
            self.assertEqual(item["evidence"][0]["source_text"], proposal["span"]["text"])
        self.assertEqual(self.invoke(*import_args), retained)
        self.assertEqual(self.invoke(*args), result)
        self.assertEqual(runtime_snapshot(self.runtime), saved)

    def test_multiple_groups_support_stdin_without_reading_it_twice(self) -> None:
        source = self.source(MULTI_TEXT, "fictional-two-works.txt")
        extracted = self.extract(source)
        expected = self.invoke(*self.grouping(source, extracted), "--indexes", "4,5")
        with self.no_runtime(), patch("sys.stdin", io.StringIO(MULTI_TEXT)):
            actual = self.invoke(*self.grouping(Path("-"), extracted), "--indexes", "4,5", "--source-format", "text")
        self.assertEqual(actual, expected)
        self.assertEqual(set(self.workspace.iterdir()), {source})

    def test_multiple_group_shape_overlap_and_aggregate_bounds_refuse_before_document_read(self) -> None:
        base = self.grouping(Path("PRIVATE_SOURCE"), {"source_sha256": "a" * 64,
                             "document": {"document_sha256": "b" * 64}, "extractor_version": 4})
        cases = [base + ["--indexes", value] for value in ("2,3", "3,4", "1,2", "4", "5,4", "PRIVATE_GROUP")]
        cases.append(base + ["--indexes", "4,5"] * 500)
        cases.append(base + ["--indexes", "4,5" + "0" * 6000])
        full = ",".join(map(str, range(1000)))
        cases.append(self.grouping(Path("PRIVATE_SOURCE"), {"source_sha256": "a" * 64,
                     "document": {"document_sha256": "b" * 64}, "extractor_version": 4}, full) + ["--indexes", "0,1"])
        with self.no_runtime(), patch("grounded_apply.cli._read_resume_input") as read:
            for args in cases:
                with self.subTest(length=len(args), tail=args[-1][:20]):
                    result = self.invoke(*args, expected=2)
                    self.assertIsNone(result["data"])
                    self.assertNotIn("PRIVATE", json.dumps(result))
            read.assert_not_called()

    def test_invalid_later_group_or_hash_refuses_whole_result_without_profile_change(self) -> None:
        source = self.source(MULTI_TEXT, "fictional-two-works.txt")
        extracted = self.extract(source)
        self.invoke("profile", "init")
        saved = runtime_snapshot(self.runtime)
        cases = [self.grouping(source, extracted) + ["--indexes", value] for value in ("0,1", "5,6", "7,8")]
        for option in ("--document-sha256", "--source-sha256"):
            args = self.grouping(source, extracted) + ["--indexes", "4,5"]
            args[args.index(option) + 1] = "0" * 64
            cases.append(args)
        with self.no_runtime():
            for args in cases:
                refused = self.invoke(*args, expected=2)
                self.assertIsNone(refused["data"])
        self.assertEqual(runtime_snapshot(self.runtime), saved)

    def test_multiple_group_human_report_and_diagnostics_keep_every_group_visible_and_content_private(self) -> None:
        source = self.source(MULTI_TEXT, "fictional-two-works.txt")
        extracted = self.extract(source)
        output, errors = io.StringIO(), io.StringIO()
        with self.no_runtime(), redirect_stdout(output), redirect_stderr(errors):
            code = main(["--log-events", *self.grouping(source, extracted, "4,5"), "--indexes", "2,3"])
        self.assertEqual(code, 0)
        for text in ("2 explicitly chosen publications", "4,5 → manifest proposal 3", "2,3 → manifest proposal 2",
                     STATUS, SECOND_STATUS, "all other proposals remain", "1 unclassified", "explicit approval"):
            self.assertIn(text, output.getvalue())
        events = [json.loads(line) for line in errors.getvalue().splitlines()]
        self.assertEqual([event["outcome"] for event in events], ["started", "succeeded"])
        self.assertTrue(all(set(e) == EVENT_FIELDS and e["command"] == "profile.group-publication" for e in events))
        for private in (str(self.workspace), "Quill", extracted["source_sha256"], "4,5", "2,3"):
            self.assertNotIn(private, errors.getvalue())

    def test_multiple_group_unexpected_errors_and_interruption_do_not_echo_or_mutate(self) -> None:
        source = self.source(MULTI_TEXT, "fictional-two-works.txt")
        extracted = self.extract(source)
        args = self.grouping(source, extracted) + ["--indexes", "4,5"]
        for target in ("grounded_apply.services.publication_grouping.build_publication_groups",
                       "grounded_apply.domain.to_jsonable"):
            with self.no_runtime(), patch(target, side_effect=ValueError("PRIVATE_MULTIPLE_DETAIL")):
                result = self.invoke(*args, expected=2)
            self.assertIsNone(result["data"])
            self.assertNotIn("PRIVATE", json.dumps(result))
        output, errors = io.StringIO(), io.StringIO()
        with self.no_runtime(), patch("grounded_apply.services.publication_grouping.build_publication_groups", side_effect=KeyboardInterrupt), \
                redirect_stdout(output), redirect_stderr(errors):
            code = main(["--log-events", *args, "--json"])
        self.assertEqual(code, 130)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual([json.loads(line)["outcome"] for line in errors.getvalue().splitlines()], ["started", "interrupted"])
        self.assertEqual(set(self.workspace.iterdir()), {source})


if __name__ == "__main__":
    unittest.main()
