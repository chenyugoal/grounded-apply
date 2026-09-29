from __future__ import annotations

import unittest
from unittest.mock import patch

from grounded_apply.services.discovery import DiscoveryService, SourceSpec, validate_source_manifest
from grounded_apply.services.discovery_sources import build_source_manifest
from tests.test_discovery import FakeTransport, encoded, greenhouse_job


class DiscoverySourceSetupTests(unittest.TestCase):
    def test_recognizes_supported_board_job_and_application_routes(self) -> None:
        cases = (
            ("https://boards.greenhouse.io/fictional-lab", "greenhouse", "fictional-lab"),
            ("https://job-boards.greenhouse.io/fictional-lab/jobs/123/", "greenhouse", "fictional-lab"),
            ("https://jobs.ashbyhq.com/Fictional_Lab/", "ashby", "Fictional_Lab"),
            ("https://jobs.ashbyhq.com/fictional-lab/post-1", "ashby", "fictional-lab"),
            ("https://jobs.ashbyhq.com/fictional-lab/post-1/application", "ashby", "fictional-lab"),
            ("https://jobs.lever.co/fictional-lab", "lever", "fictional-lab"),
            ("https://jobs.lever.co/fictional-lab/post-1/apply", "lever", "fictional-lab"),
            ("https://jobs.eu.lever.co/fictional-lab/post-1", "lever_eu", "fictional-lab"),
            ("https://apply.workable.com/fictional-lab/", "workable", "fictional-lab"),
            ("https://explore.jobs.netflix.net/careers/", "netflix", "netflix"),
            ("https://explore.jobs.netflix.net/careers/job/123-Fictional-Engineer", "netflix", "netflix"),
        )
        for url, provider, board in cases:
            with self.subTest(url=url):
                report = build_source_manifest([url])
                self.assertEqual(report.sources, (SourceSpec(f"{provider}:{board}", provider, board),))
                self.assertTrue(report.inputs[0].automatic)
                self.assertEqual(validate_source_manifest(report.manifest()), report.sources)

    def test_deduplicates_alias_hosts_and_job_links_without_merging_other_routes(self) -> None:
        report = build_source_manifest([
            "https://boards.greenhouse.io/fictional",
            "https://job-boards.greenhouse.io/fictional/jobs/1",
            "https://boards.greenhouse.io/fictional/jobs/2",
            "https://jobs.ashbyhq.com/fictional",
            "https://jobs.lever.co/fictional",
            "https://jobs.eu.lever.co/fictional",
            "https://jobs.ashbyhq.com/Fictional",
        ])
        self.assertEqual(len(report.sources), 5)
        self.assertEqual([item.duplicate for item in report.inputs], [False, True, True, False, False, False, False])
        self.assertEqual([item.position for item in report.inputs], list(range(1, 8)))
        self.assertEqual(report.inputs[0].source_id, report.inputs[2].source_id)
        reversed_report = build_source_manifest(list(reversed([
            "https://jobs.ashbyhq.com/fictional", "https://jobs.lever.co/fictional"])))
        self.assertEqual({source.id for source in reversed_report.sources}, {"ashby:fictional", "lever:fictional"})

    def test_unknown_hosts_are_visible_manual_sources_and_never_fetched(self) -> None:
        report = build_source_manifest([
            "https://example.com/careers",
            "https://EXAMPLE.COM/careers",
            "https://example.org/jobs/123",
            "https://jobs.lever.co.example.com/fictional",
        ])
        self.assertEqual(len(report.sources), 3)
        self.assertTrue(all(source.provider == "manual" for source in report.sources))
        self.assertTrue(all(not item.automatic for item in report.inputs))
        self.assertTrue(report.inputs[1].duplicate)
        transport = FakeTransport({})
        result = DiscoveryService(transport).discover(report.sources)
        self.assertEqual(transport.calls, [])
        self.assertTrue(all(source.status == "manual_required" for source in result.sources))
        self.assertEqual(report.sources[0].careers_url, "https://example.com/careers")

    def test_recognized_route_discards_and_accounts_for_parameters_without_echo(self) -> None:
        report = build_source_manifest([
            "HTTPS://JOBS.ASHBYHQ.COM/Fictional?location=unknown&token=do-not-retain#research",
            "https://jobs.ashbyhq.com/Fictional/",
        ])
        self.assertEqual(len(report.sources), 1)
        self.assertTrue(report.inputs[0].discarded_query)
        self.assertTrue(report.inputs[0].discarded_fragment)
        self.assertFalse(report.inputs[1].discarded_query)
        self.assertFalse(report.inputs[1].discarded_fragment)
        self.assertNotIn("do-not-retain", repr(report))
        self.assertNotIn("research", repr(report.manifest()))

    def test_unknown_parameters_fail_without_silently_changing_page_identity(self) -> None:
        for url in (
            "https://example.com/careers?company=fictional",
            "https://example.com/#/careers",
            "https://boards.greenhouse.io/embed/job_app?for=fictional&token=123",
            "https://jobs.lever.co/fictional/post-1/unrecognized?token=secret",
        ):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "query-free, fragment-free") as caught:
                build_source_manifest(["https://jobs.lever.co/fictional", url])
            self.assertNotIn(url, str(caught.exception))

    def test_unsupported_route_shapes_do_not_gain_automatic_access(self) -> None:
        for url in (
            "https://boards.greenhouse.io/fictional/jobs/0",
            "https://boards.greenhouse.io/fictional/jobs/001",
            "https://boards.greenhouse.io/fictional/jobs/not-an-id",
            "https://boards.greenhouse.io/fictional/jobs/1/application",
            "https://jobs.ashbyhq.com/fictional/post-1/apply",
            "https://jobs.lever.co/fictional/post-1/application",
            "https://jobs.lever.co/fictional//",
            "https://jobs.lever.co/%66ictional",
            "https://jobs.lever.co/fictional/..",
            "https://jobs.lever.co/fictional/../other",
            "https://jobs.lever.co/fictional/post%2f1",
            "https://jobs.lever.co./fictional",
            "https://explore.jobs.netflix.net/careers/sitemap.xml",
            "https://explore.jobs.netflix.net/careers/job/0",
        ):
            with self.subTest(url=url):
                report = build_source_manifest([url])
                self.assertFalse(report.inputs[0].automatic)
                self.assertEqual(report.sources[0].provider, "manual")

    def test_unsafe_inputs_are_rejected_without_echoing_content(self) -> None:
        for value in (
            None, True, 1, b"https://example.com", "", " " * 2,
            "https://jobs.lever.co/fictional\n", "https://jobs.lever.co/fictional\t",
            "https://jobs.lever.co/fictional\x00", "https://jobs.lever.co/fictional\x7f",
            "http://jobs.lever.co/fictional", "//jobs.lever.co/fictional",
            "file:///private/input", "https:///fictional", "https://",
            "https://user:secret@jobs.lever.co/fictional",
            "https://@jobs.lever.co/fictional", "https://jobs.lever.co:443/fictional",
            "https://jobs.lever.co:/fictional", "https://jobs.lever.co:bad/fictional",
            "https://jobs.lever.co\\example.com/fictional", "https://[broken/fictional",
            "https://example.com/" + "a" * 2048, "https://example.com/\ud800",
        ):
            with self.subTest(value=repr(value)), self.assertRaises(ValueError) as caught:
                build_source_manifest([value])
            self.assertNotIn("secret", str(caught.exception))
            self.assertNotIn("jobs.lever.co", str(caught.exception))

    def test_setup_is_bounded_without_silent_source_truncation(self) -> None:
        for value in ([], (), None, "https://example.com", {"https://example.com"},
            ["https://example.com"] * 257):
            with self.subTest(value=type(value)), self.assertRaises(ValueError):
                build_source_manifest(value)
        unique = [f"https://jobs.lever.co/fictional-{index}" for index in range(32)]
        report = build_source_manifest(unique)
        self.assertEqual(len(report.sources), 32)
        with self.assertRaisesRegex(ValueError, "split the watchlist"):
            build_source_manifest(unique + ["https://example.com/careers"])
        report = build_source_manifest(["https://jobs.lever.co/fictional"] * 256)
        self.assertEqual(len(report.sources), 1)
        self.assertEqual(len(report.inputs), 256)

    def test_setup_is_offline_and_manifest_runs_through_existing_connector(self) -> None:
        with patch("socket.getaddrinfo", side_effect=AssertionError("Unexpected network")):
            report = build_source_manifest([
                "https://job-boards.greenhouse.io/example/jobs/1?gh_src=fictional",
                "https://example.com/careers",
            ])
        transport = FakeTransport({
            "https://boards-api.greenhouse.io/v1/boards/example/jobs?content=true":
                encoded({"jobs": [greenhouse_job(1), greenhouse_job(2)]}),
        })
        result = DiscoveryService(transport).discover(validate_source_manifest(report.manifest()))
        self.assertEqual(len(result.jobs), 2)
        self.assertEqual([source.status for source in result.sources], ["successful", "manual_required"])
        self.assertEqual(len(transport.calls), 1)
        self.assertNotIn("gh_src", transport.calls[0][0])

    def test_manifest_output_is_independent_and_does_not_expose_null_fields(self) -> None:
        report = build_source_manifest(["https://jobs.lever.co/fictional", "https://example.com/careers"])
        first = report.manifest()
        self.assertEqual(set(first["sources"][0]), {"id", "provider", "board"})
        self.assertEqual(set(first["sources"][1]), {"id", "provider", "careers_url"})
        first["sources"].clear()
        self.assertEqual(len(report.manifest()["sources"]), 2)

    def test_workable_requires_a_company_board_and_never_infers_one_from_a_job(self) -> None:
        urls = [
            "https://apply.workable.com/j/ABCDEF1234",
            "https://apply.workable.com/j/ABCDEF1234/apply",
            "https://apply.workable.com/fictional/j/ABCDEF1234",
            "https://apply.workable.com/api/v1/widget/accounts/fictional",
            "https://apply.workable.com/api",
            "https://apply.workable.com/j",
            "https://apply.workable.com/fictional//",
            "https://apply.workable.com/%66ictional",
            "https://www.workable.com/fictional",
            "https://apply.workable.com.example.com/fictional",
        ]
        with patch("socket.getaddrinfo", side_effect=AssertionError("Unexpected network")):
            report = build_source_manifest(urls)
        self.assertTrue(all(source.provider == "manual" for source in report.sources))
        self.assertTrue(all(not item.automatic for item in report.inputs))
        transport = FakeTransport({})
        results = DiscoveryService(transport).discover(report.sources)
        self.assertEqual(transport.calls, [])
        self.assertTrue(all(source.status == "manual_required" for source in results.sources))

    def test_workable_board_filters_are_explicitly_discarded_and_boards_deduplicate(self) -> None:
        report = build_source_manifest([
            "https://apply.workable.com/fictional/?location=remote#jobs",
            "https://apply.workable.com/fictional",
            "https://apply.workable.com/other-fictional",
        ])
        self.assertEqual(report.sources, (SourceSpec("workable:fictional", "workable", "fictional"),
            SourceSpec("workable:other-fictional", "workable", "other-fictional")))
        self.assertEqual([item.duplicate for item in report.inputs], [False, True, False])
        self.assertTrue(report.inputs[0].discarded_query)
        self.assertTrue(report.inputs[0].discarded_fragment)
        self.assertNotIn("remote", repr(report.manifest()))
        self.assertEqual(validate_source_manifest(report.manifest()), report.sources)


if __name__ == "__main__":
    unittest.main()
