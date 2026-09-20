from __future__ import annotations

import http.client
import io
import socket
import ssl
import unittest
from unittest.mock import patch

from grounded_apply.repositories.discovery_http import (
    MAX_RESPONSE_BYTES,
    PublicJobHTTPTransport,
    PublicJobTransportError,
)


MODULE = "grounded_apply.repositories.discovery_http"
GREENHOUSE = "https://boards-api.greenhouse.io/v1/boards/synthetic-example/jobs?content=true"
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/synthetic-example"
LEVER = "https://api.lever.co/v0/postings/synthetic-example?mode=json&skip=0&limit=100"
LEVER_EU = "https://api.eu.lever.co/v0/postings/synthetic-example?mode=json&skip=100&limit=100"


class FakeResponse:
    def __init__(
        self,
        body: bytes = b'{"jobs":[]}',
        *,
        status: int = 200,
        headers: list[tuple[str, str]] | None = None,
    ) -> None:
        self.body = body
        self.offset = 0
        self.status = status
        self.headers = headers if headers is not None else [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
        ]
        self.closed = False
        self.read_sizes: list[int] = []

    def getheaders(self) -> list[tuple[str, str]]:
        return self.headers

    def read1(self, amount: int) -> bytes:
        self.read_sizes.append(amount)
        data = self.body[self.offset : self.offset + amount]
        self.offset += len(data)
        return data

    def close(self) -> None:
        self.closed = True

    def isclosed(self) -> bool:
        return self.closed


class PublicJobHTTPTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        # No test opens a socket. This is a synthetic DNS answer representing a
        # public address; real TEST-NET addresses are correctly non-global.
        self.dns = patch(
            MODULE + ".socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))],
        ).start()
        self.factory = patch(MODULE + ".http.client.HTTPSConnection").start()
        self.timer_factory = patch(MODULE + ".threading.Timer").start()
        self.addCleanup(patch.stopall)
        self.connection = self.factory.return_value
        self.response = FakeResponse()
        self.connection.getresponse.return_value = self.response
        self.transport = PublicJobHTTPTransport()

    def assert_code(self, code: str, url: str = GREENHOUSE, **kwargs: object) -> None:
        arguments = {"max_bytes": 1024, "timeout": 10.0, **kwargs}
        with self.assertRaises(PublicJobTransportError) as caught:
            self.transport.get(url, **arguments)  # type: ignore[arg-type]
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)

    def test_only_documented_board_gets_are_sent_without_credentials(self) -> None:
        for url in (GREENHOUSE, ASHBY, LEVER, LEVER_EU):
            with self.subTest(url=url):
                response = FakeResponse()
                self.connection.getresponse.return_value = response
                with patch.dict(
                    "os.environ",
                    {"HTTPS_PROXY": "https://user:secret@127.0.0.1:9000", "ALL_PROXY": "http://localhost"},
                ):
                    self.assertEqual(
                        self.transport.get(url, max_bytes=1024, timeout=10), b'{"jobs":[]}'
                    )
                request = self.connection.request.call_args
                self.assertEqual(request.args[0], "GET")
                self.assertTrue(request.args[1].startswith("/"))
                self.assertEqual(
                    request.kwargs["headers"],
                    {
                        "User-Agent": "GroundedApply/1 public-job-discovery",
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                        "Connection": "close",
                    },
                )
                context = self.factory.call_args.kwargs["context"]
                self.assertTrue(context.check_hostname)
                self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
                self.assertTrue(response.closed)
                self.connection.close.assert_called()
                self.timer_factory.return_value.cancel.assert_called()

    def test_arbitrary_destinations_and_url_tricks_fail_before_dns(self) -> None:
        invalid = (
            "http://api.ashbyhq.com/posting-api/job-board/synthetic-example",
            "https://localhost/posting-api/job-board/synthetic-example",
            "https://127.0.0.1/posting-api/job-board/synthetic-example",
            ASHBY.replace("api.ashbyhq.com", "api.ashbyhq.com.example.com"),
            ASHBY.replace("api.ashbyhq.com", "user:secret@api.ashbyhq.com"),
            ASHBY.replace("api.ashbyhq.com", "api.ashbyhq.com:444"),
            ASHBY + "#fragment",
            ASHBY + "?includeCompensation=true",
            ASHBY.replace("synthetic-example", "../private"),
            ASHBY.replace("synthetic-example", "%2e%2e"),
            ASHBY.replace("synthetic-example", "board/nested"),
            ASHBY.replace("synthetic-example", "board\\nested"),
            ASHBY.replace("synthetic-example", "-board"),
            ASHBY.replace("synthetic-example", "a" * 129),
            "\n" + ASHBY,
            ASHBY + "\n",
            GREENHOUSE + "&questions=true",
            GREENHOUSE.replace("content=true", "content=false"),
            GREENHOUSE.replace("/jobs?", "/jobs/123?"),
            LEVER.replace("skip=0", "skip=-1"),
            LEVER.replace("limit=100", "limit=100000"),
            LEVER + "&mode=html",
        )
        for url in invalid:
            with self.subTest(url=url):
                self.assert_code("invalid_response", url)
        self.dns.assert_not_called()
        self.factory.assert_not_called()

    def test_invalid_budgets_fail_before_dns(self) -> None:
        for max_bytes in (0, -1, True, 1.5, MAX_RESPONSE_BYTES + 1):
            with self.subTest(max_bytes=max_bytes):
                self.assert_code("invalid_response", max_bytes=max_bytes)
        for timeout in (0, -1, True, float("inf"), float("nan"), 61):
            with self.subTest(timeout=timeout):
                self.assert_code("invalid_response", timeout=timeout)
        self.dns.assert_not_called()

    def test_nonpublic_and_mixed_dns_answers_fail_before_connection(self) -> None:
        for address in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fd00::1", "224.0.0.1", "192.0.2.1"):
            with self.subTest(address=address):
                self.dns.return_value = [
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443)),
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)),
                ]
                self.assert_code("forbidden")
        self.factory.assert_not_called()

    def test_empty_resolution_and_dns_failure_are_redacted(self) -> None:
        self.dns.return_value = []
        self.assert_code("transport_failure")
        self.dns.side_effect = socket.gaierror("private DNS error details")
        self.assert_code("transport_failure")
        self.factory.assert_not_called()

    def test_status_errors_never_read_or_follow_response(self) -> None:
        for status, code in (
            (301, "redirect_refused"),
            (302, "redirect_refused"),
            (307, "redirect_refused"),
            (308, "redirect_refused"),
            (401, "forbidden"),
            (403, "forbidden"),
            (404, "not_found"),
            (429, "rate_limited"),
            (500, "transport_failure"),
            (204, "transport_failure"),
        ):
            with self.subTest(status=status):
                response = FakeResponse(
                    b"do not log this response", status=status,
                    headers=[("Location", "https://localhost/private")],
                )
                self.connection.getresponse.return_value = response
                prior_count = self.factory.call_count
                self.assert_code(code)
                self.assertEqual(self.factory.call_count, prior_count + 1)
                self.assertEqual(response.read_sizes, [])
                self.assertTrue(response.closed)

    def test_content_length_prevents_large_read_and_rejects_invalid_framing(self) -> None:
        self.response.headers = [("Content-Type", "application/json"), ("Content-Length", "1025")]
        self.assert_code("response_too_large")
        self.assertEqual(self.response.read_sizes, [])
        for extra in (
            [("Content-Length", "-1")],
            [("Content-Length", "not a number")],
            [("Content-Length", "1"), ("Content-Length", "1")],
            [("Content-Length", "1"), ("Transfer-Encoding", "chunked")],
            [("Transfer-Encoding", "gzip")],
            [("Content-Encoding", "gzip")],
            [("Transfer-Encoding", "chunked"), ("Transfer-Encoding", "chunked")],
        ):
            with self.subTest(headers=extra):
                self.response.headers = [("Content-Type", "application/json"), *extra]
                self.assert_code("invalid_response")

    def test_nonjson_or_missing_content_type_is_rejected_without_body(self) -> None:
        for headers in ([], [("Content-Type", "text/html")], [("Content-Type", "application/json")] * 2):
            with self.subTest(headers=headers):
                self.response.headers = headers
                self.assert_code("invalid_response")
        self.assertEqual(self.response.read_sizes, [])

    def test_chunked_or_unknown_length_body_is_bounded(self) -> None:
        for extra in ([], [("Transfer-Encoding", "chunked")]):
            with self.subTest(headers=extra):
                response = FakeResponse(b"x" * 1025, headers=[("Content-Type", "application/json"), *extra])
                self.connection.getresponse.return_value = response
                self.assert_code("response_too_large")
                self.assertEqual(response.offset, 1025)
                self.assertTrue(all(size <= 1025 for size in response.read_sizes))
                self.assertTrue(response.closed)

    def test_full_budget_is_accepted_and_reads_remain_chunked(self) -> None:
        body = b" " * (5 * 1024 * 1024)
        response = FakeResponse(body)
        self.connection.getresponse.return_value = response
        self.assertEqual(self.transport.get(ASHBY, max_bytes=MAX_RESPONSE_BYTES, timeout=10), body)
        self.assertTrue(all(size <= 64 * 1024 for size in response.read_sizes))
        response = FakeResponse(b"x" * 1024)
        self.connection.getresponse.return_value = response
        self.assertEqual(self.transport.get(ASHBY, max_bytes=1024, timeout=10), b"x" * 1024)

    def test_truncated_content_length_is_not_success(self) -> None:
        self.response.headers = [("Content-Type", "application/json"), ("Content-Length", "100")]
        self.assert_code("invalid_response")

    def test_real_http_response_completion_can_close_socket_before_next_read(self) -> None:
        body = b'{"jobs":[]}'
        for framing in ("content-length", "chunked"):
            with self.subTest(framing=framing):
                if framing == "content-length":
                    header = b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                    framed_body = body
                else:
                    header = b"Transfer-Encoding: chunked\r\n"
                    framed_body = format(len(body), "x").encode() + b"\r\n" + body + b"\r\n0\r\n\r\n"
                stream = io.BytesIO(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    b"Connection: close\r\n" + header + b"\r\n" + framed_body
                )
                self.connection.sock.makefile.return_value = stream
                response = http.client.HTTPResponse(self.connection.sock)
                response.begin()
                self.connection.getresponse.return_value = response

                def require_open_socket(_: float) -> None:
                    if stream.closed:
                        raise OSError(9, "Bad file descriptor")

                self.connection.sock.settimeout.side_effect = require_open_socket
                self.assertEqual(
                    self.transport.get(ASHBY, max_bytes=1024, timeout=10), body
                )
                self.assertTrue(response.isclosed())

    def test_network_tls_protocol_and_timeout_errors_are_redacted(self) -> None:
        for error, code in (
            (OSError("secret network details"), "transport_failure"),
            (ssl.SSLError("secret certificate details"), "transport_failure"),
            (http.client.HTTPException("secret protocol details"), "transport_failure"),
            (TimeoutError("secret timeout details"), "timeout"),
        ):
            with self.subTest(error=type(error).__name__):
                self.connection.getresponse.side_effect = error
                self.assert_code(code)
                self.connection.close.assert_called()

    def test_cleanup_failure_does_not_disclose_error_or_replace_primary_failure(self) -> None:
        self.connection.getresponse.side_effect = TimeoutError("secret timeout details")
        self.connection.close.side_effect = OSError("secret cleanup details")
        self.assert_code("timeout")

    def test_slow_body_cannot_reset_total_deadline_with_each_chunk(self) -> None:
        response = FakeResponse(b"a" * 200000)
        self.connection.getresponse.return_value = response
        clock = iter((0, 0, 0, 0, 1, 9, 9, 11))
        with patch(MODULE + ".time.monotonic", side_effect=lambda: next(clock)):
            self.assert_code("timeout", max_bytes=300000)
        self.assertTrue(response.closed)
        timeouts = [call.args[0] for call in self.connection.sock.settimeout.call_args_list]
        self.assertEqual(timeouts, [10, 9, 1])

    def test_deadline_interrupts_header_or_chunk_framing_reads(self) -> None:
        def expired_headers() -> FakeResponse:
            callback = self.timer_factory.call_args.args[1]
            callback()
            raise http.client.RemoteDisconnected("sensitive partial header text")

        self.connection.getresponse.side_effect = expired_headers
        self.assert_code("timeout")
        self.connection.sock.shutdown.assert_called_once_with(socket.SHUT_RDWR)
        self.timer_factory.return_value.cancel.assert_called_once()
        self.assertTrue(self.timer_factory.return_value.daemon)

    def test_deadline_shutdown_cannot_turn_truncated_body_into_success(self) -> None:
        def expired_read(_: int) -> bytes:
            self.timer_factory.call_args.args[1]()
            return b""

        with patch.object(self.response, "read1", side_effect=expired_read):
            self.assert_code("timeout")


if __name__ == "__main__":
    unittest.main()
