"""Bounded public-board discovery without candidate data or persistence."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Literal, Protocol
from urllib.parse import unquote, urlsplit

from grounded_apply.services.source_windows import NetflixCursor, NetflixWindowProgress


Provider = Literal["greenhouse", "ashby", "lever", "lever_eu", "netflix", "manual"]
SourceStatus = Literal["successful", "partial", "failed", "manual_required"]
NORMALIZER_VERSION = "public_ats_text@1"
NETFLIX_NORMALIZER_VERSION = "netflix_jobposting@1"
MAX_SOURCES = 32
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_SOURCE_RECORDS = 10000
MAX_SELECTED_JOBS = 1000
DEFAULT_SELECTED_JOBS = 100
MAX_SOURCE_REQUESTS = 10
REQUEST_TIMEOUT = 10.0
_PROVIDERS = frozenset({"greenhouse", "ashby", "lever", "lever_eu", "netflix", "manual"})
_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_OPAQUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")
_NETFLIX_JOB_URL = re.compile(
    r"https://explore\.jobs\.netflix\.net/careers/job/([1-9][0-9]{0,19})"
    r"(?:-((?:[A-Za-z0-9_-]|%[0-9A-Fa-f]{2}){1,512}))?"
    r"(?:\?domain=netflix\.com&microsite=netflix\.com)?\Z"
)


class DiscoveryErrorCode(StrEnum):
    TRANSPORT_FAILURE = "transport_failure"
    TIMEOUT = "timeout"
    RESPONSE_TOO_LARGE = "response_too_large"
    RATE_LIMITED = "rate_limited"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    REDIRECT_REFUSED = "redirect_refused"
    INVALID_RESPONSE = "invalid_response"
    INVALID_PAYLOAD = "invalid_payload"
    INVALID_RECORD = "invalid_record"
    CONFLICTING_DUPLICATE = "conflicting_duplicate"
    SOURCE_LIMIT_REACHED = "source_limit_reached"
    ROBOTS_DISALLOWED = "robots_disallowed"


class Transport(Protocol):
    def get(self, url: str, *, max_bytes: int, timeout: float) -> bytes: ...


@dataclass(frozen=True, slots=True)
class SourceSpec:
    id: str
    provider: Provider
    board: str | None = None
    careers_url: str | None = None


@dataclass(frozen=True, slots=True)
class DiscoveredJob:
    provider: Provider
    board: str
    external_id: str
    source_url: str
    title: str
    location: str | None
    source_text: str
    content_sha256: str
    normalizer_version: str = NORMALIZER_VERSION


@dataclass(frozen=True, slots=True)
class SourceReport:
    source_id: str
    provider: Provider
    board: str | None
    careers_url: str | None
    status: SourceStatus
    count: int
    filtered_count: int
    observed_count: int
    error: DiscoveryErrorCode | None
    fetched_at: str
    errors: tuple[DiscoveryErrorCode, ...] = ()
    indexed_count: int | None = None
    remaining_count: int | None = None


@dataclass(frozen=True, slots=True)
class DiscoveryReport:
    jobs: tuple[DiscoveredJob, ...]
    sources: tuple[SourceReport, ...]
    filter_method: str = "title_substring_or@1"


@dataclass(frozen=True, slots=True)
class NetflixWindowDiscovery:
    report: DiscoveryReport
    progress: NetflixWindowProgress


def _safe_https_url(value: object) -> bool:
    if (type(value) is not str or not value or len(value) > 2048
        or any(ord(char) <= 32 or ord(char) == 127 for char in value)):
        return False
    try:
        parsed = urlsplit(value)
        return (parsed.scheme == "https" and bool(parsed.hostname)
                and parsed.username is None and parsed.password is None
                and parsed.port is None and not parsed.query and not parsed.fragment
                and "\\" not in value)
    except ValueError:
        return False


def validate_source_spec(source: SourceSpec) -> None:
    if (type(source) is not SourceSpec or type(source.id) is not str
        or _OPAQUE.fullmatch(source.id) is None or type(source.provider) is not str
        or source.provider not in _PROVIDERS):
        raise ValueError("Source requires a supported provider and opaque identifier")
    if source.provider == "manual":
        if source.board is not None or not _safe_https_url(source.careers_url):
            raise ValueError("Manual source requires a bounded HTTPS careers URL")
    elif (type(source.board) is not str or _SEGMENT.fullmatch(source.board) is None
          or source.careers_url is not None):
        raise ValueError("ATS source requires a bounded board name")
    elif source.provider == "netflix" and source.board != "netflix":
        raise ValueError("Netflix source requires its fixed public board")


def validate_source_manifest(data: object, *, max_sources: int = MAX_SOURCES) -> tuple[SourceSpec, ...]:
    """Validate a closed manifest; arbitrary URLs never become fetch targets."""
    if type(max_sources) is not int or not 1 <= max_sources <= MAX_SOURCES:
        raise ValueError("Source limit must be from one to thirty-two")
    if (type(data) is not dict or set(data) != {"schema_version", "sources"}
        or type(data["schema_version"]) is not int or data["schema_version"] != 1
        or type(data["sources"]) is not list or not 1 <= len(data["sources"]) <= max_sources):
        raise ValueError("Sources require schema_version 1 and a bounded nonempty sources list")
    result = []
    ids: set[str] = set()
    identities: set[tuple[str, str | None, str | None]] = set()
    for item in data["sources"]:
        if type(item) is not dict:
            raise ValueError("Source entries must be objects")
        fields = {"id", "provider", "careers_url"} if item.get("provider") == "manual" else {"id", "provider", "board"}
        if set(item) != fields:
            raise ValueError("Source fields do not match its provider")
        source = SourceSpec(**item)
        validate_source_spec(source)
        identity = (source.provider, source.board, source.careers_url)
        if source.id in ids or identity in identities:
            raise ValueError("Source identifiers and board routes must be distinct")
        ids.add(source.id)
        identities.add(identity)
        result.append(source)
    return tuple(result)


def builtin_sources(name: str) -> tuple[SourceSpec, ...]:
    """Public route metadata, not a stored user watchlist or market-wide feed."""
    if name != "major-tech":
        raise ValueError("Unknown source preset")
    return (
        SourceSpec("anthropic", "greenhouse", "anthropic"),
        SourceSpec("openai", "ashby", "openai"),
        SourceSpec("google", "manual", careers_url="https://www.google.com/about/careers/applications/jobs/results/"),
        SourceSpec("apple", "manual", careers_url="https://jobs.apple.com/en-us/search"),
        SourceSpec("amazon", "manual", careers_url="https://www.amazon.jobs/en/search"),
        SourceSpec("netflix", "netflix", "netflix"),
        SourceSpec("meta", "manual", careers_url="https://www.metacareers.com/"),
    )


def canonical_job_url(provider: Provider, board: str, external_id: str) -> str:
    if (type(provider) is not str or provider not in _PROVIDERS - {"manual"}
        or type(board) is not str or _SEGMENT.fullmatch(board) is None
        or type(external_id) is not str or _SEGMENT.fullmatch(external_id) is None):
        raise ValueError("Invalid posting identity")
    if provider == "greenhouse":
        if not re.fullmatch(r"[1-9][0-9]{0,19}", external_id):
            raise ValueError("Invalid Greenhouse posting identity")
        return f"https://boards.greenhouse.io/{board}/jobs/{external_id}"
    if provider == "netflix":
        if board != "netflix" or not re.fullmatch(r"[1-9][0-9]{0,19}", external_id):
            raise ValueError("Invalid Netflix posting identity")
        return f"https://explore.jobs.netflix.net/careers/job/{external_id}"
    host = {"ashby": "jobs.ashbyhq.com", "lever": "jobs.lever.co", "lever_eu": "jobs.eu.lever.co"}[provider]
    return f"https://{host}/{board}/{external_id}"


def netflix_job_url_id(url: object) -> str:
    """Validate an advertised public route, including UTF-8 title slugs.

    Encoded ASCII, separators, controls, and recursive percent encoding are
    refused. Only letters, numbers, combining marks, and dashes can occur in
    decoded slugs; the fixed positive numeric identifier determines identity.
    """
    if type(url) is not str or len(url) > 2048:
        raise ValueError("Invalid Netflix job URL")
    match = _NETFLIX_JOB_URL.fullmatch(url)
    if match is None:
        raise ValueError("Invalid Netflix job URL")
    slug = match.group(2) or ""
    if any(int(value, 16) < 128 for value in re.findall(r"%([0-9A-Fa-f]{2})", slug)):
        raise ValueError("Invalid Netflix job URL")
    decoded = unquote(slug, encoding="utf-8", errors="strict")
    if any(not (char.isalnum() or char in "_-" or unicodedata.category(char) in {"Mn", "Mc", "Me", "Pd"})
           for char in decoded):
        raise ValueError("Invalid Netflix job URL")
    return match.group(1)


def discovered_job_digest(job: DiscoveredJob) -> str:
    """Hash one normalized job only; unrelated board changes cannot churn it."""
    payload = {name: getattr(job, name) for name in (
        "provider", "board", "external_id", "source_url", "title", "location", "source_text", "normalizer_version")}
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def validate_discovered_job(job: DiscoveredJob) -> None:
    if type(job) is not DiscoveredJob:
        raise ValueError("Expected a typed discovered job")
    expected_normalizer = NETFLIX_NORMALIZER_VERSION if job.provider == "netflix" else NORMALIZER_VERSION
    if (type(job.normalizer_version) is not str or job.normalizer_version != expected_normalizer
        or type(job.source_url) is not str
        or job.source_url != canonical_job_url(job.provider, job.board, job.external_id)):
        raise ValueError("Discovered job identity or normalizer is invalid")
    for value, maximum in ((job.title, 512), (job.source_text, 1024 * 1024)):
        if (type(value) is not str or not value.strip() or len(value.encode("utf-8")) > maximum
            or any(ord(char) < 32 and char not in "\n\t" or ord(char) == 127 for char in value)):
            raise ValueError("Discovered job text is invalid")
    if "\n" in job.title or "\t" in job.title:
        raise ValueError("Discovered job title must be one line")
    if job.location is not None and (type(job.location) is not str or not job.location.strip()
        or len(job.location.encode("utf-8")) > 2048 or any(ord(char) < 32 or ord(char) == 127 for char in job.location)):
        raise ValueError("Discovered job location is invalid")
    if (type(job.content_sha256) is not str or not re.fullmatch(r"[0-9a-f]{64}", job.content_sha256)
        or job.content_sha256 != discovered_job_digest(job)):
        raise ValueError("Discovered job content hash is invalid")


class DiscoveryService:
    def __init__(self, transport: Transport, *, max_sources: int = MAX_SOURCES) -> None:
        if type(max_sources) is not int or not 1 <= max_sources <= MAX_SOURCES:
            raise ValueError("Source limit must be from one to thirty-two")
        self._transport = transport
        self._max_sources = max_sources

    def discover_netflix_window(self, source: SourceSpec, *, after: NetflixCursor | None,
                                title_contains: tuple[str, ...] = (),
                                limit_per_source: int = MAX_SELECTED_JOBS) -> NetflixWindowDiscovery:
        """One saved-search window; legacy public discovery stays stateless."""
        from grounded_apply.repositories.netflix_source import discover_netflix_window

        validate_source_spec(source)
        if (source.provider != "netflix" or after is not None and type(after) is not NetflixCursor
            or type(limit_per_source) is not int or not 1 <= limit_per_source <= MAX_SELECTED_JOBS):
            raise ValueError("Netflix window requires a fixed source, cursor and bounded record limit")
        if (type(title_contains) is not tuple or len(title_contains) > 20
            or any(type(term) is not str or not term.strip() or term != term.strip()
                   or len(term) > 128 or any(ord(char) < 32 or ord(char) == 127 for char in term)
                   for term in title_contains)):
            raise ValueError("Title filters require at most twenty bounded nonblank terms")
        fetched_at = datetime.now(UTC).isoformat()
        window = discover_netflix_window(source, self._transport, after=after)
        result = window.result
        filters = tuple(term.casefold() for term in title_contains)
        matching = tuple(job for job in result.jobs if not filters or any(term in job.title.casefold() for term in filters))
        selected = matching[:limit_per_source]
        errors = result.errors
        if len(matching) > limit_per_source and DiscoveryErrorCode.SOURCE_LIMIT_REACHED not in errors:
            errors += (DiscoveryErrorCode.SOURCE_LIMIT_REACHED,)
        status: SourceStatus = "partial" if errors and result.jobs else "failed" if errors else "successful"
        report = SourceReport(source.id, source.provider, source.board, None, status,
            len(selected), len(result.jobs) - len(matching), result.observed_count,
            errors[0] if errors else None, fetched_at, errors, result.indexed_count, result.remaining_count)
        return NetflixWindowDiscovery(DiscoveryReport(tuple(selected), (report,)), window.progress)

    def discover(self, sources: tuple[SourceSpec, ...], *, title_contains: tuple[str, ...] = (),
                 limit_per_source: int = DEFAULT_SELECTED_JOBS) -> DiscoveryReport:
        # Adapters implement this module's transport/models contract. Import here
        # avoids making their implementation part of the model import surface.
        from grounded_apply.repositories.job_sources import discover_source

        if (type(sources) is not tuple or not 1 <= len(sources) <= self._max_sources
            or type(limit_per_source) is not int or not 1 <= limit_per_source <= MAX_SELECTED_JOBS):
            raise ValueError("Discovery requires bounded sources and a record limit from one to one thousand")
        identities: set[tuple[str, str | None, str | None]] = set()
        ids: set[str] = set()
        for source in sources:
            validate_source_spec(source)
            identity = (source.provider, source.board, source.careers_url)
            if source.id in ids or identity in identities:
                raise ValueError("Source identifiers and board routes must be distinct")
            ids.add(source.id)
            identities.add(identity)
        if (type(title_contains) is not tuple or len(title_contains) > 20
            or any(type(term) is not str or not term.strip() or term != term.strip()
                   or len(term) > 128 or any(ord(char) < 32 or ord(char) == 127 for char in term)
                   for term in title_contains)):
            raise ValueError("Title filters require at most twenty bounded nonblank terms")
        filters = tuple(term.casefold() for term in title_contains)
        jobs = []
        reports = []
        for source in sources:
            fetched_at = datetime.now(UTC).isoformat()
            if source.provider == "manual":
                reports.append(SourceReport(source.id, source.provider, None, source.careers_url,
                    "manual_required", 0, 0, 0, None, fetched_at))
                continue
            result = discover_source(source, self._transport, max_records=MAX_SOURCE_RECORDS)
            matching = tuple(job for job in result.jobs if not filters or any(term in job.title.casefold() for term in filters))
            selected = matching[:limit_per_source]
            errors = result.errors
            if len(matching) > limit_per_source and DiscoveryErrorCode.SOURCE_LIMIT_REACHED not in errors:
                errors += (DiscoveryErrorCode.SOURCE_LIMIT_REACHED,)
            status: SourceStatus = "successful"
            if errors:
                status = "partial" if result.jobs else "failed"
            jobs.extend(selected)
            reports.append(SourceReport(source.id, source.provider, source.board, None, status,
                len(selected), len(result.jobs) - len(matching), result.observed_count,
                errors[0] if errors else None, fetched_at, errors,
                result.indexed_count, result.remaining_count))
        return DiscoveryReport(tuple(jobs), tuple(reports))
