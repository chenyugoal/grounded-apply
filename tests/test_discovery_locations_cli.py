from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from grounded_apply.cli import main
from grounded_apply.services.discovery import DiscoveryService


MANIFEST = {"schema_version": 1, "sources": [
    {"id": "fictional-locations", "provider": "greenhouse", "board": "fictional-locations"}]}


class FixedDatetime:
    @classmethod
    def now(cls, zone: object) -> datetime:
        return datetime(2026, 9, 21, 11, 0, tzinfo=UTC)


class Feed:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.body = {"jobs": [
            {"id": index, "title": title, "location": {"name": location} if location is not None else None,
             "absolute_url": f"https://job-boards.greenhouse.io/fictional-locations/jobs/{index}",
             "content": "<h2>Requirements</h2><p>Fictional Python experience.</p>"}
            for index, title, location in (
                (1, "Fictional Research Engineer", "Other Fictional City"),
                (2, "Fictional Research Engineer", "Preferred Fictional City"),
                (3, "Fictional Research Engineer", None),
                (4, "Fictional Designer", "Remote"),
            )], "meta": {"total": 4}}

    def get(self, url: str, *, max_bytes: int, timeout: float) -> bytes:
        self.calls.append(url)
        return json.dumps(self.body).encode()


class DiscoveryLocationsCliTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory(prefix="gapply-location-cli-")
        self.addCleanup(folder.cleanup)
        self.home = Path(folder.name) / "private-home"
        environment = patch.dict(os.environ, {"GROUNDED_APPLY_HOME": str(self.home)})
        environment.start()
        self.addCleanup(environment.stop)

    def invoke(self, args: list[str], *, feed: Feed | None = None, manifest: object = MANIFEST) -> tuple[int, str, str]:
        output, errors = io.StringIO(), io.StringIO()
        with patch("sys.stdin", io.StringIO(json.dumps(manifest))), redirect_stdout(output), redirect_stderr(errors), \
             patch("grounded_apply.services.discovery.datetime", FixedDatetime), \
             patch("grounded_apply.repositories.discovery_http.PublicJobHTTPTransport", return_value=feed or Feed()):
            code = main(args)
        return code, output.getvalue(), errors.getvalue()

    def discover(self, *args: str, feed: Feed | None = None, manifest: object = MANIFEST) -> tuple[int, dict, str]:
        code, output, errors = self.invoke(
            ["--log-events", "jobs", "discover", "--sources-file", "-", *args, "--json"], feed=feed, manifest=manifest)
        return code, json.loads(output), errors

    def snapshot(self) -> dict[str, tuple[bytes | None, int, int, int, int, int]]:
        if not self.home.exists():
            return {}
        result = {}
        for path in [self.home, *sorted(self.home.rglob("*"))]:
            info = path.lstat()
            result[str(path.relative_to(self.home))] = (
                path.read_bytes() if path.is_file() else None, info.st_mode, info.st_ino,
                info.st_nlink, info.st_mtime_ns, info.st_ctime_ns)
        return result

    def test_no_location_flags_preserve_recorded_entire_json_and_human_outputs(self) -> None:
        # Recorded from the prior implementation before the location CLI edit.
        hashes = {
            "json": ("39cf5778ede9f662d8b5d00b016c3275542151a420c1d7d2f8d14b55cac06f98",
                     "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
            "human": ("8c0e08de6d33a42acdb53128bcedd254b13edd466c387ae0528fd029251d8a21",
                      "c9b21759d475a9522b4baa0e3650ea83bb0905db4b0c9edb6de80bc25b4757ce"),
        }
        for mode, expected in hashes.items():
            with self.subTest(mode=mode):
                code, output, errors = self.invoke(["jobs", "discover", "--sources-file", "-",
                    "--title-contains", "Research Engineer", "--limit-per-source", "1", "--dry-run"]
                    + (["--json"] if mode == "json" else []))
                self.assertEqual(code, 2)
                self.assertEqual(tuple(hashlib.sha256(value.encode()).hexdigest() for value in (output, errors)), expected)
        self.assertFalse(self.home.exists())

    def test_title_and_location_selection_reaches_later_posting_before_quota(self) -> None:
        feed = Feed()
        code, result, events = self.discover("--dry-run", "--title-contains", "Research Engineer",
            "--location-contains", "pReFeRrEd", "--missing-location", "exclude", "--limit-per-source", "1", feed=feed)
        self.assertEqual(code, 0, result)
        data = result["data"]
        self.assertEqual(data["schema_version"], 2)
        self.assertEqual(data["filter_method"], "title_location_substring_or@1")
        self.assertEqual(data["location_contains"], ["pReFeRrEd"])
        self.assertEqual(data["missing_location"], "exclude")
        self.assertEqual([job["external_id"] for job in data["jobs"]], ["2"])
        self.assertEqual(data["jobs"][0]["location"], "Preferred Fictional City")
        self.assertEqual(data["selections"], [{"source_id": "fictional-locations", "valid_count": 4,
            "title_filtered_count": 1, "location_filtered_count": 1, "unknown_excluded_count": 1,
            "unknown_included_count": 0, "selected_count": 1, "selected_unknown_count": 0, "limit_deferred_count": 0}])
        self.assertEqual(data["sources"][0]["filtered_count"], 3)
        self.assertEqual(data["sources"][0]["status"], "successful")
        self.assertFalse(data["storage_checked"])
        self.assertFalse(data["external_submission_taken"])
        self.assertEqual(data["captures"], [])
        self.assertEqual(len(feed.calls), 1)
        self.assertFalse(self.home.exists())
        records = [json.loads(line) for line in events.splitlines()]
        self.assertEqual([record["command"] for record in records], ["jobs.discover"] * 2)
        self.assertEqual(records[-1]["outcome"], "succeeded")
        for private in ("pReFeRrEd", "Preferred", "fictional-locations", "Python", str(self.home)):
            self.assertNotIn(private, events)

    def test_default_included_unknown_counts_are_distinct_from_selected_unknown(self) -> None:
        code, result, _ = self.discover("--dry-run", "--location-contains", "Preferred",
            "--title-contains", "Research Engineer", "--limit-per-source", "1")
        self.assertEqual(code, 2, result)
        data = result["data"]
        self.assertEqual(data["missing_location"], "include")
        self.assertEqual(data["selections"][0]["unknown_included_count"], 1)
        self.assertEqual(data["selections"][0]["selected_unknown_count"], 0)
        self.assertEqual(data["selections"][0]["limit_deferred_count"], 1)
        self.assertEqual(data["sources"][0]["errors"], ["source_limit_reached"])
        self.assertFalse(result["ok"])
        code, result, _ = self.discover("--dry-run", "--location-contains", "Preferred",
            "--title-contains", "Research Engineer", "--limit-per-source", "2")
        self.assertEqual(code, 0, result)
        self.assertEqual([job["external_id"] for job in result["data"]["jobs"]], ["2", "3"])
        self.assertIsNone(result["data"]["jobs"][1]["location"])
        self.assertEqual(result["data"]["selections"][0]["selected_unknown_count"], 1)

    def test_missing_location_option_alone_selects_schema_two(self) -> None:
        for policy, expected in (("include", ["1", "2", "3", "4"]), ("exclude", ["1", "2", "4"])):
            with self.subTest(policy=policy):
                code, result, _ = self.discover("--dry-run", "--missing-location", policy)
                self.assertEqual(code, 0, result)
                self.assertEqual(result["data"]["schema_version"], 2)
                self.assertEqual(result["data"]["location_contains"], [])
                self.assertEqual([job["external_id"] for job in result["data"]["jobs"]], expected)

    def test_literal_repeated_terms_do_not_infer_remote_eligibility(self) -> None:
        feed = Feed()
        feed.body["jobs"][0]["location"]["name"] = "Not Remote"
        code, result, _ = self.discover("--dry-run", "--title-contains", "Engineer",
            "--location-contains", "Remote", "--location-contains", "PREFERRED", "--missing-location", "exclude", feed=feed)
        self.assertEqual(code, 0, result)
        self.assertEqual([job["external_id"] for job in result["data"]["jobs"]], ["1", "2"])
        self.assertEqual(result["data"]["jobs"][0]["location"], "Not Remote")
        self.assertEqual(result["data"]["location_contains"], ["Remote", "PREFERRED"])

    def test_invalid_location_options_fail_before_source_storage_or_transport(self) -> None:
        invalid = [["--location-contains", term] for term in ("", " ", " padded ", "x" * 129, "x\nprivate", "x\u200bprivate", "x\ud800private")]
        invalid += [["--location-contains", "Fictional"] * 21, ["--missing-location", "guess"]]
        for args in invalid:
            for source_args in (["--sources-file", "-"], ["--sources-file", "/unreadable/private.json"], ["--preset", "major-tech"]):
                with self.subTest(args=args, source=source_args), \
                     patch("grounded_apply.cli._read_utf8_input") as reader, \
                     patch("grounded_apply.cli.resolve_runtime_paths") as paths, \
                     patch("grounded_apply.repositories.discovery_http.PublicJobHTTPTransport") as transport:
                    out, err = io.StringIO(), io.StringIO()
                    with redirect_stdout(out), redirect_stderr(err):
                        code = main(["--log-events", "jobs", "discover", *source_args, *args, "--json"])
                    self.assertEqual(code, 2)
                    self.assertFalse(json.loads(out.getvalue())["ok"])
                    reader.assert_not_called()
                    paths.assert_not_called()
                    transport.assert_not_called()
                    self.assertNotIn("private", out.getvalue() + err.getvalue())
        self.assertFalse(self.home.exists())

    def test_opt_in_invalid_title_or_quota_fails_before_all_input_and_runtime(self) -> None:
        for args in (["--title-contains", ""], ["--title-contains", "x" * 129],
                     ["--limit-per-source", "0"], ["--limit-per-source", "1001"]):
            with self.subTest(args=args), patch("grounded_apply.cli._read_utf8_input") as reader, \
                 patch("grounded_apply.cli.resolve_runtime_paths") as paths, \
                 patch("grounded_apply.repositories.discovery_http.PublicJobHTTPTransport") as transport:
                output, errors = io.StringIO(), io.StringIO()
                with redirect_stdout(output), redirect_stderr(errors):
                    code = main(["jobs", "discover", "--sources-file", "-", "--missing-location", "include", *args, "--json"])
                self.assertEqual(code, 2)
                self.assertFalse(json.loads(output.getvalue())["ok"])
                reader.assert_not_called()
                paths.assert_not_called()
                transport.assert_not_called()

    def test_manual_and_partial_source_reports_remain_visible_with_zero_matches(self) -> None:
        feed = Feed()
        feed.body["jobs"].append({"id": "invalid"})
        feed.body["jobs"].append(dict(feed.body["jobs"][0]))
        feed.body["meta"]["total"] = 6
        manifest = {"schema_version": 1, "sources": [*MANIFEST["sources"],
            {"id": "fictional-manual", "provider": "manual", "careers_url": "https://example.com/careers"}]}
        with patch("grounded_apply.services.discovery.MAX_SOURCE_RECORDS", 5):
            code, result, _ = self.discover("--dry-run", "--location-contains", "No matching fictional place",
                "--missing-location", "exclude", manifest=manifest, feed=feed)
        self.assertEqual(code, 2, result)
        self.assertEqual(result["data"]["jobs"], [])
        sources = result["data"]["sources"]
        self.assertEqual(sources[0]["status"], "partial")
        self.assertEqual(set(sources[0]["errors"]), {"invalid_record", "source_limit_reached"})
        self.assertEqual(sources[1]["status"], "manual_required")
        self.assertEqual(result["data"]["selections"][0]["valid_count"], 4)
        self.assertEqual(result["data"]["selections"][1]["valid_count"], 0)
        self.assertEqual(len(feed.calls), 1)
        self.assertFalse(self.home.exists())

    def test_capture_requires_storage_before_network_and_replays_filtered_snapshots(self) -> None:
        feed = Feed()
        code, _, _ = self.discover("--location-contains", "Preferred", "--missing-location", "exclude", feed=feed)
        self.assertEqual(code, 2)
        self.assertEqual(feed.calls, [])
        self.assertFalse(self.home.exists())
        self.assertEqual(self.invoke(["profile", "init", "--json"])[0], 0)
        code, result, _ = self.discover("--location-contains", "Preferred", "--missing-location", "exclude")
        self.assertEqual(code, 0, result)
        data = result["data"]
        self.assertTrue(data["storage_checked"])
        self.assertEqual([row["external_id"] for row in data["captures"]], ["2"])
        self.assertNotIn("source_text", data["jobs"][0])
        before = self.snapshot()
        retry_code, retry, _ = self.discover("--location-contains", "PREFERRED", "--missing-location", "exclude")
        self.assertEqual(retry_code, 0, retry)
        self.assertTrue(retry["data"]["captures"][0]["replayed"])
        self.assertEqual(data["captures"][0]["job_id"], retry["data"]["captures"][0]["job_id"])
        self.assertEqual(self.snapshot(), before)
        show_code, show, _ = self.invoke(["jobs", "show", "--job-id", data["captures"][0]["job_id"], "--json"])
        self.assertEqual(show_code, 0)
        self.assertEqual(json.loads(show)["data"]["capture_method"], "public_ats_feed")
        self.assertEqual(self.snapshot(), before)

    def test_opt_in_dry_run_preserves_existing_runtime(self) -> None:
        self.assertEqual(self.invoke(["profile", "init", "--json"])[0], 0)
        before = self.snapshot()
        code, result, _ = self.discover("--dry-run", "--missing-location", "exclude")
        self.assertEqual(code, 0, result)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(result["data"]["storage_checked"])

    def test_human_output_shows_exact_locations_unknowns_counts_and_safe_terms(self) -> None:
        code, output, errors = self.invoke(["jobs", "discover", "--sources-file", "-", "--dry-run",
            "--location-contains", "Preferred", "--title-contains", "Engineer"])
        self.assertEqual(code, 0, errors)
        self.assertIn("Published location: Preferred Fictional City", output)
        self.assertIn("Published location: unknown (included by policy)", output)
        self.assertIn("1 selected with unknown location", output)
        self.assertIn("Text matches do not establish geographic eligibility", output)
        self.assertNotIn("Fictional Designer", output)
        # Existing term policy permits internal Unicode line separators; the
        # human boundary must render them as escaped text, never extra UI lines.
        code, output, _ = self.invoke(["jobs", "discover", "--sources-file", "-", "--dry-run",
            "--location-contains", "Fictional\u2028City", "--missing-location", "exclude"])
        self.assertEqual(code, 0)
        self.assertNotIn("\u2028", output)
        self.assertIn("\\u2028", output)

    def test_unexpected_service_errors_are_content_free_and_interrupts_survive(self) -> None:
        for error in (RuntimeError("fictional-private-path"), ValueError("fictional-private-path"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__), patch.object(DiscoveryService, "discover_with_locations", side_effect=error):
                code, output, events = self.invoke(["--log-events", "jobs", "discover", "--sources-file", "-",
                    "--location-contains", "Preferred", "--dry-run", "--json"])
                self.assertEqual(code, 130 if isinstance(error, KeyboardInterrupt) else 2)
                self.assertNotIn("fictional-private-path", output + events)
                if code == 2:
                    self.assertEqual(json.loads(output)["error"]["message"], "Location-filtered discovery failed; no snapshots were saved")
                self.assertEqual(json.loads(events.splitlines()[-1])["outcome"], "interrupted" if code == 130 else "failed")
        self.assertFalse(self.home.exists())

    def test_output_failure_keeps_fixed_preview_or_recovery_message(self) -> None:
        for capture in (False, True):
            if capture:
                self.assertEqual(self.invoke(["profile", "init", "--json"])[0], 0)
            with self.subTest(capture=capture), patch("grounded_apply.cli._emit", side_effect=RuntimeError("fictional-private-output")):
                code, output, errors = self.invoke(["jobs", "discover", "--sources-file", "-", "--location-contains", "Preferred",
                    "--missing-location", "exclude", "--json"] + ([] if capture else ["--dry-run"]))
                self.assertEqual(code, 2)
                self.assertNotIn("fictional-private-output", output + errors)
                self.assertIn("snapshots may already be saved" if capture else "no snapshots were saved", errors)
        code, result, _ = self.discover("--location-contains", "Preferred", "--missing-location", "exclude")
        self.assertEqual(code, 0, result)
        self.assertTrue(result["data"]["captures"][0]["replayed"])


if __name__ == "__main__":
    unittest.main()
