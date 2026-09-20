"""Pure bounded Netflix detail windows; cursors are sort hints, never URLs.

The saved-search service owns checkpoint ordering and authorization. A window
refreshes one newest entry and consumes at most six later entries. A changed
sitemap does not reset a still-useful keyset cursor; reaching its end does.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


NETFLIX_WINDOW_POLICY = "netflix_head1_tail6@1"
MAX_NETFLIX_INVENTORY = 10000


@dataclass(frozen=True, slots=True)
class NetflixCursor:
    lastmod: str
    external_id: str

    def __post_init__(self) -> None:
        if (type(self.lastmod) is not str or len(self.lastmod) != 32
            or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}\+00:00", self.lastmod) is None
            or type(self.external_id) is not str or re.fullmatch(r"[1-9][0-9]{0,19}", self.external_id) is None):
            raise ValueError("Netflix cursor requires a normalized timestamp and numeric identity")
        try:
            parsed = datetime.fromisoformat(self.lastmod)
            if parsed.astimezone(UTC).isoformat(timespec="microseconds") != self.lastmod:
                raise ValueError
        except ValueError:
            raise ValueError("Netflix cursor timestamp is invalid") from None

    def to_dict(self) -> dict[str, str]:
        return {"lastmod": self.lastmod, "external_id": self.external_id}


def validate_netflix_cursor(value: object) -> NetflixCursor | None:
    if value is None:
        return None
    if type(value) is not dict or set(value) != {"lastmod", "external_id"}:
        raise ValueError("Netflix cursor fields are invalid")
    return NetflixCursor(value["lastmod"], value["external_id"])


def _later(value: NetflixCursor, after: NetflixCursor) -> bool:
    return value.lastmod < after.lastmod or value.lastmod == after.lastmod and int(value.external_id) > int(after.external_id)


@dataclass(frozen=True, slots=True)
class NetflixWindowPlan:
    after: NetflixCursor | None
    head: NetflixCursor | None
    tail: tuple[NetflixCursor, ...]
    indexed_count: int
    reset: bool
    exhausted: bool


def plan_netflix_window(entries: tuple[NetflixCursor, ...], after: NetflixCursor | None) -> NetflixWindowPlan:
    if (type(entries) is not tuple or len(entries) > MAX_NETFLIX_INVENTORY
        or any(type(entry) is not NetflixCursor for entry in entries)
        or after is not None and type(after) is not NetflixCursor
        or len({entry.external_id for entry in entries}) != len(entries)):
        raise ValueError("Netflix window requires a bounded unique inventory and validated cursor")
    ordered = sorted(entries, key=lambda entry: int(entry.external_id))
    ordered.sort(key=lambda entry: entry.lastmod, reverse=True)
    head = ordered[0] if ordered else None
    tail = [entry for entry in ordered[1:] if after is None or _later(entry, after)]
    reset = after is not None and head is not None and not tail
    if reset:
        tail = ordered[1:]
    return NetflixWindowPlan(after, head, tuple(tail[:6]), len(ordered), reset, len(tail) <= 6)


@dataclass(frozen=True, slots=True)
class NetflixWindowProgress:
    after: NetflixCursor | None
    next_cursor: NetflixCursor | None
    indexed_count: int | None
    consumed_tail_count: int
    cycle_complete: bool
    reset: bool
    policy: str = NETFLIX_WINDOW_POLICY

    def to_dict(self) -> dict[str, Any]:
        return {"policy": self.policy, "after": None if self.after is None else self.after.to_dict(),
            "next": None if self.next_cursor is None else self.next_cursor.to_dict(),
            "indexed_count": self.indexed_count, "consumed_tail_count": self.consumed_tail_count,
            "cycle_complete": self.cycle_complete, "reset": self.reset}


def validate_netflix_window_progress(value: object) -> NetflixWindowProgress:
    fields = {"policy", "after", "next", "indexed_count", "consumed_tail_count", "cycle_complete", "reset"}
    if type(value) is not dict or set(value) != fields or value["policy"] != NETFLIX_WINDOW_POLICY:
        raise ValueError("Netflix window progress fields or policy are invalid")
    after, following = validate_netflix_cursor(value["after"]), validate_netflix_cursor(value["next"])
    indexed, consumed = value["indexed_count"], value["consumed_tail_count"]
    complete, reset = value["cycle_complete"], value["reset"]
    if (type(consumed) is not int or not 0 <= consumed <= 6
        or type(complete) is not bool or type(reset) is not bool
        or indexed is not None and (type(indexed) is not int or not 0 <= indexed <= MAX_NETFLIX_INVENTORY)):
        raise ValueError("Netflix window progress bounds are invalid")
    if indexed is None:
        if following != after or consumed or complete or reset:
            raise ValueError("An unread Netflix inventory cannot advance")
    else:
        if consumed > max(0, indexed - 1) or reset and (after is None or indexed == 0):
            raise ValueError("Netflix window progress is inconsistent with inventory")
        if complete:
            if (following is not None or indexed > 1 and consumed == 0
                or (after is None or reset) and (indexed > 7 or consumed != max(0, indexed - 1))):
                raise ValueError("Netflix completed window progress is inconsistent")
        elif indexed == 0 or consumed == 0 and following != after or consumed and following is None:
            raise ValueError("Netflix unfinished window progress is inconsistent")
        elif consumed and after is not None and not reset and not _later(following, after):
            raise ValueError("Netflix cursor did not advance in inventory order")
        elif consumed and reset and not _later(after, following):
            raise ValueError("Netflix reset cursor did not return to an earlier inventory position")
    return NetflixWindowProgress(after, following, indexed, consumed, complete, reset)


def finish_netflix_window(plan: NetflixWindowPlan, *, consumed_tail_count: int,
                          head_consumed: bool) -> NetflixWindowProgress:
    if (type(plan) is not NetflixWindowPlan or type(consumed_tail_count) is not int
        or not 0 <= consumed_tail_count <= len(plan.tail) or type(head_consumed) is not bool
        or consumed_tail_count and not head_consumed):
        raise ValueError("Netflix window consumption is invalid")
    complete = (plan.head is None or head_consumed) and plan.exhausted and consumed_tail_count == len(plan.tail)
    following = None if complete else plan.tail[consumed_tail_count - 1] if consumed_tail_count else plan.after
    return validate_netflix_window_progress(NetflixWindowProgress(plan.after, following,
        plan.indexed_count, consumed_tail_count, complete, plan.reset).to_dict())
