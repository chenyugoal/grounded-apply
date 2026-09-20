"""Explicit text preferences for package selection, never eligibility inference.

The caller supplies validated posting metadata and applies this policy before
its preparation quota. Matching a location string does not establish residence,
work authorization, workplace type, or availability from another location.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Literal


PreparationFilterReason = Literal[
    "title_excluded", "location_excluded", "location_not_matched", "location_unknown_excluded",
]
MissingLocationPolicy = Literal["include", "exclude"]
_TERM_FIELDS = ("title_excludes", "location_contains", "location_excludes")
_FIELDS = frozenset((*_TERM_FIELDS, "missing_location"))


def _validate_terms(values: tuple[str, ...]) -> None:
    if (type(values) is not tuple or len(values) > 20
        or any(type(term) is not str or not 1 <= len(term) <= 128 or term != term.strip()
               or any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in term)
               for term in values)):
        raise ValueError("Preparation filters require at most twenty trimmed terms of one to 128 characters without controls")


def _validate_source_text(value: str, maximum: int) -> None:
    try:
        valid = (type(value) is str and bool(value.strip()) and len(value.encode("utf-8")) <= maximum
                 and not any(ord(char) < 32 or ord(char) == 127 for char in value))
    except UnicodeError:
        valid = False
    if not valid:
        raise ValueError("Preparation filters require bounded nonblank source metadata")


@dataclass(frozen=True, slots=True)
class PreparationFilters:
    title_excludes: tuple[str, ...] = ()
    location_contains: tuple[str, ...] = ()
    location_excludes: tuple[str, ...] = ()
    missing_location: MissingLocationPolicy = "include"

    def __post_init__(self) -> None:
        for values in (self.title_excludes, self.location_contains, self.location_excludes):
            _validate_terms(values)
        if type(self.missing_location) is not str or self.missing_location not in {"include", "exclude"}:
            raise ValueError("Missing location policy must be include or exclude")

    def to_dict(self) -> dict[str, list[str] | str]:
        """Return a detached canonical shape for the immutable saved scope."""
        return {"title_excludes": list(self.title_excludes), "location_contains": list(self.location_contains),
            "location_excludes": list(self.location_excludes), "missing_location": self.missing_location}

    def assess(self, *, title: str, location: str | None) -> PreparationFilterReason | None:
        """Return a fixed exclusion reason, or None to keep this text match.

        None is a selection result, not a statement of geographic or candidate
        eligibility. Unknown location follows the user's explicit policy.
        """
        _validate_source_text(title, 512)
        if location is not None:
            _validate_source_text(location, 2048)
        folded_title = title.casefold()
        if any(term.casefold() in folded_title for term in self.title_excludes):
            return "title_excluded"
        if location is None:
            return "location_unknown_excluded" if self.missing_location == "exclude" else None
        folded = location.casefold()
        if any(term.casefold() in folded for term in self.location_excludes):
            return "location_excluded"
        if self.location_contains and not any(term.casefold() in folded for term in self.location_contains):
            return "location_not_matched"
        return None


def validate_preparation_filters(value: object) -> PreparationFilters:
    """Parse closed JSON preferences, defaulting unknown locations to include."""
    if type(value) is not dict or set(value) - _FIELDS:
        raise ValueError("Preparation filter fields are invalid")
    terms: dict[str, tuple[str, ...]] = {}
    for key in _TERM_FIELDS:
        entries = value.get(key, [])
        if type(entries) is not list:
            raise ValueError("Preparation filter terms must be lists")
        terms[key] = tuple(entries)
    return PreparationFilters(**terms, missing_location=value.get("missing_location", "include"))
