"""Pure daily scheduling policy; no clock reads, persistence or external actions.

The caller owns authorization and durable occurrence identity. Local dates are
bounded to 2000–2100. Gaps advance to the first valid instant on that civil date;
entirely missing dates are skipped. Folds select the earlier UTC instant.
Time-zone rules come from the installed IANA database; persist resolved due times
when claiming occurrences so a later database update cannot move existing work.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


MIN_DATE = date(2000, 1, 1)
MAX_DATE = date(2100, 12, 31)
_DAY = timedelta(days=1)
_MICROSECOND = timedelta(microseconds=1)


@dataclass(frozen=True, slots=True)
class DailyOccurrence:
    local_date: date
    due_at: datetime


def _date(value: object) -> date:
    if type(value) is not date or not MIN_DATE <= value <= MAX_DATE:
        raise ValueError("Schedule dates must be civil dates from 2000 through 2100")
    return value


def _time(value: object) -> time:
    if type(value) is not str or re.fullmatch(r"[0-2][0-9]:[0-5][0-9]", value) is None:
        raise ValueError("Schedule local_time must use HH:MM")
    try:
        return time.fromisoformat(value)
    except ValueError:
        raise ValueError("Schedule local_time must use a valid HH:MM") from None


def _zone(value: object) -> ZoneInfo:
    if (type(value) is not str or len(value) > 128
        or re.fullmatch(r"[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+)*", value) is None
        or value.split("/")[0] in {"posix", "right", "localtime", "posixrules"}):
        raise ValueError("Schedule timezone must name an explicit installed IANA zone")
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("Schedule timezone must name an explicit installed IANA zone") from None


def _clock(value: object) -> datetime:
    if (type(value) is not datetime or value.utcoffset() != timedelta(0)
        or not (isinstance(value.tzinfo, timezone)
            or isinstance(value.tzinfo, ZoneInfo) and value.tzinfo.key in {"UTC", "Etc/UTC", "GMT", "Etc/GMT"})):
        raise ValueError("Schedule clock must be an aware UTC datetime")
    return value.astimezone(UTC)


def _clock_date(clock: datetime, zone: ZoneInfo) -> date:
    # Clocks far outside the supported civil window need no zone conversion;
    # clamping also avoids datetime overflow near years 1 and 9999.
    if clock.date() < MIN_DATE - _DAY:
        return MIN_DATE - _DAY
    if clock.date() > MAX_DATE + _DAY:
        return MAX_DATE + _DAY
    return clock.astimezone(zone).date()


def validate_schedule_manifest(value: object) -> dict[str, Any]:
    """Normalize a closed version-1 scope without looking up its saved search."""
    required = {"schema_version", "search_id", "timezone", "local_time", "start_date"}
    limits = {"max_items": (20, 50), "max_seconds": (900, 3600), "max_attempts": (3, 10)}
    if (type(value) is not dict or not required <= set(value)
        or set(value) - required - set(limits)
        or type(value["schema_version"]) is not int or value["schema_version"] != 1):
        raise ValueError("Schedule requires version 1 and an explicit search, zone, time and start date")
    identifier = value["search_id"]
    if type(identifier) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", identifier) is None:
        raise ValueError("Schedule search_id must be an opaque identifier")
    _zone(value["timezone"])
    _time(value["local_time"])
    start = value["start_date"]
    if type(start) is not str or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", start) is None:
        raise ValueError("Schedule start_date must use YYYY-MM-DD")
    try:
        _date(date.fromisoformat(start))
    except ValueError:
        raise ValueError("Schedule start_date must be a valid date from 2000 through 2100") from None
    normalized = {key: value[key] for key in ("schema_version", "search_id", "timezone", "local_time", "start_date")}
    for key, (default, maximum) in limits.items():
        selected = value.get(key, default)
        if type(selected) is not int or not 1 <= selected <= maximum:
            raise ValueError("Schedule limits must be positive integers within supported bounds")
        normalized[key] = selected
    return normalized


def _resolve(local_date: date, zone: ZoneInfo, local_time: time) -> datetime | None:
    wall = datetime.combine(local_date, local_time)
    candidates = sorted({wall.replace(tzinfo=zone, fold=fold).astimezone(UTC) for fold in (0, 1)})
    valid = [instant for instant in candidates if instant.astimezone(zone).replace(tzinfo=None) == wall]
    if valid:
        return valid[0]
    # The two imaginary-time offsets straddle the forward transition. Find its
    # first valid instant, rather than shifting 02:45 to 03:45 or assuming a
    # one-hour gap. UTC bisection also handles half-hour and whole-day jumps.
    before, after = candidates[0], candidates[-1]
    if not (before.astimezone(zone).replace(tzinfo=None) < wall
            < after.astimezone(zone).replace(tzinfo=None)):
        raise ValueError("Time-zone rules could not resolve this schedule occurrence")
    while after - before > _MICROSECOND:
        middle = before + (after - before) // 2
        if middle.astimezone(zone).replace(tzinfo=None) >= wall:
            after = middle
        else:
            before = middle
    return after if after.astimezone(zone).date() == local_date else None


def due_instant(local_date: date, zone: str, local_time: str) -> datetime | None:
    """Resolve one civil date; return None if no remaining instant exists on it."""
    return _resolve(_date(local_date), _zone(zone), _time(local_time))


def latest_due(now: datetime, *, start_date: date, zone: str, local_time: str) -> DailyOccurrence | None:
    """Return the latest occurrence at or before now, never before start_date."""
    clock, start, location, wall_time = _clock(now), _date(start_date), _zone(zone), _time(local_time)
    selected = min(_clock_date(clock, location), MAX_DATE)
    while selected >= start:
        instant = _resolve(selected, location, wall_time)
        if instant is not None and instant <= clock:
            return DailyOccurrence(selected, instant)
        selected -= _DAY
    return None


def next_due(now: datetime, *, start_date: date, zone: str, local_time: str) -> DailyOccurrence | None:
    """Return the first occurrence strictly after now, or None beyond 2100."""
    clock, start, location, wall_time = _clock(now), _date(start_date), _zone(zone), _time(local_time)
    selected = max(_clock_date(clock, location), start)
    while selected <= MAX_DATE:
        instant = _resolve(selected, location, wall_time)
        if instant is not None and instant > clock:
            return DailyOccurrence(selected, instant)
        selected += _DAY
    return None
