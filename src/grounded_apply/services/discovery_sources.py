"""Turn explicit public board links into a bounded, offline source manifest.

Recognition proves a supported URL shape, not that a board exists or is current.
No supplied URL is fetched, and unknown hosts never become automatic sources.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from grounded_apply.services.discovery import (
    MAX_SOURCES, Provider, SourceSpec, canonical_job_url, netflix_job_url_id,
    validate_source_manifest, validate_source_spec,
)


MAX_SOURCE_URLS = 256
_BOARD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_HOST_PROVIDERS: dict[str, Provider] = {
    "boards.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "jobs.ashbyhq.com": "ashby",
    "jobs.lever.co": "lever",
    "jobs.eu.lever.co": "lever_eu",
    "apply.workable.com": "workable",
}


@dataclass(frozen=True, slots=True)
class SourceURLResolution:
    position: int
    source_id: str
    provider: Provider
    automatic: bool
    duplicate: bool
    discarded_query: bool
    discarded_fragment: bool


@dataclass(frozen=True, slots=True)
class SourceSetupReport:
    sources: tuple[SourceSpec, ...]
    inputs: tuple[SourceURLResolution, ...]

    def manifest(self) -> dict[str, object]:
        """Return the existing closed schema, ready for discovery or saved scope."""
        rows: list[dict[str, object]] = []
        for source in self.sources:
            row: dict[str, object] = {"id": source.id, "provider": source.provider}
            if source.provider == "manual":
                row["careers_url"] = source.careers_url
            else:
                row["board"] = source.board
            rows.append(row)
        result: dict[str, object] = {"schema_version": 1, "sources": rows}
        validate_source_manifest(result)
        return result


def _board_route(provider: Provider, path: str) -> str | None:
    # Strip at most one trailing slash. Never decode or normalize path segments:
    # escaped separators, dot segments and alternate routing stay unsupported.
    parts = path.removesuffix("/").split("/")
    if len(parts) < 2 or parts[0] or _BOARD.fullmatch(parts[1]) is None:
        return None
    board = parts[1]
    if provider == "workable":
        # Workable posting links use /j/{shortcode} without a company token.
        # Only an explicit company board can configure an automatic source.
        return board if len(parts) == 2 and board not in {"j", "api"} else None
    if len(parts) == 2:
        return board
    if provider == "greenhouse":
        if len(parts) != 4 or parts[2] != "jobs":
            return None
        identifier = parts[3]
    else:
        suffix = "application" if provider == "ashby" else "apply"
        if len(parts) != 3 and not (len(parts) == 4 and parts[3] == suffix):
            return None
        identifier = parts[2]
    try:
        canonical_job_url(provider, board, identifier)
    except ValueError:
        return None
    return board


def _source_from_url(value: str) -> tuple[SourceSpec, bool, bool]:
    if (type(value) is not str or not value or len(value) > 2048
        or any(ord(char) <= 32 or ord(char) == 127 for char in value)
        or "\\" in value):
        raise ValueError("Source links must be bounded HTTPS URLs without whitespace")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.port is not None or parsed.netloc.endswith(":")):
            raise ValueError
    except ValueError:
        raise ValueError("Source links require HTTPS without credentials or ports") from None
    host = parsed.hostname
    provider = _HOST_PROVIDERS.get(host)
    board = None if provider is None else _board_route(provider, parsed.path)
    if host == "explore.jobs.netflix.net":
        if parsed.path in {"/careers", "/careers/"}:
            provider, board = "netflix", "netflix"
        else:
            try:
                netflix_job_url_id(urlunsplit(("https", host, parsed.path, "", "")))
                provider, board = "netflix", "netflix"
            except (ValueError, UnicodeError):
                pass
    if provider is not None and board is not None:
        source = SourceSpec(f"{provider}:{board}", provider, board)
        validate_source_spec(source)
        return source, bool(parsed.query), bool(parsed.fragment)
    # Unknown query/fragment values may determine the page's identity. Refuse
    # rather than silently stripping them to satisfy the manual-source schema.
    if parsed.query or parsed.fragment:
        raise ValueError("Unrecognized links need a query-free, fragment-free HTTPS careers URL")
    manual_url = urlunsplit(("https", parsed.netloc.lower(), parsed.path, "", ""))
    try:
        digest = sha256(manual_url.encode("utf-8")).hexdigest()
    except UnicodeError:
        raise ValueError("Source links must contain valid Unicode") from None
    source = SourceSpec(f"manual:{digest}", "manual", careers_url=manual_url)
    validate_source_spec(source)
    return source, False, False


def build_source_manifest(urls: tuple[str, ...] | list[str]) -> SourceSetupReport:
    """Resolve every explicit URL or fail without producing a partial manifest.

    Duplicate board/job links retain input accounting and one stable source ID.
    Parameters on recognized routes are discarded and reported; they do not
    change the whole-board scope. The caller must show that scope to the user.
    """
    if type(urls) not in {tuple, list} or not 1 <= len(urls) <= MAX_SOURCE_URLS:
        raise ValueError("Source setup requires one to 256 explicit links")
    sources: dict[str, SourceSpec] = {}
    inputs: list[SourceURLResolution] = []
    for position, value in enumerate(urls, 1):
        source, discarded_query, discarded_fragment = _source_from_url(value)
        duplicate = source.id in sources
        if not duplicate:
            if len(sources) >= MAX_SOURCES:
                raise ValueError("Source setup supports at most 32 distinct sources; split the watchlist")
            sources[source.id] = source
        inputs.append(SourceURLResolution(position, source.id, source.provider,
            source.provider != "manual", duplicate, discarded_query, discarded_fragment))
    report = SourceSetupReport(tuple(sources.values()), tuple(inputs))
    report.manifest()
    return report


@dataclass(frozen=True, slots=True)
class SourceURLRejection:
    """An input position and fixed reason, without rejected URL or error text."""

    position: int
    error: Literal["invalid_url", "source_limit_reached"]

    def __post_init__(self) -> None:
        if type(self.position) is not int or not 1 <= self.position <= MAX_SOURCE_URLS:
            raise ValueError("Source rejection position must be an integer from one to 256")
        if type(self.error) is not str or self.error not in {"invalid_url", "source_limit_reached"}:
            raise ValueError("Source rejection requires a supported error code")


@dataclass(frozen=True, slots=True)
class KeepValidSourceSetupReport:
    """Account for every supplied link while retaining an optional valid manifest."""

    accepted: SourceSetupReport | None
    rejected_inputs: tuple[SourceURLRejection, ...]
    input_count: int

    def __post_init__(self) -> None:
        if type(self.input_count) is not int or not 1 <= self.input_count <= MAX_SOURCE_URLS:
            raise ValueError("Source setup requires one to 256 explicit links")
        if type(self.rejected_inputs) is not tuple or any(
            type(item) is not SourceURLRejection for item in self.rejected_inputs
        ):
            raise ValueError("Source setup rejections must contain typed input positions")
        accepted_positions: list[int] = []
        if self.accepted is not None:
            if (type(self.accepted) is not SourceSetupReport
                or type(self.accepted.inputs) is not tuple or not self.accepted.inputs
                or any(type(item) is not SourceURLResolution or type(item.position) is not int
                       for item in self.accepted.inputs)):
                raise ValueError("Accepted source setup must contain typed input positions")
            self.accepted.manifest()
            accepted_positions = [item.position for item in self.accepted.inputs]
        rejected_positions = [item.position for item in self.rejected_inputs]
        if (accepted_positions != sorted(accepted_positions)
            or rejected_positions != sorted(rejected_positions)
            or sorted(accepted_positions + rejected_positions) != list(range(1, self.input_count + 1))):
            raise ValueError("Source setup must account for every input position exactly once")

    @property
    def accepted_input_count(self) -> int:
        return 0 if self.accepted is None else len(self.accepted.inputs)

    @property
    def setup_status(self) -> Literal["complete", "partial", "failed"]:
        if self.accepted is None:
            return "failed"
        return "partial" if self.rejected_inputs else "complete"


def build_source_manifest_keep_valid(
    urls: tuple[str, ...] | list[str],
) -> KeepValidSourceSetupReport:
    """Retain valid links and position-only rejections without repairing input.

    The raw input bound still fails the whole request. Once the distinct-source
    cap is reached, later new sources are rejected but links to already retained
    sources remain accepted duplicates. An all-rejected batch has no manifest.
    Unexpected failures propagate; only ordinary URL validation is isolated.
    """

    if type(urls) not in {tuple, list} or not 1 <= len(urls) <= MAX_SOURCE_URLS:
        raise ValueError("Source setup requires one to 256 explicit links")
    sources: dict[str, SourceSpec] = {}
    inputs: list[SourceURLResolution] = []
    rejected: list[SourceURLRejection] = []
    for position, value in enumerate(urls, 1):
        try:
            source, discarded_query, discarded_fragment = _source_from_url(value)
        except ValueError:
            rejected.append(SourceURLRejection(position, "invalid_url"))
            continue
        duplicate = source.id in sources
        if not duplicate:
            if len(sources) >= MAX_SOURCES:
                rejected.append(SourceURLRejection(position, "source_limit_reached"))
                continue
            sources[source.id] = source
        inputs.append(SourceURLResolution(position, source.id, source.provider,
            source.provider != "manual", duplicate, discarded_query, discarded_fragment))
    accepted = SourceSetupReport(tuple(sources.values()), tuple(inputs)) if inputs else None
    return KeepValidSourceSetupReport(accepted, tuple(rejected), len(urls))
