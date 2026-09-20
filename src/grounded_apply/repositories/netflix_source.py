"""Read Netflix's crawler-published sitemaps and inert JobPosting JSON-LD.

The fixed source uses at most ten requests: robots, sitemap index, job sitemap,
then seven advertised job pages. Standalone discovery samples newest entries;
saved-search windows refresh one newest entry and advance through six others.
Neither route crawls hidden APIs or claims full coverage on larger boards.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit
from xml.etree import ElementTree

from grounded_apply.repositories.job_sources import SourceResult, html_to_text
from grounded_apply.services.discovery import (
    MAX_SOURCE_RECORDS, MAX_SOURCE_REQUESTS, NETFLIX_NORMALIZER_VERSION,
    REQUEST_TIMEOUT, DiscoveredJob, DiscoveryErrorCode, SourceSpec, Transport,
    canonical_job_url, discovered_job_digest, validate_discovered_job,
    validate_source_spec, netflix_job_url_id,
)
from grounded_apply.services.source_windows import (
    NetflixCursor, NetflixWindowProgress, finish_netflix_window, plan_netflix_window,
)


HOST = "https://explore.jobs.netflix.net"
ROBOTS_URL = HOST + "/robots.txt"
SITEMAP_QUERY = "?domain=netflix.com&microsite=netflix.com"
INDEX_URL = HOST + "/careers/sitemap_index.xml" + SITEMAP_QUERY
JOBS_SITEMAP_URL = HOST + "/careers/sitemap.xml" + SITEMAP_QUERY
_CATEGORY_SITEMAP_URL = HOST + "/careers/sitemap_cat.xml" + SITEMAP_QUERY
_USER_AGENT = "groundedapply"
_MAX_ROBOTS_BYTES = 64 * 1024
_MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
_MAX_JSON_BYTES = 1024 * 1024
_NAMESPACE = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


class _SourceError(Exception):
    def __init__(self, code: DiscoveryErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True, slots=True)
class _Robots:
    rules: tuple[tuple[str, str], ...]
    sitemap_published: bool
    restricted_rate: bool

    def allows(self, url: str) -> bool:
        path = urlsplit(url).path
        if urlsplit(url).query:
            path += "?" + urlsplit(url).query
        path = _robots_octets(path)
        matches: list[tuple[int, bool]] = []
        for directive, rule in self.rules:
            if not rule:
                continue
            rule = _robots_octets(rule)
            if _rule_matches(rule, path):
                matches.append((len(rule.replace("*", "").removesuffix("$")), directive == "allow"))
        # RFC 9309 uses the longest match, with Allow winning equal specificity.
        return not self.restricted_rate and (not matches or max(matches)[1])


def _robots_octets(value: str) -> str:
    """Normalize UTF-8 and escaped unreserved bytes for RFC 9309 matching."""
    parts: list[str] = []
    position = 0
    while position < len(value):
        char = value[position]
        if char == "%":
            pair = value[position + 1:position + 3]
            if not re.fullmatch(r"[0-9A-Fa-f]{2}", pair):
                raise ValueError("Invalid robots escape")
            byte = int(pair, 16)
            decoded = chr(byte)
            parts.append(decoded if decoded.isascii() and (decoded.isalnum() or decoded in "-._~")
                         else "%" + pair.upper())
            position += 3
        else:
            parts.append(char if ord(char) < 128 else "".join(f"%{byte:02X}" for byte in char.encode("utf-8")))
            position += 1
    return "".join(parts)


def _rule_matches(rule: str, path: str) -> bool:
    """Match robots '*' without a remotely supplied backtracking regex."""
    pattern = rule[:-1] if rule.endswith("$") else rule + "*"
    pattern_index = path_index = 0
    star_index = retry_index = -1
    while path_index < len(path):
        if pattern_index < len(pattern) and pattern[pattern_index] == "*":
            star_index, retry_index = pattern_index, path_index
            pattern_index += 1
        elif pattern_index < len(pattern) and pattern[pattern_index] == path[path_index]:
            pattern_index += 1
            path_index += 1
        elif star_index >= 0:
            retry_index += 1
            path_index = retry_index
            pattern_index = star_index + 1
        else:
            return False
    return all(char == "*" for char in pattern[pattern_index:])


def _robots(payload: bytes) -> _Robots:
    text = payload.decode("utf-8-sig")
    if len(text.splitlines()) > 2000 or any(ord(c) < 32 and c not in "\n\r\t" for c in text):
        raise ValueError("Invalid robots policy")
    groups: list[tuple[list[str], list[tuple[str, str]], bool]] = []
    agents: list[str] = []
    rules: list[tuple[str, str]] = []
    restricted = False
    has_directive = False
    sitemap_published = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        key, separator, value = line.partition(":")
        if not separator:
            raise ValueError("Invalid robots policy")
        key, value = key.strip().lower(), value.strip()
        if key == "sitemap":
            sitemap_published |= value == INDEX_URL
            continue
        if key == "user-agent":
            if has_directive:
                groups.append((agents, rules, restricted))
                agents, rules, restricted, has_directive = [], [], False, False
            if not re.fullmatch(r"[A-Za-z0-9_*.-]{1,128}", value):
                raise ValueError("Invalid robots agent")
            agents.append(value.lower())
        elif key in {"allow", "disallow"}:
            if not agents or len(value) > 2048 or value and not value.startswith("/"):
                raise ValueError("Invalid robots rule")
            if "$" in value.removesuffix("$") or any(ord(c) <= 32 for c in value):
                raise ValueError("Invalid robots rule")
            _robots_octets(value)
            rules.append((key, value))
            has_directive = True
        elif key in {"crawl-delay", "request-rate"}:
            # This adapter does not implement a pacing scheduler. Do not ignore
            # a source's explicit rate instruction or reinterpret it as consent.
            restricted = True
            has_directive = True
    if agents:
        groups.append((agents, rules, restricted))
    if not groups:
        raise ValueError("Missing robots groups")
    selected: list[tuple[int, list[tuple[str, str]], bool]] = []
    for names, group_rules, group_restricted in groups:
        specificity = max((len(name) for name in names if name != "*" and name in _USER_AGENT), default=-1)
        if specificity < 0 and "*" in names:
            specificity = 0
        if specificity >= 0:
            selected.append((specificity, group_rules, group_restricted))
    best = max((item[0] for item in selected), default=-1)
    merged = tuple(rule for score, group_rules, _ in selected if score == best for rule in group_rules)
    return _Robots(merged, sitemap_published,
                   any(limited for score, _, limited in selected if score == best))


def _xml(payload: bytes, root_name: str) -> ElementTree.Element:
    # ElementTree does not retrieve external entities. Reject all declarations
    # before parsing as well, including UTF-16 encodings and internal expansion.
    text = payload.decode("utf-8-sig")
    if "<!doctype" in text.lower() or "<!entity" in text.lower() or "\x00" in text:
        raise ValueError("XML declarations are not supported")
    root = ElementTree.fromstring(text)
    if root.tag != _NAMESPACE + root_name:
        raise ValueError("Invalid sitemap root")
    return root


def _fields(entry: ElementTree.Element, expected: str, allowed: frozenset[str]) -> dict[str, str]:
    if entry.tag != _NAMESPACE + expected:
        raise ValueError("Invalid sitemap entry")
    result: dict[str, str] = {}
    for child in entry:
        name = child.tag.removeprefix(_NAMESPACE)
        if child.tag != _NAMESPACE + name or name not in allowed or name in result or len(child):
            raise ValueError("Invalid sitemap field")
        value = child.text or ""
        if len(value) > 2048:
            raise ValueError("Sitemap field too long")
        result[name] = value.strip()
    if not result.get("loc"):
        raise ValueError("Missing sitemap location")
    return result


def _check_index(payload: bytes) -> None:
    root = _xml(payload, "sitemapindex")
    if not 1 <= len(root) <= 16:
        raise ValueError("Invalid sitemap index size")
    links: set[str] = set()
    for entry in root:
        fields = _fields(entry, "sitemap", frozenset({"loc", "lastmod"}))
        url = fields["loc"]
        if url not in {JOBS_SITEMAP_URL, _CATEGORY_SITEMAP_URL} or url in links:
            raise ValueError("Unrecognized sitemap route")
        links.add(url)
    if JOBS_SITEMAP_URL not in links:
        raise ValueError("Job sitemap was not published")


def _identifier(url: object) -> str:
    return netflix_job_url_id(url)


def _modified(value: str) -> datetime:
    if not value or len(value) > 40:
        raise ValueError("Missing or invalid sitemap lastmod")
    at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    # Date-only / timezone-less source values use UTC solely as a stable sort
    # key. We do not assert a factual timezone for the posting's displayed date.
    return at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class _Entry:
    identifier: str
    url: str
    modified: datetime


def _entries(payload: bytes) -> tuple[tuple[_Entry, ...], bool]:
    root = _xml(payload, "urlset")
    if len(root) > MAX_SOURCE_RECORDS + 1:
        raise _SourceError(DiscoveryErrorCode.SOURCE_LIMIT_REACHED)
    entries: dict[str, _Entry] = {}
    conflicts: set[str] = set()
    invalid = False
    for item in root:
        try:
            values = _fields(item, "url", frozenset({"loc", "lastmod", "priority", "changefreq"}))
            url = values["loc"]
            if url in {HOST + "/careers", HOST + "/careers?domain=netflix.com"}:
                continue
            identifier = _identifier(url)
            entry = _Entry(identifier, url, _modified(values.get("lastmod", "")))
            if identifier in entries and entries[identifier] != entry:
                conflicts.add(identifier)
                invalid = True
            else:
                entries[identifier] = entry
        except (ValueError, TypeError):
            invalid = True
    for identifier in conflicts:
        entries.pop(identifier, None)
    # Stable ID order breaks equal lastmod ties without assuming sitemap order.
    ordered = sorted(entries.values(), key=lambda item: int(item.identifier))
    ordered.sort(key=lambda item: item.modified, reverse=True)
    return tuple(ordered), invalid


class _StructuredData(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.blocks: list[str] = []
        self._active = False
        self._parts: list[str] = []
        self._size = 0
        self.restricted = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "script" and (values.get("type") or "").lower() == "application/ld+json":
            if self._active or len(self.blocks) >= 8:
                raise ValueError("Too many structured blocks")
            self._active = True
            self._parts = []
        if tag == "meta" and (values.get("name") or "").lower() in {"robots", _USER_AGENT}:
            directives = re.split(r"[\s,]+", (values.get("content") or "").lower())
            if set(directives) & {"none", "noindex", "nofollow", "noarchive", "nosnippet", "noai"}:
                self.restricted = True

    def handle_data(self, data: str) -> None:
        if self._active:
            self._size += len(data.encode("utf-8"))
            if self._size > _MAX_JSON_BYTES:
                raise ValueError("Structured data too large")
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._active:
            self.blocks.append("".join(self._parts))
            self._active = False


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate structured key")
        result[key] = value
    return result


def _constant(value: str) -> object:
    raise ValueError("Invalid structured number")


def _line(value: object, maximum: int = 512) -> str:
    if (type(value) is not str or not value.strip() or len(value.encode("utf-8")) > maximum
        or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError("Invalid source field")
    return value.strip()


def _location(value: object) -> str | None:
    if value is None:
        return None
    items = value if type(value) is list else [value]
    if not 1 <= len(items) <= 50:
        raise ValueError("Invalid locations")
    locations = []
    for item in items:
        if type(item) is not dict or item.get("@type") != "Place" or type(item.get("address")) is not dict:
            raise ValueError("Invalid location")
        address = item["address"]
        country = address.get("addressCountry")
        if type(country) is dict and country.get("@type") == "Country":
            country = country.get("name")
        parts = [address.get("addressLocality"), address.get("addressRegion"), country]
        cleaned = [_line(part) for part in parts if part not in (None, "")]
        if not cleaned:
            raise ValueError("Missing location wording")
        locations.append(", ".join(cleaned))
    return _line("; ".join(dict.fromkeys(locations)), 2048)


def _posting(payload: bytes, entry: _Entry, source: SourceSpec) -> DiscoveredJob:
    parser = _StructuredData()
    parser.feed(payload.decode("utf-8"))
    parser.close()
    if parser.restricted:
        raise _SourceError(DiscoveryErrorCode.ROBOTS_DISALLOWED)
    if parser._active:
        raise ValueError("Unterminated structured data")
    postings: list[dict[str, object]] = []
    for raw in parser.blocks:
        document = json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)
        values = document if type(document) is list else [document]
        if len(values) > 64:
            raise ValueError("Too many structured objects")
        for value in values:
            if type(value) is not dict:
                raise ValueError("Invalid structured object")
            if value.get("@type") == "JobPosting":
                postings.append(value)
    if len(postings) != 1:
        raise ValueError("Expected exactly one posting")
    posting = postings[0]
    if _identifier(posting.get("url")) != entry.identifier:
        raise ValueError("Posting identity does not match sitemap")
    organization = posting.get("hiringOrganization")
    if type(organization) is not dict or organization.get("name") != "Netflix":
        raise ValueError("Unexpected hiring organization")
    title = _line(posting.get("title"))
    location = _location(posting.get("jobLocation"))
    description = html_to_text(posting.get("description"))
    parts = [title, location]
    for key, label in (("datePosted", "Date posted"), ("validThrough", "Valid through"),
                       ("employmentType", "Employment type")):
        if key in posting:
            parts.append(label + ": " + _line(posting[key]))
    parts.append(description)
    job = DiscoveredJob("netflix", "netflix", entry.identifier,
        canonical_job_url("netflix", "netflix", entry.identifier), title, location,
        "\n\n".join(part for part in parts if part is not None), "", NETFLIX_NORMALIZER_VERSION)
    job = replace(job, content_sha256=discovered_job_digest(job))
    validate_discovered_job(job)
    return job


class _Reader:
    def __init__(self, transport: Transport) -> None:
        self.transport = transport
        self.requests = 0

    def fetch(self, url: str, maximum: int = _MAX_DOCUMENT_BYTES) -> bytes:
        if self.requests >= MAX_SOURCE_REQUESTS:
            raise _SourceError(DiscoveryErrorCode.SOURCE_LIMIT_REACHED)
        self.requests += 1
        try:
            body = self.transport.get(url, max_bytes=maximum, timeout=REQUEST_TIMEOUT)
        except Exception as exc:
            try:
                code = DiscoveryErrorCode(getattr(exc, "code", "transport_failure"))
            except (ValueError, TypeError):
                code = DiscoveryErrorCode.TRANSPORT_FAILURE
            raise _SourceError(code) from None
        if type(body) is not bytes:
            raise _SourceError(DiscoveryErrorCode.INVALID_RESPONSE)
        if len(body) > maximum:
            raise _SourceError(DiscoveryErrorCode.RESPONSE_TOO_LARGE)
        return body


def discover_netflix(source: SourceSpec, transport: Transport, *, max_records: int) -> SourceResult:
    validate_source_spec(source)
    if source.provider != "netflix" or type(max_records) is not int or not 1 <= max_records <= MAX_SOURCE_RECORDS:
        raise ValueError("Netflix discovery requires a bounded fixed source")
    errors: list[DiscoveryErrorCode] = []
    jobs: list[DiscoveredJob] = []
    reader = _Reader(transport)
    fetch = reader.fetch

    def record_error(code: DiscoveryErrorCode) -> None:
        if code not in errors:
            errors.append(code)

    try:
        policy = _robots(fetch(ROBOTS_URL, _MAX_ROBOTS_BYTES))
        if not policy.sitemap_published or not policy.allows(INDEX_URL) or not policy.allows(JOBS_SITEMAP_URL):
            raise _SourceError(DiscoveryErrorCode.ROBOTS_DISALLOWED)
        _check_index(fetch(INDEX_URL))
        entries, invalid = _entries(fetch(JOBS_SITEMAP_URL))
    except _SourceError as exc:
        return SourceResult((), 0, (exc.code,))
    except (ValueError, TypeError, UnicodeError, ElementTree.ParseError, RecursionError):
        return SourceResult((), 0, (DiscoveryErrorCode.INVALID_PAYLOAD,))
    if invalid:
        record_error(DiscoveryErrorCode.INVALID_RECORD)
    observed = 0
    for entry in entries:
        if observed >= max_records or reader.requests >= MAX_SOURCE_REQUESTS:
            record_error(DiscoveryErrorCode.SOURCE_LIMIT_REACHED)
            break
        if not policy.allows(entry.url):
            record_error(DiscoveryErrorCode.ROBOTS_DISALLOWED)
            break
        observed += 1
        try:
            jobs.append(_posting(fetch(entry.url), entry, source))
        except _SourceError as exc:
            record_error(exc.code)
            if exc.code == DiscoveryErrorCode.SOURCE_LIMIT_REACHED:
                # The local or parent request budget refused the GET before
                # network dispatch, so this detail was not observed.
                observed -= 1
                break
            if exc.code in {DiscoveryErrorCode.FORBIDDEN, DiscoveryErrorCode.RATE_LIMITED,
                            DiscoveryErrorCode.ROBOTS_DISALLOWED, DiscoveryErrorCode.REDIRECT_REFUSED}:
                break
        except (ValueError, TypeError, UnicodeError, RecursionError, AssertionError):
            record_error(DiscoveryErrorCode.INVALID_RECORD)
    return SourceResult(tuple(jobs), observed, tuple(errors), len(entries), len(entries) - observed)


@dataclass(frozen=True, slots=True)
class NetflixWindowSourceResult:
    result: SourceResult
    progress: NetflixWindowProgress


def discover_netflix_window(source: SourceSpec, transport: Transport, *,
                            after: NetflixCursor | None) -> NetflixWindowSourceResult:
    """Read one fresh head and six tail details without changing legacy reads."""
    validate_source_spec(source)
    if source.provider != "netflix" or after is not None and type(after) is not NetflixCursor:
        raise ValueError("Netflix window requires a fixed source and validated cursor")
    reader = _Reader(transport)
    unchanged = NetflixWindowProgress(after, after, None, 0, False, False)
    try:
        policy = _robots(reader.fetch(ROBOTS_URL, _MAX_ROBOTS_BYTES))
        if not policy.sitemap_published or not policy.allows(INDEX_URL) or not policy.allows(JOBS_SITEMAP_URL):
            raise _SourceError(DiscoveryErrorCode.ROBOTS_DISALLOWED)
        _check_index(reader.fetch(INDEX_URL))
        entries, invalid = _entries(reader.fetch(JOBS_SITEMAP_URL))
        if len(entries) > MAX_SOURCE_RECORDS:
            raise _SourceError(DiscoveryErrorCode.SOURCE_LIMIT_REACHED)
        keys = tuple(NetflixCursor(entry.modified.isoformat(timespec="microseconds"), entry.identifier) for entry in entries)
        plan = plan_netflix_window(keys, after)
    except _SourceError as exc:
        return NetflixWindowSourceResult(SourceResult((), 0, (exc.code,)), unchanged)
    except (ValueError, TypeError, UnicodeError, ElementTree.ParseError, RecursionError):
        return NetflixWindowSourceResult(SourceResult((), 0, (DiscoveryErrorCode.INVALID_PAYLOAD,)), unchanged)
    by_id = {entry.identifier: entry for entry in entries}
    errors = [DiscoveryErrorCode.INVALID_RECORD] if invalid else []
    jobs: list[DiscoveredJob] = []
    observed = consumed = 0
    head_consumed = False
    stopped = False

    def record_error(code: DiscoveryErrorCode) -> None:
        if code not in errors:
            errors.append(code)

    selected = () if plan.head is None else (plan.head, *plan.tail)
    for position, key in enumerate(selected):
        entry = by_id[key.external_id]
        if not policy.allows(entry.url):
            record_error(DiscoveryErrorCode.ROBOTS_DISALLOWED)
            stopped = True
            break
        observed += 1
        try:
            jobs.append(_posting(reader.fetch(entry.url), entry, source))
        except _SourceError as exc:
            record_error(exc.code)
            if exc.code == DiscoveryErrorCode.SOURCE_LIMIT_REACHED:
                observed -= 1
            # Isolated detail failures consume this bounded attempt and are
            # revisited next cycle. Restrictions and parent-budget refusals
            # stop here; progress must never bypass the refused entry.
            if exc.code in {DiscoveryErrorCode.FORBIDDEN, DiscoveryErrorCode.RATE_LIMITED,
                            DiscoveryErrorCode.ROBOTS_DISALLOWED, DiscoveryErrorCode.REDIRECT_REFUSED,
                            DiscoveryErrorCode.SOURCE_LIMIT_REACHED}:
                stopped = True
                break
        except (ValueError, TypeError, UnicodeError, RecursionError, AssertionError):
            record_error(DiscoveryErrorCode.INVALID_RECORD)
        if position == 0:
            head_consumed = True
        else:
            consumed += 1
    if not stopped and observed < len(entries):
        record_error(DiscoveryErrorCode.SOURCE_LIMIT_REACHED)
    progress = finish_netflix_window(plan, consumed_tail_count=consumed, head_consumed=head_consumed)
    return NetflixWindowSourceResult(SourceResult(tuple(jobs), observed, tuple(errors), len(entries),
        len(entries) - observed), progress)
