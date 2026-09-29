"""Opt-in source setup preserves valid siblings and accounts for every rejection."""

from __future__ import annotations

import json
import unittest
from contextlib import ExitStack
from dataclasses import asdict, replace
from unittest.mock import patch

from grounded_apply.services.discovery import DiscoveryService, validate_source_manifest
from grounded_apply.services.discovery_sources import (
    KeepValidSourceSetupReport, SourceURLRejection,
    build_source_manifest, build_source_manifest_keep_valid,
)
from tests.test_discovery import FakeTransport, encoded, greenhouse_job


class PartialSourceSetupTests(unittest.TestCase):
    def test_all_valid_preserves_exact_strict_report_and_manifest(self) -> None:
        urls = [
            "https://job-boards.greenhouse.io/fictional/jobs/1?private-discarded#private-fragment",
            "https://boards.greenhouse.io/fictional",
            "https://jobs.ashbyhq.com/fictional",
            "https://jobs.lever.co/fictional",
            "https://jobs.eu.lever.co/fictional",
            "https://apply.workable.com/fictional/",
            "https://apply.workable.com/j/ABCDEF1234",
            "https://explore.jobs.netflix.net/careers",
            "https://example.com/careers",
        ]
        strict = build_source_manifest(urls)
        report = build_source_manifest_keep_valid(urls)
        self.assertEqual(report.accepted, strict)
        self.assertEqual(report.accepted.manifest(), strict.manifest())
        self.assertEqual(report.rejected_inputs, ())
        self.assertEqual((report.input_count, report.accepted_input_count, report.setup_status), (9, 9, "complete"))
        self.assertEqual(set(asdict(strict)), {"sources", "inputs"})
        self.assertNotIn("setup_status", asdict(strict))
        self.assertNotIn("private-", json.dumps(asdict(report)))

    def test_mixed_batch_retains_original_positions_and_only_fixed_rejections(self) -> None:
        urls = [
            "https://jobs.lever.co/fictional",
            "http://private-rejected.example.com/secret",
            "https://jobs.lever.co/fictional/job-1/apply?private-query#private-fragment",
            "https://private-user:private-secret@example.com/careers",
            "https://example.com/careers",
            "https://example.com/careers?private-token=secret",
        ]
        report = build_source_manifest_keep_valid(urls)
        self.assertEqual((report.input_count, report.accepted_input_count, report.setup_status), (6, 3, "partial"))
        self.assertEqual([item.position for item in report.accepted.inputs], [1, 3, 5])
        self.assertEqual([item.duplicate for item in report.accepted.inputs], [False, True, False])
        self.assertTrue(report.accepted.inputs[1].discarded_query)
        self.assertTrue(report.accepted.inputs[1].discarded_fragment)
        self.assertEqual([asdict(item) for item in report.rejected_inputs], [
            {"position": position, "error": "invalid_url"} for position in (2, 4, 6)
        ])
        self.assertEqual(len(report.accepted.sources), 2)
        self.assertNotIn("private-", json.dumps(asdict(report)))
        with self.assertRaises(ValueError):
            build_source_manifest(urls)

    def test_all_rejected_never_constructs_an_empty_manifest(self) -> None:
        values = [None, True, 1, b"private-bytes", "", " private-whitespace ",
                  "https://example.com/private\n", "file:///private-source", "https://example.com/\ud800",
                  "https://user:private-secret@example.com", "https://example.com:443/careers"]
        with patch("grounded_apply.services.discovery_sources.SourceSetupReport.manifest", side_effect=AssertionError("No empty manifest")):
            report = build_source_manifest_keep_valid(values)
        self.assertIsNone(report.accepted)
        self.assertEqual((report.input_count, report.accepted_input_count, report.setup_status), (len(values), 0, "failed"))
        self.assertEqual([item.position for item in report.rejected_inputs], list(range(1, len(values) + 1)))
        self.assertTrue(all(item.error == "invalid_url" for item in report.rejected_inputs))
        self.assertNotIn("private", json.dumps(asdict(report)))

    def test_source_cap_rejects_new_routes_but_keeps_later_retained_duplicates(self) -> None:
        urls = [f"https://jobs.lever.co/fictional-{index}" for index in range(32)]
        urls.extend(("https://jobs.lever.co/fictional-overflow", "https://example.com/overflow",
                     "https://jobs.lever.co/fictional-0/job-1/apply?private-discarded",
                     "https://jobs.lever.co/fictional-overflow", "https://jobs.lever.co/fictional-31",
                     "https://private-user:private-secret@example.com"))
        report = build_source_manifest_keep_valid(urls)
        self.assertEqual((report.input_count, report.accepted_input_count, report.setup_status), (38, 34, "partial"))
        self.assertEqual(len(report.accepted.sources), 32)
        self.assertEqual([item.position for item in report.accepted.inputs[-2:]], [35, 37])
        self.assertTrue(all(item.duplicate for item in report.accepted.inputs[-2:]))
        self.assertEqual([asdict(item) for item in report.rejected_inputs], [
            {"position": 33, "error": "source_limit_reached"},
            {"position": 34, "error": "source_limit_reached"},
            {"position": 36, "error": "source_limit_reached"},
            {"position": 38, "error": "invalid_url"},
        ])
        self.assertNotIn("overflow", repr(report))
        positions = [item.position for item in report.accepted.inputs] + [item.position for item in report.rejected_inputs]
        self.assertEqual(sorted(positions), list(range(1, 39)))
        with self.assertRaisesRegex(ValueError, "32 distinct sources"):
            build_source_manifest(urls)

    def test_invalid_inputs_do_not_consume_distinct_source_capacity(self) -> None:
        urls = ["https://private-user:private-secret@example.com"]
        urls += [f"https://jobs.lever.co/fictional-{index}" for index in range(32)]
        report = build_source_manifest_keep_valid(urls)
        self.assertEqual(len(report.accepted.sources), 32)
        self.assertEqual(report.accepted_input_count, 32)
        self.assertEqual(report.rejected_inputs, (SourceURLRejection(1, "invalid_url"),))
        self.assertEqual(report.accepted.inputs[-1].position, 33)

    def test_raw_container_and_count_bound_fail_before_parsing_any_url(self) -> None:
        for value in ([], (), None, True, "private-url", {"private-url"},
                      ["https://jobs.lever.co/fictional"] * 257):
            with self.subTest(value_type=type(value)), patch(
                "grounded_apply.services.discovery_sources._source_from_url",
                side_effect=AssertionError("Must validate raw batch bound first"),
            ), self.assertRaises(ValueError) as error:
                build_source_manifest_keep_valid(value)
            self.assertNotIn("private-url", str(error.exception))
        report = build_source_manifest_keep_valid(["https://jobs.lever.co/fictional"] * 256)
        self.assertEqual(report.accepted_input_count, 256)
        self.assertEqual(len(report.accepted.sources), 1)
        self.assertTrue(all(item.duplicate for item in report.accepted.inputs[1:]))

    def test_validation_errors_are_isolated_but_unexpected_failures_propagate(self) -> None:
        with patch("grounded_apply.services.discovery_sources._source_from_url", side_effect=ValueError("private-exception")):
            report = build_source_manifest_keep_valid(["private-url"])
        self.assertEqual(report.rejected_inputs, (SourceURLRejection(1, "invalid_url"),))
        self.assertNotIn("private", repr(report))
        for failure in (RuntimeError("fictional-failure"), KeyboardInterrupt(), MemoryError()):
            with self.subTest(kind=type(failure)), patch(
                "grounded_apply.services.discovery_sources._source_from_url", side_effect=failure,
            ), self.assertRaises(type(failure)) as error:
                build_source_manifest_keep_valid(["https://example.com/careers"])
            self.assertIs(error.exception, failure)

    def test_manual_boardless_and_unsupported_routes_keep_existing_policy(self) -> None:
        urls = ["https://apply.workable.com/j/ABCDEF1234", "https://jobs.lever.co/%66ictional",
                "https://jobs.lever.co.example.com/fictional", "https://example.com/careers"]
        report = build_source_manifest_keep_valid(urls)
        self.assertEqual(report.setup_status, "complete")
        self.assertEqual(report.accepted, build_source_manifest(urls))
        self.assertTrue(all(source.provider == "manual" for source in report.accepted.sources))
        transport = FakeTransport({})
        discovery = DiscoveryService(transport).discover(report.accepted.sources)
        self.assertEqual(transport.calls, [])
        self.assertTrue(all(source.status == "manual_required" for source in discovery.sources))

    def test_offline_retained_manifest_runs_through_unchanged_discovery(self) -> None:
        with ExitStack() as stack:
            for target in ("builtins.open", "os.open", "socket.socket", "socket.getaddrinfo", "sqlite3.connect"):
                stack.enter_context(patch(target, side_effect=AssertionError("Source setup must be offline")))
            report = build_source_manifest_keep_valid([
                "https://job-boards.greenhouse.io/example/jobs/1", "http://private-rejected.example.com",
            ])
        manifest = report.accepted.manifest()
        transport = FakeTransport({
            "https://boards-api.greenhouse.io/v1/boards/example/jobs?content=true": encoded({"jobs": [greenhouse_job(1)]}),
        })
        discovery = DiscoveryService(transport).discover(validate_source_manifest(manifest))
        self.assertEqual(len(discovery.jobs), 1)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(report.setup_status, "partial")
        manifest["sources"].clear()
        self.assertEqual(len(report.accepted.manifest()["sources"]), 1)

    def test_typed_reports_require_fixed_codes_and_complete_position_accounting(self) -> None:
        report = build_source_manifest_keep_valid(["https://example.com/careers", "http://example.com/private"])
        invalid = (
            {"input_count": True}, {"input_count": 0}, {"input_count": 257},
            {"input_count": 3}, {"rejected_inputs": ()}, {"rejected_inputs": []},
            {"rejected_inputs": (SourceURLRejection(1, "invalid_url"),)},
            {"accepted": None},
        )
        for change in invalid:
            with self.subTest(fields=list(change)), self.assertRaises(ValueError):
                replace(report, **change)
        for position, error in ((True, "invalid_url"), (0, "invalid_url"), (257, "invalid_url"),
                                (1, "private-exception"), (1, None)):
            with self.subTest(position=position), self.assertRaises(ValueError) as caught:
                SourceURLRejection(position, error)
            self.assertNotIn("private-exception", str(caught.exception))
        self.assertIsInstance(report, KeepValidSourceSetupReport)


if __name__ == "__main__":
    unittest.main()
