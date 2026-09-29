"""Literal location selection changes the bounded returned subset, not coverage."""

from __future__ import annotations

import json
import unittest
from dataclasses import FrozenInstanceError, asdict, replace
from datetime import UTC, datetime
from hashlib import sha256
from unittest.mock import patch

from grounded_apply.services.discovery import (
    MAX_RESPONSE_BYTES, MAX_SOURCE_RECORDS, REQUEST_TIMEOUT, DiscoveryErrorCode,
    DiscoverySelection, DiscoveryService, SourceSpec, validate_location_discovery_request,
    validate_location_filters,
)


SOURCE = SourceSpec("fictional-locations", "greenhouse", "fictional-locations")
MANUAL = SourceSpec("fictional-manual", "manual", careers_url="https://example.com/careers")
URL = "https://boards-api.greenhouse.io/v1/boards/fictional-locations/jobs?content=true"


def posting(identifier: int, location: str | None = "Preferred", title: str = "Fictional Research Engineer") -> dict:
    return {"id": identifier, "title": title, "location": None if location is None else {"name": location},
            "absolute_url": f"https://job-boards.greenhouse.io/fictional-locations/jobs/{identifier}",
            "content": "<p>Build fictional tools.</p>"}


class Feed:
    def __init__(self, records: list[dict] | None = None, failure: Exception | None = None):
        self.records = records or []
        self.failure = failure
        self.calls = []

    def get(self, url: str, *, max_bytes: int, timeout: float) -> bytes:
        self.calls.append((url, max_bytes, timeout))
        if self.failure is not None:
            raise self.failure
        return json.dumps({"jobs": self.records}).encode()


def select(records: list[dict], **kwargs):
    feed = Feed(records)
    return DiscoveryService(feed).discover_with_locations((SOURCE,), **kwargs), feed


class DiscoveryLocationTests(unittest.TestCase):
    def assert_accounting(self, report) -> None:
        self.assertEqual(len(report.sources), len(report.selections))
        for source, selection in zip(report.sources, report.selections, strict=True):
            self.assertEqual(source.source_id, selection.source_id)
            self.assertEqual(selection.valid_count, selection.title_filtered_count + selection.location_filtered_count
                             + selection.unknown_excluded_count + selection.selected_count + selection.limit_deferred_count)
            self.assertEqual(source.filtered_count, selection.title_filtered_count + selection.location_filtered_count
                             + selection.unknown_excluded_count)
            self.assertEqual(source.count, selection.selected_count)
            self.assertLessEqual(selection.selected_unknown_count, selection.unknown_included_count)
            self.assertLessEqual(selection.unknown_included_count - selection.selected_unknown_count,
                                 selection.limit_deferred_count)

    def test_legacy_report_serialization_and_job_hashes_remain_exactly_unchanged(self) -> None:
        records = [posting(1, "Other", "Fictional Finance"), posting(2, "Other"),
                   posting(3, "Preferred"), posting(4, None)]
        with patch("grounded_apply.services.discovery.datetime") as clock:
            clock.now.return_value = datetime(2026, 9, 21, 12, tzinfo=UTC)
            legacy = DiscoveryService(Feed(records)).discover((SOURCE, MANUAL), title_contains=("Research",), limit_per_source=2)
            explicit = DiscoveryService(Feed(records)).discover_with_locations((SOURCE, MANUAL),
                title_contains=("Research",), limit_per_source=2)
        encoded = json.dumps(asdict(legacy), ensure_ascii=False, separators=(",", ":"))
        # Captured before changing discovery.py, including field order and timestamps.
        self.assertEqual(sha256(encoded.encode()).hexdigest(), "2d5b8985d20a25cfda55c1848a145cc8b5df52d263d8118a17f10d7717c1fe71")
        self.assertEqual(set(asdict(legacy)), {"jobs", "sources", "filter_method"})
        self.assertEqual(legacy.filter_method, "title_substring_or@1")
        self.assertEqual(explicit.jobs, legacy.jobs)
        self.assertEqual(explicit.sources, legacy.sources)
        self.assertEqual(explicit.filter_method, "title_location_substring_or@1")
        self.assert_accounting(explicit)

    def test_title_then_location_filters_precede_selected_quota_without_extra_reads(self) -> None:
        records = [posting(1, "Preferred", "Fictional Finance"), posting(2, "Other"),
                   posting(3, "Preferred"), posting(4, "Preferred")]
        report, feed = select(records, title_contains=("research",), location_contains=("Preferred",), limit_per_source=1)
        self.assertEqual([job.external_id for job in report.jobs], ["3"])
        self.assertEqual(feed.calls, [(URL, MAX_RESPONSE_BYTES, REQUEST_TIMEOUT)])
        self.assertEqual(asdict(report.selections[0]), {"source_id": SOURCE.id, "valid_count": 4,
            "title_filtered_count": 1, "location_filtered_count": 1, "unknown_excluded_count": 0,
            "unknown_included_count": 0, "selected_count": 1, "selected_unknown_count": 0,
            "limit_deferred_count": 1})
        self.assertEqual(report.sources[0].status, "partial")
        self.assertEqual(report.sources[0].errors, (DiscoveryErrorCode.SOURCE_LIMIT_REACHED,))
        self.assert_accounting(report)

    def test_missing_include_and_exclude_keep_unknown_and_selected_counts_distinct(self) -> None:
        records = [posting(1, None), posting(2), posting(3, None), posting(4, "Other"),
                   posting(5, None, "Fictional Finance")]
        include, _ = select(records, title_contains=("Research",), location_contains=("Preferred",), limit_per_source=2)
        exclude, _ = select(records, title_contains=("Research",), location_contains=("Preferred",),
                            missing_location="exclude", limit_per_source=2)
        self.assertEqual([job.external_id for job in include.jobs], ["1", "2"])
        self.assertEqual([job.external_id for job in exclude.jobs], ["2"])
        self.assertEqual((include.selections[0].unknown_included_count, include.selections[0].selected_unknown_count,
                          include.selections[0].limit_deferred_count), (2, 1, 1))
        self.assertEqual((exclude.selections[0].title_filtered_count, exclude.selections[0].location_filtered_count,
                          exclude.selections[0].unknown_excluded_count), (1, 1, 2))
        self.assertEqual(exclude.sources[0].status, "successful")
        self.assert_accounting(include)
        self.assert_accounting(exclude)

    def test_missing_policy_alone_is_supported_and_blank_locations_remain_invalid_records(self) -> None:
        records = [posting(1, None), posting(2, "Other"), posting(3, " "), posting(4, "Preferred")]
        include, _ = select(records)
        exclude, _ = select(records, missing_location="exclude")
        self.assertEqual([job.external_id for job in include.jobs], ["1", "2", "4"])
        self.assertEqual([job.external_id for job in exclude.jobs], ["2", "4"])
        self.assertEqual(include.location_contains, ())
        self.assertEqual((include.selections[0].unknown_included_count, include.selections[0].selected_unknown_count), (1, 1))
        self.assertEqual(exclude.selections[0].unknown_excluded_count, 1)
        self.assertIn(DiscoveryErrorCode.INVALID_RECORD, include.sources[0].errors)
        self.assertIn(DiscoveryErrorCode.INVALID_RECORD, exclude.sources[0].errors)
        self.assert_accounting(include)
        self.assert_accounting(exclude)

    def test_literal_casefold_or_terms_and_title_location_and_do_not_infer_aliases(self) -> None:
        records = [posting(1, "Straße"), posting(2, "München", "Fictional Scientist"),
                   posting(3, "Munich"), posting(4, "Straße", "Fictional Finance"),
                   posting(5, "C++ / R&D office")]
        terms = ("STRASSE", "MÜNCHEN", "C++ / R&D")
        report, _ = select(records, title_contains=("Engineer", "Scientist"), location_contains=terms)
        self.assertEqual([job.external_id for job in report.jobs], ["1", "2", "5"])
        self.assertEqual([job.location for job in report.jobs], ["Straße", "München", "C++ / R&D office"])
        self.assertEqual(report.location_contains, terms)
        self.assertEqual((report.selections[0].title_filtered_count, report.selections[0].location_filtered_count), (1, 1))
        self.assert_accounting(report)

    def test_remote_word_is_literal_even_in_negation_and_never_implies_worldwide_eligibility(self) -> None:
        records = [posting(1, "Remote—US"), posting(2, "Not Remote—US office only"),
                   posting(3, "Remote—UK"), posting(4, "Worldwide"), posting(5, None)]
        report, _ = select(records, location_contains=("Remote—US",))
        self.assertEqual([job.external_id for job in report.jobs], ["1", "2", "5"])
        self.assertEqual(report.jobs[1].location, "Not Remote—US office only")
        self.assertEqual(report.selections[0].selected_unknown_count, 1)
        self.assert_accounting(report)

    def test_pure_validation_preserves_terms_and_rejects_bad_inputs_before_transport(self) -> None:
        terms = ("C++ / R&D", "Remote", "Remote", "Security clearance")
        with patch("builtins.open", side_effect=AssertionError("file IO")), \
             patch("socket.socket", side_effect=AssertionError("network")), \
             patch("sqlite3.connect", side_effect=AssertionError("storage")):
            policy = validate_location_filters(terms, "include")
            self.assertEqual(policy.location_contains, terms)
            self.assertEqual(validate_location_discovery_request(location_contains=terms), policy)
        invalid = [None, [], (None,), (True,), ("",), (" ",), (" padded",), ("padded ",),
                   ("x" * 129,), ("x",) * 21, ("private\nvalue",), ("a\x00b",), ("a\x7fb",),
                   ("a\u200bb",), ("private\ud800",)]
        cases = [{"location_contains": value} for value in invalid]
        cases += [{"missing_location": value} for value in (None, True, "unknown", " include", [])]
        cases += [{"title_contains": value} for value in ([], ("",), (" x",), ("private\nvalue",), ("x",) * 21)]
        cases += [{"limit_per_source": value} for value in (True, 0, 1001, "1", 1.0)]
        for kwargs in cases:
            feed = Feed([posting(1)])
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError) as error:
                DiscoveryService(feed).discover_with_locations((SOURCE,), **kwargs)
            self.assertNotIn("private", str(error.exception))
            self.assertEqual(feed.calls, [])
            with self.assertRaises(ValueError):
                validate_location_discovery_request(**kwargs)
        self.assertEqual(len(validate_location_filters(("x" * 128,) * 20, "include").location_contains), 20)

    def test_invalid_sources_remain_refused_before_transport(self) -> None:
        feed = Feed()
        for sources in ((), [SOURCE], (SOURCE, SOURCE), (replace(SOURCE, board="private/invalid"),)):
            with self.subTest(sources=sources), self.assertRaises(ValueError):
                DiscoveryService(feed).discover_with_locations(sources, location_contains=("Preferred",))
        self.assertEqual(feed.calls, [])

    def test_empty_failed_manual_and_all_filtered_sources_keep_distinct_statuses_and_zero_rows(self) -> None:
        for records, failure, expected in (([], None, "successful"), ([posting(1, "Other")], None, "successful"),
                                           ([], RuntimeError("PRIVATE_REMOTE_RESPONSE"), "failed")):
            feed = Feed(records, failure)
            report = DiscoveryService(feed).discover_with_locations((SOURCE, MANUAL), location_contains=("Preferred",))
            self.assertEqual(report.jobs, ())
            self.assertEqual([source.status for source in report.sources], [expected, "manual_required"])
            self.assertEqual([row.source_id for row in report.selections], [SOURCE.id, MANUAL.id])
            self.assertEqual(report.selections[1], DiscoverySelection(MANUAL.id, 0, 0, 0, 0, 0, 0, 0, 0))
            self.assertEqual(len(feed.calls), 1)
            self.assertNotIn("PRIVATE_REMOTE_RESPONSE", json.dumps(asdict(report)))
            self.assert_accounting(report)

    def test_valid_record_counts_are_distinct_from_malformed_duplicate_provider_observations(self) -> None:
        records = [posting(1), posting(1), posting(2, None), {"id": "bad"}, posting(3, "Other")]
        report, _ = select(records, location_contains=("Preferred",), missing_location="exclude")
        self.assertEqual(report.sources[0].observed_count, 5)
        self.assertEqual(report.selections[0].valid_count, 3)
        self.assertEqual([job.external_id for job in report.jobs], ["1"])
        self.assertEqual(report.sources[0].status, "partial")
        self.assertIn(DiscoveryErrorCode.INVALID_RECORD, report.sources[0].errors)
        self.assert_accounting(report)

    def test_provider_scan_cap_survives_zero_matches_and_never_refetches_to_fill_quota(self) -> None:
        records = [posting(1, "Other")] * (MAX_SOURCE_RECORDS + 1)
        report, feed = select(records, location_contains=("Preferred",), limit_per_source=1000)
        self.assertEqual(report.jobs, ())
        self.assertEqual(report.sources[0].status, "partial")
        self.assertEqual(report.sources[0].observed_count, MAX_SOURCE_RECORDS)
        self.assertEqual(report.sources[0].errors, (DiscoveryErrorCode.SOURCE_LIMIT_REACHED,))
        self.assertEqual((report.selections[0].valid_count, report.selections[0].location_filtered_count,
                          report.selections[0].limit_deferred_count), (1, 1, 0))
        self.assertEqual(len(feed.calls), 1)
        self.assert_accounting(report)

    def test_multiple_provider_reasons_and_output_cap_remain_visible(self) -> None:
        records = [posting(1), posting(2, None), {"id": "bad"}]
        report, _ = select(records, location_contains=("Preferred",), limit_per_source=1)
        self.assertEqual(report.sources[0].errors, (DiscoveryErrorCode.INVALID_RECORD, DiscoveryErrorCode.SOURCE_LIMIT_REACHED))
        self.assertEqual((report.selections[0].unknown_included_count, report.selections[0].selected_unknown_count,
                          report.selections[0].limit_deferred_count), (1, 0, 1))
        self.assert_accounting(report)

    def test_indexed_and_remaining_coverage_metadata_survive_filtering_without_extra_fetches(self) -> None:
        from grounded_apply.repositories.job_sources import discover_source

        normalized = discover_source(SOURCE, Feed([posting(1, "Other")]), max_records=MAX_SOURCE_RECORDS)
        partial = replace(normalized, errors=(DiscoveryErrorCode.RATE_LIMITED, DiscoveryErrorCode.SOURCE_LIMIT_REACHED),
                          indexed_count=80, remaining_count=79)
        feed = Feed()
        with patch("grounded_apply.repositories.job_sources.discover_source", return_value=partial) as adapter:
            report = DiscoveryService(feed).discover_with_locations((SOURCE,), location_contains=("Preferred",))
        adapter.assert_called_once_with(SOURCE, feed, max_records=MAX_SOURCE_RECORDS)
        self.assertEqual(feed.calls, [])
        self.assertEqual(report.jobs, ())
        self.assertEqual((report.sources[0].status, report.sources[0].indexed_count, report.sources[0].remaining_count),
                         ("partial", 80, 79))
        self.assertEqual(report.sources[0].errors, partial.errors)
        self.assertEqual(report.sources[0].error, DiscoveryErrorCode.RATE_LIMITED)
        self.assert_accounting(report)

    def test_report_and_selection_are_immutable_and_exported_data_is_detached(self) -> None:
        report, _ = select([posting(1), posting(2, None)], location_contains=("Preferred",))
        with self.assertRaises(FrozenInstanceError):
            report.missing_location = "exclude"
        with self.assertRaises(FrozenInstanceError):
            report.selections[0].selected_count = 7
        exported = asdict(report)
        exported["selections"][0]["selected_count"] = 7
        exported["jobs"][0]["location"] = "Fictional changed location"
        self.assertEqual(report.selections[0].selected_count, 2)
        self.assertEqual(report.jobs[0].location, "Preferred")
        self.assertEqual(set(exported), {"jobs", "sources", "location_contains", "missing_location", "selections", "filter_method"})

    def test_selection_accounting_rejects_invalid_counts_without_echo(self) -> None:
        row = DiscoverySelection("fictional", 3, 0, 0, 0, 2, 2, 1, 1)
        for values in ({"source_id": "PRIVATE_INVALID_ID/"}, {"valid_count": True}, {"valid_count": 10001},
                       {"title_filtered_count": -1}, {"unknown_included_count": 3},
                       {"selected_unknown_count": 3}, {"selected_count": 1}, {"limit_deferred_count": 0}):
            with self.subTest(values=values), self.assertRaises(ValueError) as error:
                replace(row, **values)
            self.assertNotIn("PRIVATE", str(error.exception))

    def test_filtering_retains_exact_job_identity_content_and_does_not_open_storage(self) -> None:
        records = [posting(1, "Other"), posting(2), posting(3, None)]
        legacy = DiscoveryService(Feed(records)).discover((SOURCE,))
        with patch("grounded_apply.config.resolve_runtime_paths", side_effect=AssertionError("runtime")), \
             patch("sqlite3.connect", side_effect=AssertionError("storage")), \
             patch("socket.socket", side_effect=AssertionError("live network")):
            report, _ = select(records, location_contains=("Preferred",))
        self.assertEqual(report.jobs, legacy.jobs[1:])
        self.assert_accounting(report)
        for error in (KeyboardInterrupt, SystemExit):
            with self.subTest(error=error), self.assertRaises(error):
                DiscoveryService(Feed(failure=error())).discover_with_locations((SOURCE,), location_contains=("Preferred",))


if __name__ == "__main__":
    unittest.main()
