"""Narrow parsers for documented public ATS job-board responses."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlsplit

from grounded_apply.services.discovery import (
    MAX_RESPONSE_BYTES, MAX_SOURCE_RECORDS, MAX_SOURCE_REQUESTS, REQUEST_TIMEOUT, DiscoveredJob,
    DiscoveryErrorCode, SourceSpec, Transport, canonical_job_url,
    discovered_job_digest, validate_discovered_job, validate_source_spec,
)


@dataclass(frozen=True, slots=True)
class SourceResult:
    jobs: tuple[DiscoveredJob, ...]
    observed_count: int
    errors: tuple[DiscoveryErrorCode, ...]
    indexed_count: int | None = None
    remaining_count: int | None = None


class _TextParser(HTMLParser):
    _BLOCKS = frozenset({"p", "div", "section", "article", "header", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "br", "hr", "table", "tr", "td", "th", "blockquote", "pre"})
    _HIDDEN = frozenset({"script", "style", "template", "noscript", "iframe", "object", "svg"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._HIDDEN:
            self.hidden.append(tag)
        elif not self.hidden and tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
        elif tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def _text(value: object, *, maximum: int = 1024 * 1024, allow_empty: bool = False) -> str:
    if (type(value) is not str or len(value.encode("utf-8")) > maximum
        or any(ord(char) < 32 and char not in "\n\r\t" or ord(char) == 127 for char in value)
        or not allow_empty and not value.strip()):
        raise ValueError("Invalid source text")
    return value


def html_to_text(value: object) -> str:
    """Preserve visible wording and blocks; never execute markup or visit links."""
    text = _text(value)
    # Greenhouse can encode the whole HTML document as entities. Decode only
    # that outer representation, not literal escaped text inside actual tags.
    for _ in range(2):
        if re.search(r"</?[A-Za-z][^>]*>", text) or "&lt;" not in text and "&#" not in text:
            break
        decoded = unescape(text)
        if decoded == text:
            break
        text = decoded
    parser = _TextParser()
    try:
        parser.feed(text)
        parser.close()
    except AssertionError:
        raise ValueError("Invalid HTML declaration") from None
    lines = [re.sub(r"[\t\r\f\v ]+", " ", line).strip() for line in "".join(parser.parts).split("\n")]
    result = "\n".join(line for line in lines if line)
    return _text(result)


def _plain(value: object, *, maximum: int = 1024 * 1024, allow_empty: bool = False) -> str:
    return _text(value, maximum=maximum, allow_empty=allow_empty).replace("\r\n", "\n").replace("\r", "\n").strip()


def _line(value: object, maximum: int) -> str:
    text = _plain(value, maximum=maximum)
    if "\n" in text or "\t" in text:
        raise ValueError("Expected one line")
    return text


def _location(value: object) -> str | None:
    return None if value is None or value == "" else _line(value, 2048)


def _job(source: SourceSpec, *, external_id: str, title: str, location: str | None,
         description: str) -> DiscoveredJob:
    assert source.board is not None
    url = canonical_job_url(source.provider, source.board, external_id)
    # Title and location are distinct source fields, preserved without inferred
    # employer, requirements, seniority, or eligibility labels.
    text = "\n\n".join(part for part in (title, location, description) if part is not None)
    job = DiscoveredJob(source.provider, source.board, external_id, url, title, location, text, "")
    job = replace(job, content_sha256=discovered_job_digest(job))
    validate_discovered_job(job)
    return job


def _greenhouse(source: SourceSpec, record: dict[str, object]) -> DiscoveredJob:
    identifier = record.get("id")
    if type(identifier) is not int or identifier <= 0:
        raise ValueError("Invalid posting ID")
    location = record.get("location")
    if location is not None and type(location) is not dict:
        raise ValueError("Invalid posting location")
    return _job(source, external_id=str(identifier), title=_line(record.get("title"), 512),
        location=_location(None if location is None else location.get("name")),
        description=html_to_text(record.get("content")))


def _ashby(source: SourceSpec, record: dict[str, object]) -> DiscoveredJob | None:
    listed = record.get("isListed")
    if type(listed) is not bool:
        raise ValueError("Posting visibility is unknown")
    if not listed:
        return None
    url = _line(record.get("jobUrl"), 2048)
    parsed = urlsplit(url)
    path = parsed.path.split("/")
    if (parsed.scheme != "https" or parsed.netloc != "jobs.ashbyhq.com"
        or parsed.query or parsed.fragment or len(path) != 3 or path[1] != source.board):
        raise ValueError("Invalid Ashby posting URL")
    identifier = path[2]
    if canonical_job_url(source.provider, source.board or "", identifier) != url:
        raise ValueError("Invalid Ashby posting identity")
    description = _plain(record["descriptionPlain"]) if "descriptionPlain" in record else html_to_text(record.get("descriptionHtml"))
    return _job(source, external_id=identifier, title=_line(record.get("title"), 512),
        location=_location(record.get("location")), description=description)


def _lever(source: SourceSpec, record: dict[str, object]) -> DiscoveredJob:
    identifier = record.get("id")
    if type(identifier) is not str:
        raise ValueError("Invalid posting ID")
    categories = record.get("categories")
    if categories is not None and type(categories) is not dict:
        raise ValueError("Invalid posting categories")
    description = _plain(record["descriptionPlain"], allow_empty=True) if "descriptionPlain" in record else html_to_text(record.get("description"))
    parts = [description]
    lists = record.get("lists", [])
    if type(lists) is not list or len(lists) > 100:
        raise ValueError("Invalid posting lists")
    for section in lists:
        if type(section) is not dict:
            raise ValueError("Invalid posting list")
        parts.extend((_line(section.get("text"), 512), html_to_text(section.get("content"))))
    if "additionalPlain" in record:
        additional = _plain(record["additionalPlain"], allow_empty=True)
        if additional:
            parts.append(additional)
    elif record.get("additional"):
        parts.append(html_to_text(record["additional"]))
    return _job(source, external_id=identifier, title=_line(record.get("text"), 512),
        location=_location(None if categories is None else categories.get("location")), description=_text("\n\n".join(parts)))


def _workable_location(record: dict[str, object]) -> str | None:
    def place(values: tuple[object, ...]) -> str | None:
        parts = [_line(value, 512) for value in values if value is not None and value != ""]
        return ", ".join(dict.fromkeys(parts)) or None

    places: list[str] = []
    if "locations" in record:
        locations = record["locations"]
        if type(locations) is not list or len(locations) > 100:
            raise ValueError("Invalid Workable locations")
        for location in locations:
            if type(location) is not dict or type(location.get("hidden")) is not bool:
                raise ValueError("Workable location visibility is unknown")
            if location["hidden"]:
                continue
            value = place(tuple(location.get(key) for key in ("city", "region", "country")))
            if value is None:
                raise ValueError("Visible Workable location is empty")
            places.append(value)
        # An explicit list controls location visibility. Falling back to the
        # top-level fields could restore a location the board marked hidden.
    else:
        value = place(tuple(record.get(key) for key in ("city", "state", "country")))
        if value is not None:
            places.append(value)
    if "telecommuting" in record:
        if type(record["telecommuting"]) is not bool:
            raise ValueError("Invalid Workable remote flag")
        if record["telecommuting"]:
            places.append("Remote")
    return _location("; ".join(dict.fromkeys(places)))


def _workable(source: SourceSpec, record: dict[str, object]) -> DiscoveredJob:
    identifier = record.get("shortcode")
    if type(identifier) is not str:
        raise ValueError("Invalid Workable posting identity")
    url = canonical_job_url(source.provider, source.board or "", identifier)
    if record.get("url") != url or record.get("shortlink") != url:
        raise ValueError("Workable posting URLs do not match its identity")
    if "application_url" in record and record["application_url"] != url + "/apply":
        raise ValueError("Invalid Workable application URL")
    # This documented public widget route publishes public jobs. Its `state`
    # field is geographic; the authenticated SPI publication-state schema does
    # not apply. Only explicit public location fields establish location/remote.
    return _job(source, external_id=identifier, title=_line(record.get("title"), 512),
        location=_workable_location(record), description=html_to_text(record.get("description")))


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise ValueError("Non-finite JSON value")


def _records(provider: str, payload: bytes) -> list[object]:
    parsed = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    if provider == "greenhouse":
        if type(parsed) is not dict or type(parsed.get("jobs")) is not list:
            raise ValueError("Invalid Greenhouse response")
        records = parsed["jobs"]
        # A reported total larger than the supplied board is incomplete; do not
        # claim this is an authoritative empty/complete observation.
        if "meta" in parsed:
            meta = parsed["meta"]
            if type(meta) is not dict or type(meta.get("total")) is not int or meta["total"] != len(records):
                raise ValueError("Incomplete Greenhouse response")
    elif provider == "ashby":
        if type(parsed) is not dict or parsed.get("apiVersion") != "1" or type(parsed.get("jobs")) is not list:
            raise ValueError("Invalid Ashby response")
        records = parsed["jobs"]
    elif provider == "workable":
        # The documented widget is a single public feed. Unknown fields could
        # introduce pagination or completeness semantics: do not silently ignore
        # them, follow cursors, or claim an authoritative complete observation.
        if (type(parsed) is not dict or set(parsed) != {"name", "description", "jobs"}
            or type(parsed["jobs"]) is not list):
            raise ValueError("Invalid Workable response")
        _line(parsed["name"], 512)
        _text(parsed["description"], allow_empty=True)
        records = parsed["jobs"]
    else:
        records = parsed
    if type(records) is not list:
        raise ValueError("Invalid source response")
    return records


def _url(source: SourceSpec, skip: int) -> str:
    if source.provider == "greenhouse":
        return f"https://boards-api.greenhouse.io/v1/boards/{source.board}/jobs?content=true"
    if source.provider == "ashby":
        return f"https://api.ashbyhq.com/posting-api/job-board/{source.board}"
    if source.provider == "workable":
        return f"https://apply.workable.com/api/v1/widget/accounts/{source.board}?details=true"
    host = "api.eu.lever.co" if source.provider == "lever_eu" else "api.lever.co"
    return f"https://{host}/v0/postings/{source.board}?mode=json&skip={skip}&limit=100"


def discover_source(source: SourceSpec, transport: Transport, *, max_records: int) -> SourceResult:
    """Fetch one bounded board. Errors remain fixed codes, never remote content."""
    validate_source_spec(source)
    if source.provider == "manual" or type(max_records) is not int or not 1 <= max_records <= MAX_SOURCE_RECORDS:
        raise ValueError("Board adapter requires an ATS source and a bounded record limit")
    if source.provider == "netflix":
        from grounded_apply.repositories.netflix_source import discover_netflix

        return discover_netflix(source, transport, max_records=max_records)
    jobs: dict[str, DiscoveredJob] = {}
    conflicts: set[str] = set()
    errors: list[DiscoveryErrorCode] = []
    observed = 0
    skip = 0

    def error(code: DiscoveryErrorCode) -> None:
        if code not in errors:
            errors.append(code)

    for _ in range(MAX_SOURCE_REQUESTS):
        try:
            payload = transport.get(_url(source, skip), max_bytes=MAX_RESPONSE_BYTES, timeout=REQUEST_TIMEOUT)
        except Exception as exc:
            try:
                code = DiscoveryErrorCode(getattr(exc, "code", "transport_failure"))
            except (ValueError, TypeError):
                code = DiscoveryErrorCode.TRANSPORT_FAILURE
            error(code)
            break
        if type(payload) is not bytes:
            error(DiscoveryErrorCode.INVALID_RESPONSE)
            break
        if len(payload) > MAX_RESPONSE_BYTES:
            error(DiscoveryErrorCode.RESPONSE_TOO_LARGE)
            break
        try:
            records = _records(source.provider, payload)
        except (ValueError, TypeError, UnicodeError, RecursionError):
            error(DiscoveryErrorCode.INVALID_PAYLOAD)
            break
        paginated = source.provider in {"lever", "lever_eu"}
        if paginated and len(records) > 100:
            error(DiscoveryErrorCode.INVALID_PAYLOAD)
            break
        remaining = max_records - observed
        for record in records[:remaining]:
            observed += 1
            try:
                if type(record) is not dict:
                    raise ValueError("Invalid posting record")
                parser = (_greenhouse if source.provider == "greenhouse" else
                    _ashby if source.provider == "ashby" else
                    _workable if source.provider == "workable" else _lever)
                job = parser(source, record)
                if job is None:
                    continue
            except (ValueError, TypeError, UnicodeError, RecursionError, KeyError):
                error(DiscoveryErrorCode.INVALID_RECORD)
                continue
            if job.external_id in conflicts:
                continue
            previous = jobs.get(job.external_id)
            if previous is not None and previous != job:
                del jobs[job.external_id]
                conflicts.add(job.external_id)
                error(DiscoveryErrorCode.CONFLICTING_DUPLICATE)
            else:
                jobs[job.external_id] = job
        if len(records) > remaining:
            error(DiscoveryErrorCode.SOURCE_LIMIT_REACHED)
            break
        if not paginated or len(records) < 100:
            break
        if observed >= max_records:
            error(DiscoveryErrorCode.SOURCE_LIMIT_REACHED)
            break
        skip += len(records)
    else:
        error(DiscoveryErrorCode.SOURCE_LIMIT_REACHED)
    return SourceResult(tuple(jobs[key] for key in sorted(jobs)), observed, tuple(errors))
