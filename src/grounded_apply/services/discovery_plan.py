"""Plan bounded public-board searches from explicit, literal job preferences.

This module performs no search and reads no profile, provider, or runtime state.
Terms remain data: neither a query language nor search-engine URLs are generated.
The caller must use an authorized search tool and verify actual result links
before configuring sources. A plan establishes no posting or coverage evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from unicodedata import category


MAX_ROLE_INPUTS = 3
MAX_LOCATION_INPUTS = 2
MAX_TERM_CODEPOINTS = 128
MAX_PUBLIC_SEARCH_QUERIES = 9
MAX_PUBLIC_SEARCH_LINKS = 18
PUBLIC_SEARCH_DOMAINS = (
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "jobs.ashbyhq.com",
    "jobs.lever.co",
    "jobs.eu.lever.co",
    "apply.workable.com",
    "explore.jobs.netflix.net",
)


def _term(value: str) -> str:
    if type(value) is not str:
        raise ValueError("Public search terms must be text")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValueError("Public search terms must contain valid Unicode") from None
    # Inspect before trimming: a leading newline or tab is still a control,
    # rather than permission to silently turn multiline input into a query.
    if any(category(character) in {"Cc", "Zl", "Zp"} for character in value):
        raise ValueError("Public search terms must be single-line text without controls")
    result = value.strip()
    if not result or len(result) > MAX_TERM_CODEPOINTS:
        raise ValueError("Public search terms require one to 128 code points after trimming")
    return result


def _terms(values: tuple[str, ...], *, minimum: int, maximum: int) -> tuple[str, ...]:
    if type(values) is not tuple or not minimum <= len(values) <= maximum:
        raise ValueError("Public search planning requires one to three role inputs and zero to two location inputs")
    # Exact deduplication preserves first occurrence, case, punctuation, internal
    # whitespace and Unicode spelling. Roles are not interpreted as claim types.
    return tuple(dict.fromkeys(_term(value) for value in values))


@dataclass(frozen=True, slots=True)
class PublicSearchQuery:
    """One literal role, optionally with a literal location, on fixed domains."""

    position: int
    terms: tuple[str, ...]
    domains: tuple[str, ...] = field(default=PUBLIC_SEARCH_DOMAINS, init=False)

    def __post_init__(self) -> None:
        if type(self.position) is not int or not 1 <= self.position <= MAX_PUBLIC_SEARCH_QUERIES:
            raise ValueError("Public search position must be an integer from one to nine")
        if type(self.terms) is not tuple or not 1 <= len(self.terms) <= 2:
            raise ValueError("Public search rows require one role and at most one location")
        if any(_term(term) != term for term in self.terms):
            raise ValueError("Public search row terms must already be trimmed")


@dataclass(frozen=True, slots=True)
class PublicSearchPlan:
    """An immutable plan; execution budgets do not imply searches were run."""

    roles: tuple[str, ...]
    locations: tuple[str, ...] = ()
    schema_version: int = field(default=1, init=False)
    searches: tuple[PublicSearchQuery, ...] = field(init=False)
    query_count: int = field(init=False)
    max_queries: int = field(default=MAX_PUBLIC_SEARCH_QUERIES, init=False)
    max_distinct_links: int = field(default=MAX_PUBLIC_SEARCH_LINKS, init=False)
    network_requests: int = field(default=0, init=False)
    storage_changed: bool = field(default=False, init=False)
    profile_read: bool = field(default=False, init=False)
    coverage_established: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        roles = _terms(self.roles, minimum=1, maximum=MAX_ROLE_INPUTS)
        locations = _terms(self.locations, minimum=0, maximum=MAX_LOCATION_INPUTS)
        rows: list[PublicSearchQuery] = []
        for role in roles:
            rows.append(PublicSearchQuery(len(rows) + 1, (role,)))
            for location in locations:
                rows.append(PublicSearchQuery(len(rows) + 1, (role, location)))
        object.__setattr__(self, "roles", roles)
        object.__setattr__(self, "locations", locations)
        object.__setattr__(self, "searches", tuple(rows))
        object.__setattr__(self, "query_count", len(rows))


def build_public_search_plan(
    *, roles: tuple[str, ...], locations: tuple[str, ...] = (),
) -> PublicSearchPlan:
    """Return at most nine searches without opening storage or contacting sites.

    Input limits apply before exact deduplication; nothing is silently truncated.
    Each role retains a location-free search because indexed location text is
    incomplete. Remote and eligibility-related terms remain literal job intent,
    never evidence about the candidate or worldwide eligibility.
    """

    return PublicSearchPlan(roles=roles, locations=locations)
