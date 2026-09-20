from __future__ import annotations

import unittest
from dataclasses import asdict

from grounded_apply.services.discovery import DiscoveryErrorCode, DiscoveryService, SourceSpec
from grounded_apply.services.source_windows import (
    NETFLIX_WINDOW_POLICY, NetflixCursor, NetflixWindowProgress, finish_netflix_window,
    plan_netflix_window, validate_netflix_cursor, validate_netflix_window_progress,
)
from tests.test_discovery import FakeTransport, FixedTransportError
from tests.test_netflix_source import (
    INDEX_URL, JOBS_SITEMAP_URL, ROBOTS, ROBOTS_URL, SOURCE, job_url, page, posting, responses, sitemap,
)


LASTMOD = "2026-09-18T00:00:00.000000+00:00"


def cursor(identifier: int, lastmod: str = LASTMOD) -> NetflixCursor:
    return NetflixCursor(lastmod, str(identifier))


def window_responses(*, count: int = 15, filtered_head: bool = False) -> dict:
    """Fictional raw sitemap and JSON-LD, reusable by saved-search tests."""
    result = responses([(identifier, "2026-09-18") for identifier in range(1001, 1001 + count)])
    if filtered_head:
        for identifier in range(1001, 1001 + count):
            result[job_url(identifier)] = page(posting(identifier,
                title="Fictional Accounting Analyst" if identifier <= 1007 else "Fictional Research Engineer"))
    return result


class NetflixWindowPolicyTests(unittest.TestCase):
    def test_cursor_requires_closed_normalized_utc_key_and_numeric_identity(self) -> None:
        valid = cursor(1007)
        self.assertEqual(validate_netflix_cursor(valid.to_dict()), valid)
        self.assertIsNone(validate_netflix_cursor(None))
        # Explicit fixtures also reject aliases that would otherwise compare as
        # the same date/ID while changing an immutable cursor's JSON identity.
        invalid = [[], {}, {**valid.to_dict(), "url": "https://example.com/"}]
        invalid += [{**valid.to_dict(), "external_id": value} for value in (True, 1007, "0", "01007", "-1", "1/2", "1" * 21, "١")]
        invalid += [{**valid.to_dict(), "lastmod": value} for value in (None, "2026-09-18", "2026-09-18T00:00:00Z",
            "2026-09-18T00:00:00.000000-00:00", "2026-09-18T01:00:00.000000+01:00", "2026-02-30T00:00:00.000000+00:00")]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_netflix_cursor(value)

    def test_numeric_ties_reordering_and_three_bounded_windows(self) -> None:
        entries = tuple(cursor(number) for number in range(1015, 1000, -1))
        after = None
        expected = [list(range(1001, 1008)), [1001, *range(1008, 1014)], [1001, 1014, 1015]]
        for index, identifiers in enumerate(expected):
            plan = plan_netflix_window(entries, after)
            self.assertEqual([int(key.external_id) for key in (plan.head, *plan.tail)], identifiers)
            progress = finish_netflix_window(plan, consumed_tail_count=len(plan.tail), head_consumed=True)
            self.assertEqual(validate_netflix_window_progress(progress.to_dict()), progress)
            self.assertEqual(progress.cycle_complete, index == 2)
            after = progress.next_cursor
        self.assertIsNone(after)
        numeric = plan_netflix_window((cursor(100), cursor(10), cursor(2)), None)
        self.assertEqual([item.external_id for item in (numeric.head, *numeric.tail)], ["2", "10", "100"])

    def test_deleted_anchor_and_newer_insert_keep_tail_position(self) -> None:
        entries = tuple(cursor(number) for number in range(1001, 1016) if number != 1007)
        newest = cursor(9999, "2026-09-19T00:00:00.000000+00:00")
        plan = plan_netflix_window((*entries, newest), cursor(1007))
        self.assertEqual(plan.head, newest)
        self.assertEqual([item.external_id for item in plan.tail], [str(number) for number in range(1008, 1014)])
        self.assertFalse(plan.reset)

    def test_missing_remaining_tail_starts_new_cycle_without_duplicate_head(self) -> None:
        plan = plan_netflix_window(tuple(cursor(number) for number in range(1001, 1007)), cursor(1007))
        self.assertTrue(plan.reset)
        self.assertEqual([item.external_id for item in plan.tail], [str(number) for number in range(1002, 1007)])
        done = finish_netflix_window(plan, consumed_tail_count=5, head_consumed=True)
        self.assertTrue(done.cycle_complete)
        self.assertIsNone(done.next_cursor)

    def test_progress_rejects_unread_advance_backward_keys_and_malformed_counters(self) -> None:
        valid = NetflixWindowProgress(cursor(1007), cursor(1013), 15, 6, False, False).to_dict()
        invalid = [{**valid, "policy": "other@1"}, {**valid, "extra": None}, {**valid, "consumed_tail_count": True},
            {**valid, "consumed_tail_count": 7}, {**valid, "indexed_count": True}, {**valid, "indexed_count": 10001},
            {**valid, "reset": 1}, {**valid, "cycle_complete": 1}, {**valid, "next": cursor(1006).to_dict()},
            {**valid, "next": cursor(1007).to_dict()}, {**valid, "next": None},
            {**valid, "indexed_count": None}, {**valid, "cycle_complete": True},
            {**valid, "after": None, "reset": True}, {**valid, "consumed_tail_count": 0},
            {**valid, "reset": True}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_netflix_window_progress(value)
        unchanged = NetflixWindowProgress(cursor(1007), cursor(1007), None, 0, False, False)
        self.assertEqual(validate_netflix_window_progress(unchanged.to_dict()), unchanged)

    def test_inventory_and_consumption_are_bounded_and_preserve_unfinished_position(self) -> None:
        for entries in ([cursor(1)], (cursor(1), cursor(1)), tuple(cursor(number) for number in range(1, 10002))):
            with self.assertRaises(ValueError):
                plan_netflix_window(entries, None)
        plan = plan_netflix_window(tuple(cursor(number) for number in range(1001, 1016)), cursor(1007))
        unchanged = finish_netflix_window(plan, consumed_tail_count=0, head_consumed=False)
        self.assertEqual(unchanged.next_cursor, cursor(1007))
        partial = finish_netflix_window(plan, consumed_tail_count=2, head_consumed=True)
        self.assertEqual(partial.next_cursor, cursor(1009))
        for count, consumed in ((True, True), (7, True), (1, False), (0, 1)):
            with self.assertRaises(ValueError):
                finish_netflix_window(plan, consumed_tail_count=count, head_consumed=consumed)


class NetflixWindowAdapterTests(unittest.TestCase):
    def discover(self, fixture=None, *, after=None, **options):
        transport = FakeTransport(window_responses() if fixture is None else fixture)
        result = DiscoveryService(transport).discover_netflix_window(SOURCE, after=after, **options)
        self.assertEqual(result.progress.after, after)
        self.assertEqual(validate_netflix_window_progress(result.progress.to_dict()), result.progress)
        self.assertLessEqual(len(transport.calls), 10)
        return result, transport

    def test_reaches_later_filtered_titles_without_changing_source_report_shape(self) -> None:
        fixture = window_responses(filtered_head=True)
        first, _ = self.discover(fixture, title_contains=("Research",))
        second, transport = self.discover(fixture, after=first.progress.next_cursor, title_contains=("Research",))
        self.assertEqual(first.report.jobs, ())
        self.assertEqual(first.report.sources[0].filtered_count, 7)
        self.assertEqual([job.external_id for job in second.report.jobs], [str(number) for number in range(1008, 1014)])
        self.assertEqual(second.progress.consumed_tail_count, 6)
        self.assertEqual([call[0] for call in transport.calls[:3]], [ROBOTS_URL, INDEX_URL, JOBS_SITEMAP_URL])
        self.assertEqual(set(asdict(second.report.sources[0])), {"source_id", "provider", "board", "careers_url", "status", "count",
            "filtered_count", "observed_count", "error", "fetched_at", "errors", "indexed_count", "remaining_count"})

    def test_cycle_remains_explicitly_partial_and_frozen_input_is_repeatable(self) -> None:
        first, _ = self.discover()
        second, transport = self.discover(after=first.progress.next_cursor)
        repeat, repeated_transport = self.discover(after=first.progress.next_cursor)
        self.assertEqual(second.report.jobs, repeat.report.jobs)
        self.assertEqual(second.progress, repeat.progress)
        self.assertEqual(transport.calls, repeated_transport.calls)
        third, transport = self.discover(after=second.progress.next_cursor)
        self.assertEqual([job.external_id for job in third.report.jobs], ["1001", "1014", "1015"])
        self.assertEqual(len(transport.calls), 6)
        self.assertEqual((third.report.sources[0].indexed_count, third.report.sources[0].observed_count,
            third.report.sources[0].remaining_count), (15, 3, 12))
        self.assertEqual(third.report.sources[0].status, "partial")
        self.assertTrue(third.progress.cycle_complete)

    def test_standalone_discovery_keeps_repeating_its_original_newest_sample(self) -> None:
        fixture = window_responses()
        first = DiscoveryService(FakeTransport(fixture)).discover((SOURCE,))
        second = DiscoveryService(FakeTransport(fixture)).discover((SOURCE,))
        window, _ = self.discover(fixture)
        self.assertEqual(first.jobs, second.jobs)
        self.assertEqual(first.jobs, window.report.jobs)
        self.assertEqual(set(asdict(first)), {"jobs", "sources", "filter_method"})

    def test_advertised_offset_and_naive_sort_hints_normalize_to_one_cursor_key(self) -> None:
        fixture = window_responses()
        fixture[JOBS_SITEMAP_URL] = sitemap([(number,
            "2026-09-18T01:00:00+01:00" if number % 2 else "2026-09-18T00:00:00")
            for number in range(1001, 1016)])
        result, _ = self.discover(fixture)
        self.assertEqual([job.external_id for job in result.report.jobs], [str(number) for number in range(1001, 1008)])
        self.assertEqual(result.progress.next_cursor, cursor(1007))

    def test_only_current_advertised_slug_is_fetched_after_reorder_and_anchor_deletion(self) -> None:
        fixture = window_responses()
        fixture[JOBS_SITEMAP_URL] = sitemap([(number, "2026-09-18") for number in range(1015, 1000, -1) if number != 1007])
        old = job_url(1008)
        new = old.replace("fictional-engineer", "renamed-fictional-engineer")
        fixture[JOBS_SITEMAP_URL] = fixture[JOBS_SITEMAP_URL].replace(old.replace("&", "&amp;").encode(), new.replace("&", "&amp;").encode())
        fixture[new] = page(posting(1008, url=new))
        result, transport = self.discover(fixture, after=cursor(1007))
        self.assertFalse(result.progress.reset)
        self.assertEqual([job.external_id for job in result.report.jobs], ["1001", *map(str, range(1008, 1014))])
        self.assertIn(new, [call[0] for call in transport.calls])
        self.assertNotIn(old, [call[0] for call in transport.calls])

    def test_not_found_and_invalid_individual_payload_consume_tail_positions(self) -> None:
        fixture = window_responses()
        fixture[job_url(1003)] = FixedTransportError("not_found")
        fixture[job_url(1004)] = b'<script type="application/ld+json">invalid</script>'
        result, _ = self.discover(fixture)
        self.assertEqual(result.progress.next_cursor, cursor(1007))
        self.assertEqual(result.progress.consumed_tail_count, 6)
        self.assertEqual(len(result.report.jobs), 5)
        self.assertIn(DiscoveryErrorCode.NOT_FOUND, result.report.sources[0].errors)
        self.assertIn(DiscoveryErrorCode.INVALID_RECORD, result.report.sources[0].errors)

    def test_policy_rate_redirect_and_budget_failures_do_not_advance_past_failed_entry(self) -> None:
        for error in ("forbidden", "rate_limited", "redirect_refused", "source_limit_reached"):
            with self.subTest(error=error):
                fixture = window_responses()
                fixture[job_url(1004)] = FixedTransportError(error)
                result, transport = self.discover(fixture)
                self.assertEqual(result.progress.next_cursor, cursor(1003))
                self.assertEqual(result.progress.consumed_tail_count, 2)
                self.assertNotIn(job_url(1005), [call[0] for call in transport.calls])
                repeated, calls = self.discover(fixture, after=result.progress.next_cursor)
                self.assertEqual(repeated.progress.next_cursor, cursor(1003))
                self.assertEqual(repeated.progress.consumed_tail_count, 0)
                self.assertEqual([call[0] for call in calls.calls[3:]], [job_url(1001), job_url(1004)])

    def test_isolated_head_and_tail_failures_do_not_starve_healthy_entries(self) -> None:
        failures = [FixedTransportError("timeout"), FixedTransportError("transport_failure"),
            FixedTransportError("http_error"), FixedTransportError("response_too_large"),
            b"x" * (2 * 1024 * 1024 + 1), "invalid response type"]
        for payload in failures:
            with self.subTest(payload=type(payload).__name__):
                fixture = window_responses()
                fixture[job_url(1001)] = FixedTransportError("timeout")
                fixture[job_url(1004)] = payload
                result, transport = self.discover(fixture)
                self.assertEqual(result.progress.next_cursor, cursor(1007))
                self.assertEqual(result.progress.consumed_tail_count, 6)
                self.assertEqual([job.external_id for job in result.report.jobs], ["1002", "1003", "1005", "1006", "1007"])
                self.assertEqual(result.report.sources[0].observed_count, 7)
                self.assertEqual(len(transport.calls), 10)
                self.assertEqual(result.report.sources[0].status, "partial")
                self.assertIn(DiscoveryErrorCode.TIMEOUT, result.report.sources[0].errors)

    def test_parent_budget_refusal_is_not_an_observed_detail(self) -> None:
        for windowed in (True, False):
            with self.subTest(windowed=windowed):
                transport = FakeTransport(window_responses())

                class LimitedTransport:
                    def get(self, url, *, max_bytes, timeout):
                        if len(transport.calls) >= 5:
                            raise FixedTransportError("source_limit_reached")
                        return transport.get(url, max_bytes=max_bytes, timeout=timeout)

                service = DiscoveryService(LimitedTransport())
                if windowed:
                    result = service.discover_netflix_window(SOURCE, after=None)
                    report = result.report
                    self.assertEqual(result.progress.next_cursor, cursor(1002))
                    self.assertEqual(result.progress.consumed_tail_count, 1)
                else:
                    report = service.discover((SOURCE,))
                self.assertEqual(len(transport.calls), 5)
                self.assertEqual([job.external_id for job in report.jobs], ["1001", "1002"])
                self.assertEqual(report.sources[0].observed_count, 2)
                self.assertEqual(report.sources[0].remaining_count, 13)
                self.assertIn(DiscoveryErrorCode.SOURCE_LIMIT_REACHED, report.sources[0].errors)

    def test_current_robots_and_page_restrictions_stop_before_consuming_tail(self) -> None:
        fixture = window_responses()
        fixture[ROBOTS_URL] = ROBOTS + b"Disallow: /careers/job/1004*\n"
        result, transport = self.discover(fixture)
        self.assertEqual(result.progress.next_cursor, cursor(1003))
        self.assertNotIn(job_url(1004), [call[0] for call in transport.calls])
        fixture[ROBOTS_URL] = ROBOTS
        fixture[job_url(1004)] = b'<meta name="robots" content="noindex">' + page(posting(1004))
        result, _ = self.discover(fixture)
        self.assertEqual(result.progress.next_cursor, cursor(1003))
        self.assertEqual(result.report.sources[0].error, DiscoveryErrorCode.ROBOTS_DISALLOWED)

    def test_preinventory_failure_preserves_cursor_and_new_head_failure_consumes_nothing(self) -> None:
        for url, payload in ((ROBOTS_URL, FixedTransportError("forbidden")), (INDEX_URL, b"invalid"),
                             (JOBS_SITEMAP_URL, b"invalid")):
            fixture = window_responses()
            fixture[url] = payload
            result, _ = self.discover(fixture, after=cursor(1007))
            self.assertIsNone(result.progress.indexed_count)
            self.assertEqual(result.progress.next_cursor, cursor(1007))
            self.assertFalse(result.progress.reset)
        fixture = window_responses()
        fixture[job_url(1001)] = FixedTransportError("forbidden")
        result, _ = self.discover(fixture, after=cursor(1007))
        self.assertEqual(result.progress.next_cursor, cursor(1007))
        self.assertEqual(result.progress.consumed_tail_count, 0)

    def test_empty_singleton_and_selection_limit_do_not_invent_coverage_or_extra_requests(self) -> None:
        for count in (0, 1):
            result, transport = self.discover(window_responses(count=count), after=cursor(1007))
            self.assertEqual(len(transport.calls), 3 + count)
            self.assertEqual(result.report.sources[0].status, "successful")
            self.assertTrue(result.progress.cycle_complete)
            self.assertIsNone(result.progress.next_cursor)
        result, _ = self.discover(limit_per_source=2)
        self.assertEqual(len(result.report.jobs), 2)
        self.assertEqual(result.progress.consumed_tail_count, 6)

    def test_window_input_validation_happens_before_requests(self) -> None:
        transport = FakeTransport(window_responses())
        service = DiscoveryService(transport)
        for options in ({"after": {}}, {"after": None, "limit_per_source": True},
                        {"after": None, "title_contains": (" Research",)}):
            with self.assertRaises(ValueError):
                service.discover_netflix_window(SOURCE, **options)
        with self.assertRaises(ValueError):
            service.discover_netflix_window(SourceSpec("fictional", "greenhouse", "example"), after=None)
        self.assertEqual(transport.calls, [])


if __name__ == "__main__":
    unittest.main()
