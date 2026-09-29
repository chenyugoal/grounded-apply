"""Bounded, credential-free GETs to documented public job-board endpoints.

This adapter never uses environment proxies or follows redirects. DNS address
screening is sampled before the standard TLS connection resolves the hostname;
it is not DNS pinning or an atomic defense against resolver changes. TLS still
authenticates the allowlisted hostname. DNS resolution itself has no portable
stdlib hard deadline. Connection operations have socket inactivity timeouts;
after connecting, one monotonic deadline and socket shutdown timer cover header
and body reads, including slow chunk framing inside the HTTP parser.
"""

from __future__ import annotations

import http.client
import ipaddress
import math
import re
import socket
import ssl
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext, suppress
from urllib.parse import urlsplit

from grounded_apply.services.discovery import netflix_job_url_id

MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024
_BOARD = r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}"
_WORKABLE_URL = re.compile(
    rf"https://apply\.workable\.com/api/v1/widget/accounts/{_BOARD}\?details=true"
)
_WORKABLE_MINIMUM_GAP = 1.05
_ALLOWED_URLS = (
    re.compile(
        rf"https://boards-api\.greenhouse\.io/v1/boards/{_BOARD}/jobs"
        r"\?content=true"
    ),
    re.compile(rf"https://api\.ashbyhq\.com/posting-api/job-board/{_BOARD}"),
    re.compile(
        rf"https://api\.(?:eu\.)?lever\.co/v0/postings/{_BOARD}"
        r"\?mode=json&skip=(?:0|[1-9][0-9]{0,6})&limit=100"
    ),
    _WORKABLE_URL,
)
_NETFLIX_URLS = (
    (re.compile(r"https://explore\.jobs\.netflix\.net/robots\.txt"), ("text/plain",)),
    (re.compile(
        r"https://explore\.jobs\.netflix\.net/careers/sitemap(?:_index)?\.xml"
        r"\?domain=netflix\.com&microsite=netflix\.com"
    ), ("application/xml", "text/xml")),
)
_ERROR_CODES = frozenset(
    {
        "transport_failure",
        "timeout",
        "response_too_large",
        "rate_limited",
        "forbidden",
        "not_found",
        "redirect_refused",
        "invalid_response",
    }
)


class PublicJobTransportError(Exception):
    """A content-free error: never include a URL, response body, or exception."""

    def __init__(self, code: str) -> None:
        self.code = code if code in _ERROR_CODES else "transport_failure"
        super().__init__(self.code)


class PublicJobHTTPTransport:
    """Fetch only bounded, public board listings; no candidate input is sent."""

    def __init__(self) -> None:
        self._workable_lock = threading.Lock()
        self._workable_next_attempt: float | None = None

    @contextmanager
    def _workable_attempt(self, deadline: float) -> Iterator[None]:
        # Courtesy pacing belongs to this transport instance, not all processes
        # or an authenticated account. Keep at least a second between attempts
        # without extending the caller's original timeout budget.
        if not self._workable_lock.acquire(timeout=_remaining(deadline)):
            raise PublicJobTransportError("timeout")
        attempted = False
        try:
            while self._workable_next_attempt is not None:
                wait = self._workable_next_attempt - time.monotonic()
                if wait <= 0:
                    break
                if wait >= _remaining(deadline):
                    raise PublicJobTransportError("timeout")
                time.sleep(wait)
            _remaining(deadline)
            attempted = True
            yield
        finally:
            if attempted:
                self._workable_next_attempt = time.monotonic() + _WORKABLE_MINIMUM_GAP
            self._workable_lock.release()

    def get(self, url: str, *, max_bytes: int, timeout: float) -> bytes:
        if (
            type(max_bytes) is not int
            or not 1 <= max_bytes <= MAX_RESPONSE_BYTES
            or isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or not 0 < timeout <= 60
            or not isinstance(url, str)
            or len(url) > 2048
        ):
            raise PublicJobTransportError("invalid_response")

        allowed_types: tuple[str, ...] = ()
        if any(pattern.fullmatch(url) for pattern in _ALLOWED_URLS):
            allowed_types = ("application/json",)
        for pattern, media_types in _NETFLIX_URLS:
            if pattern.fullmatch(url):
                allowed_types = media_types
                break
        if not allowed_types:
            try:
                netflix_job_url_id(url)
                allowed_types = ("text/html",)
            except (ValueError, UnicodeError):
                pass
        if not allowed_types:
            raise PublicJobTransportError("invalid_response")

        parsed = urlsplit(url)
        host = parsed.netloc
        target = parsed.path + ("?" + parsed.query if parsed.query else "")
        deadline = time.monotonic() + timeout
        connection: http.client.HTTPSConnection | None = None
        response: http.client.HTTPResponse | None = None
        deadline_timer: threading.Timer | None = None
        expired = threading.Event()
        try:
            with self._workable_attempt(deadline) if _WORKABLE_URL.fullmatch(url) else nullcontext():
                _require_public_resolution(host)
                connection = http.client.HTTPSConnection(
                    host,
                    timeout=_remaining(deadline),
                    context=ssl.create_default_context(),
                )
                connection.request(
                    "GET",
                    target,
                    headers={
                        "User-Agent": "GroundedApply/1 public-job-discovery",
                        "Accept": ", ".join(allowed_types),
                        "Accept-Encoding": "identity",
                        "Connection": "close",
                    },
                )
            # Keep the socket reference: getresponse() may detach it from the
            # connection for a response marked Connection: close.
            request_socket = connection.sock
            if request_socket is None:
                raise PublicJobTransportError("transport_failure")

            def interrupt_expired_request() -> None:
                expired.set()
                with suppress(OSError):
                    request_socket.shutdown(socket.SHUT_RDWR)

            deadline_timer = threading.Timer(_remaining(deadline), interrupt_expired_request)
            deadline_timer.daemon = True
            deadline_timer.start()
            request_socket.settimeout(_remaining(deadline))
            response = connection.getresponse()
            if expired.is_set():
                raise PublicJobTransportError("timeout")
            _check_status(response.status)
            expected_length = _check_headers(response, max_bytes, allowed_types=allowed_types)
            body = bytearray()
            # HTTPResponse closes its file when the declared body is consumed.
            # With Connection: close that also closes the detached socket, so
            # do not set a timeout on it for a redundant final EOF read.
            while not response.isclosed():
                request_socket.settimeout(_remaining(deadline))
                # read1 performs at most one buffered/raw read. read(n) could
                # keep accepting a slow stream past the cumulative deadline.
                chunk = response.read1(min(_READ_CHUNK_BYTES, max_bytes - len(body) + 1))
                if expired.is_set():
                    raise PublicJobTransportError("timeout")
                _remaining(deadline)
                if not chunk:
                    break
                if len(body) + len(chunk) > max_bytes:
                    raise PublicJobTransportError("response_too_large")
                body.extend(chunk)
            if expected_length is not None and len(body) != expected_length:
                raise PublicJobTransportError("invalid_response")
            return bytes(body)
        except PublicJobTransportError:
            raise
        except (TimeoutError, socket.timeout):
            raise PublicJobTransportError("timeout") from None
        except (OSError, http.client.HTTPException, ValueError):
            code = "timeout" if expired.is_set() else "transport_failure"
            raise PublicJobTransportError(code) from None
        finally:
            if deadline_timer is not None:
                deadline_timer.cancel()
            if response is not None:
                with suppress(OSError, http.client.HTTPException):
                    response.close()
            if connection is not None:
                with suppress(OSError, http.client.HTTPException):
                    connection.close()


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise PublicJobTransportError("timeout")
    return remaining


def _require_public_resolution(host: str) -> None:
    addresses = socket.getaddrinfo(
        host, 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
    )
    if not addresses:
        raise PublicJobTransportError("transport_failure")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global or ip.is_multicast or ip.is_reserved:
            raise PublicJobTransportError("forbidden")


def _check_status(status: int) -> None:
    if 300 <= status < 400:
        raise PublicJobTransportError("redirect_refused")
    codes = {401: "forbidden", 403: "forbidden", 404: "not_found", 429: "rate_limited"}
    if status in codes:
        raise PublicJobTransportError(codes[status])
    if status != 200:
        raise PublicJobTransportError("transport_failure")


def _check_headers(response: http.client.HTTPResponse, max_bytes: int, *,
                   allowed_types: tuple[str, ...] = ("application/json",)) -> int | None:
    headers = response.getheaders()
    # Careers pages may communicate crawler restrictions in HTTP headers as
    # well as robots.txt and HTML. Conservatively honor restrictive directives
    # even when an unrecognized crawler prefix accompanies them.
    if allowed_types != ("application/json",) and any(
        name.lower() == "x-robots-tag"
        and re.search(r"\b(?:none|noindex|nofollow|noarchive|nosnippet|noai)\b", value.lower())
        for name, value in headers
    ):
        raise PublicJobTransportError("forbidden")
    lengths = [value for name, value in headers if name.lower() == "content-length"]
    transfers = [value for name, value in headers if name.lower() == "transfer-encoding"]
    encodings = [value for name, value in headers if name.lower() == "content-encoding"]
    content_types = [value for name, value in headers if name.lower() == "content-type"]
    if (
        len(lengths) > 1
        or len(transfers) > 1
        or (lengths and transfers)
        or any(value.strip().lower() != "chunked" for value in transfers)
        or any(value.strip().lower() != "identity" for value in encodings)
        or len(content_types) != 1
        or content_types[0].split(";", 1)[0].strip().lower() not in allowed_types
    ):
        raise PublicJobTransportError("invalid_response")
    if not lengths:
        return None
    value = lengths[0].strip()
    if not re.fullmatch(r"[0-9]{1,10}", value):
        raise PublicJobTransportError("invalid_response")
    length = int(value)
    if length > max_bytes:
        raise PublicJobTransportError("response_too_large")
    return length
