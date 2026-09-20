from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from grounded_apply.services.schedule_policy import (
    DailyOccurrence, MAX_DATE, MIN_DATE, due_instant, latest_due, next_due, validate_schedule_manifest,
)


class SchedulePolicyTests(unittest.TestCase):
    def manifest(self, **changes):
        return {"schema_version": 1, "search_id": "fictional-search", "timezone": "America/Chicago",
            "local_time": "09:30", "start_date": "2026-09-19", **changes}

    def test_manifest_is_closed_explicit_and_normalized_without_mutation(self) -> None:
        original = self.manifest()
        result = validate_schedule_manifest(original)
        self.assertEqual(result, {**original, "max_items": 20, "max_seconds": 900, "max_attempts": 3})
        self.assertEqual(validate_schedule_manifest(result), result)
        self.assertNotIn("max_items", original)
        self.assertEqual(validate_schedule_manifest(self.manifest(max_items=50, max_seconds=3600, max_attempts=10))["max_items"], 50)
        for key in original:
            partial = original.copy()
            del partial[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                validate_schedule_manifest(partial)
        for value in (None, [], {}, True, self.manifest(schema_version=True), self.manifest(schema_version=2),
            self.manifest(end_date="2027-01-01"), self.manifest(search_id=""), self.manifest(search_id="../private"),
            self.manifest(search_id="a" * 257), self.manifest(start_date="20260919"),
            self.manifest(start_date="2026-9-19"), self.manifest(start_date="2026-02-29"),
            self.manifest(start_date="1999-12-31"), self.manifest(start_date="2101-01-01"),
            self.manifest(start_date=date(2026, 9, 19))):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_schedule_manifest(value)

    def test_budget_types_and_bounds_do_not_accept_bool_or_coercion(self) -> None:
        for key, maximum in (("max_items", 50), ("max_seconds", 3600), ("max_attempts", 10)):
            for invalid in (True, False, 0, -1, maximum + 1, 1.0, "1", None):
                with self.subTest(key=key, invalid=invalid), self.assertRaises(ValueError):
                    validate_schedule_manifest(self.manifest(**{key: invalid}))
            self.assertEqual(validate_schedule_manifest(self.manifest(**{key: 1}))[key], 1)

    def test_zone_and_time_require_installed_explicit_canonical_syntax(self) -> None:
        for zone in (None, "", " America/Chicago", "America/Chicago ", "Mars/Example", "/etc/localtime",
            "../UTC", "America//Chicago", "localtime", "posixrules", "right/UTC", "posix/UTC", "UTC\x00"):
            with self.subTest(zone=zone), self.assertRaises(ValueError):
                validate_schedule_manifest(self.manifest(timezone=zone))
        for wall in (None, 930, "9:30", "09:3", "09:30:00", "09:30Z", "24:00", "29:00", "12:60", " 09:30", "０９:３０"):
            with self.subTest(wall=wall), self.assertRaises(ValueError):
                validate_schedule_manifest(self.manifest(local_time=wall))
        with patch("grounded_apply.services.schedule_policy.ZoneInfo", side_effect=ZoneInfoNotFoundError):
            with self.assertRaisesRegex(ValueError, "installed IANA"):
                validate_schedule_manifest(self.manifest())

    def test_chicago_gap_uses_transition_not_shifted_wall_minutes(self) -> None:
        self.assertEqual(due_instant(date(2026, 3, 8), "America/Chicago", "02:45"), datetime(2026, 3, 8, 8, tzinfo=UTC))
        self.assertEqual(due_instant(date(2026, 3, 8), "America/Chicago", "03:00"), datetime(2026, 3, 8, 8, tzinfo=UTC))

    def test_chicago_fold_has_one_occurrence_at_first_instant(self) -> None:
        expected = datetime(2026, 11, 1, 6, 30, tzinfo=UTC)
        self.assertEqual(due_instant(date(2026, 11, 1), "America/Chicago", "01:30"), expected)
        args = {"start_date": date(2026, 11, 1), "zone": "America/Chicago", "local_time": "01:30"}
        for clock in (expected, expected + timedelta(hours=1)):
            self.assertEqual(latest_due(clock, **args), DailyOccurrence(date(2026, 11, 1), expected))
            self.assertEqual(next_due(clock, **args).local_date, date(2026, 11, 2))

    def test_lord_howe_half_hour_gap_and_fold(self) -> None:
        self.assertEqual(due_instant(date(2026, 10, 4), "Australia/Lord_Howe", "02:15"), datetime(2026, 10, 3, 15, 30, tzinfo=UTC))
        self.assertEqual(due_instant(date(2026, 4, 5), "Australia/Lord_Howe", "01:45"), datetime(2026, 4, 4, 14, 45, tzinfo=UTC))

    def test_entirely_skipped_apia_day_never_becomes_duplicate_next_day(self) -> None:
        skipped = date(2011, 12, 30)
        for wall in ("00:00", "09:30", "23:59"):
            with self.subTest(wall=wall):
                self.assertIsNone(due_instant(skipped, "Pacific/Apia", wall))
        args = {"start_date": date(2011, 12, 29), "zone": "Pacific/Apia", "local_time": "09:30"}
        before = datetime(2011, 12, 30, 9, 59, tzinfo=UTC)
        self.assertEqual(latest_due(before, **args).local_date, date(2011, 12, 29))
        following = next_due(before, **args)
        self.assertEqual(following, DailyOccurrence(date(2011, 12, 31), datetime(2011, 12, 30, 19, 30, tzinfo=UTC)))
        self.assertIsNone(latest_due(before, **{**args, "start_date": skipped}))

    def test_midnight_gap_stays_on_same_civil_day(self) -> None:
        self.assertEqual(due_instant(date(2018, 11, 4), "America/Sao_Paulo", "00:00"), datetime(2018, 11, 4, 3, tzinfo=UTC))

    def test_exact_boundary_and_local_midnight_not_utc_date(self) -> None:
        args = {"start_date": date(2026, 9, 19), "zone": "America/Chicago", "local_time": "23:30"}
        due = datetime(2026, 9, 20, 4, 30, tzinfo=UTC)
        self.assertIsNone(latest_due(due - timedelta(microseconds=1), **args))
        self.assertEqual(next_due(due - timedelta(microseconds=1), **args), DailyOccurrence(date(2026, 9, 19), due))
        self.assertEqual(latest_due(due, **args), DailyOccurrence(date(2026, 9, 19), due))
        self.assertEqual(next_due(due, **args).local_date, date(2026, 9, 20))
        tokyo = {"start_date": date(2026, 9, 20), "zone": "Asia/Tokyo", "local_time": "00:00"}
        self.assertEqual(latest_due(datetime(2026, 9, 19, 15, tzinfo=UTC), **tokyo).local_date, date(2026, 9, 20))

    def test_offline_and_clock_rollback_have_no_internal_history_or_catchup_loop(self) -> None:
        args = {"start_date": date(2026, 1, 1), "zone": "UTC", "local_time": "09:30"}
        self.assertEqual(latest_due(datetime(2026, 9, 19, 10, tzinfo=UTC), **args).local_date, date(2026, 9, 19))
        self.assertEqual(latest_due(datetime(2026, 9, 18, 10, tzinfo=UTC), **args).local_date, date(2026, 9, 18))
        self.assertEqual(next_due(datetime(2025, 1, 1, tzinfo=UTC), **args).local_date, date(2026, 1, 1))

    def test_clock_requires_aware_utc_and_accepts_explicit_utc_equivalents(self) -> None:
        args = {"start_date": date(2026, 1, 1), "zone": "UTC", "local_time": "09:30"}
        for clock in (None, date(2026, 1, 2), datetime(2026, 1, 2),
            datetime(2026, 1, 2, tzinfo=timezone(timedelta(hours=1))),
            datetime(2026, 1, 2, tzinfo=ZoneInfo("Europe/London"))):
            for helper in (latest_due, next_due):
                with self.subTest(clock=clock, helper=helper.__name__), self.assertRaises(ValueError):
                    helper(clock, **args)
        for zone in (UTC, ZoneInfo("UTC"), ZoneInfo("Etc/UTC"), timezone(timedelta(0), "fixed-zero")):
            self.assertEqual(latest_due(datetime(2026, 1, 2, 10, tzinfo=zone), **args).local_date, date(2026, 1, 2))

    def test_date_bounds_and_types_fail_closed_without_overflow(self) -> None:
        for selected in (date(1999, 12, 31), date(2101, 1, 1), datetime(2026, 1, 1), "2026-01-01", None):
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                due_instant(selected, "UTC", "00:00")
        self.assertEqual(due_instant(MIN_DATE, "UTC", "00:00"), datetime(2000, 1, 1, tzinfo=UTC))
        self.assertEqual(due_instant(MAX_DATE, "UTC", "23:59"), datetime(2100, 12, 31, 23, 59, tzinfo=UTC))
        args = {"start_date": MIN_DATE, "zone": "Pacific/Kiritimati", "local_time": "23:59"}
        self.assertIsNone(latest_due(datetime(1999, 12, 30, tzinfo=UTC), **args))
        self.assertIsNone(next_due(datetime(2101, 1, 1, tzinfo=UTC), **args))
        self.assertEqual(latest_due(datetime(2101, 1, 1, tzinfo=UTC), **args).local_date, MAX_DATE)
        self.assertEqual(next_due(datetime(1999, 12, 30, tzinfo=UTC), **args).local_date, MIN_DATE)
        self.assertIsNone(next_due(datetime.max.replace(tzinfo=UTC), **args))
        self.assertIsNone(latest_due(datetime.min.replace(tzinfo=UTC), **args))
        self.assertEqual(next_due(datetime.min.replace(tzinfo=UTC), **args).local_date, MIN_DATE)
        self.assertEqual(latest_due(datetime.max.replace(tzinfo=UTC), **args).local_date, MAX_DATE)


if __name__ == "__main__":
    unittest.main()
