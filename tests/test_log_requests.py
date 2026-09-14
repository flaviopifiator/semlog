"""`log_requests`/`http.server.request` completion event tests (HTM-007,
task 3.13/3.14), driven only through stdlib `wsgiref`-style helpers (CP-010)
and the hand-written ASGI caller (CP-011).

Disabled by default (`log_requests=False`); when a middleware is
constructed with `log_requests=True`, it emits exactly one
`http.server.request` event per request: INFO on a normal return (any
status), ERROR when the wrapped application raises unhandled, with the
exception rendered exactly once and re-raised unchanged. Fields:
`http.request.method`, `url.path`, `http.response.status_code`,
`event.duration` (nanoseconds, from a monotonic clock); `url.query` is
never present. A failure inside the event-emission logic itself must never
reach the wrapped application (see `RequestEventNeverBreaksTheAppTests`).
"""

from __future__ import annotations

import asyncio
import logging
import unittest
from unittest import mock

from semlog._middleware import ASGIMiddleware, WSGIMiddleware

from ._asgi_support import call_asgi, http_scope
from ._wsgi_support import environ, recording_start_response

_LOGGER_NAME = "semlog._middleware"


def _ok_wsgi_app(environ, start_response):
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [b"ok"]


def _raising_wsgi_app(environ, start_response):
    raise ValueError("wsgi boom")


def _streaming_wsgi_app(environ, start_response):
    start_response("200 OK", [])

    def body():
        yield b"a"
        yield b"b"

    return body()


async def _ok_asgi_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200})
    await send({"type": "http.response.body", "body": b"ok"})


async def _raising_asgi_app(scope, receive, send):
    raise ValueError("asgi boom")


class LogRequestsDisabledByDefaultTests(unittest.TestCase):
    """Proves: HTM-007"""

    def test_wsgi_emits_nothing_with_no_explicit_log_requests(self):
        middleware = WSGIMiddleware(_ok_wsgi_app)
        with self.assertNoLogs(_LOGGER_NAME, level="INFO"):
            list(middleware(environ(), recording_start_response()))

    def test_asgi_emits_nothing_with_no_explicit_log_requests(self):
        middleware = ASGIMiddleware(_ok_asgi_app)
        with self.assertNoLogs(_LOGGER_NAME, level="INFO"):
            asyncio.run(call_asgi(middleware, http_scope()))

    def test_wsgi_emits_nothing_with_log_requests_explicitly_false(self):
        middleware = WSGIMiddleware(_ok_wsgi_app, log_requests=False)
        with self.assertNoLogs(_LOGGER_NAME, level="INFO"):
            list(middleware(environ(), recording_start_response()))


class WSGILogRequestsTests(unittest.TestCase):
    """Proves: HTM-007"""

    def test_enabled_emits_one_info_event_with_the_documented_fields(self):
        middleware = WSGIMiddleware(_ok_wsgi_app, log_requests=True)
        env = environ(
            REQUEST_METHOD="GET", PATH_INFO="/orders", QUERY_STRING="token=secret"
        )
        with self.assertLogs(_LOGGER_NAME, level="INFO") as cm:
            body = b"".join(middleware(env, recording_start_response()))
        self.assertEqual(b"ok", body)
        self.assertEqual(1, len(cm.records))
        record = cm.records[0]
        self.assertEqual(logging.INFO, record.levelno)
        self.assertEqual("http.server.request", record.msg)
        self.assertEqual("GET", record.__dict__["http.request.method"])
        self.assertEqual("/orders", record.__dict__["url.path"])
        self.assertEqual(200, record.__dict__["http.response.status_code"])
        self.assertIsInstance(record.__dict__["event.duration"], int)
        self.assertGreaterEqual(record.__dict__["event.duration"], 0)
        self.assertNotIn("url.query", record.__dict__)

    def test_enabled_emits_one_error_event_on_unhandled_exception_and_reraises(self):
        middleware = WSGIMiddleware(_raising_wsgi_app, log_requests=True)
        with (
            self.assertLogs(_LOGGER_NAME, level="ERROR") as cm,
            self.assertRaises(ValueError),
        ):
            middleware(environ(), recording_start_response())
        self.assertEqual(1, len(cm.records))
        record = cm.records[0]
        self.assertEqual(logging.ERROR, record.levelno)
        self.assertIsNotNone(record.exc_info)
        self.assertIsNone(record.__dict__["http.response.status_code"])
        self.assertNotIn("url.query", record.__dict__)

    def test_enabled_emits_only_after_the_streaming_body_is_fully_consumed(self):
        middleware = WSGIMiddleware(_streaming_wsgi_app, log_requests=True)
        with self.assertNoLogs(_LOGGER_NAME, level="INFO"):
            iterable = middleware(environ(), recording_start_response())
        with self.assertLogs(_LOGGER_NAME, level="INFO") as cm:
            self.assertEqual([b"a", b"b"], list(iterable))
        self.assertEqual(1, len(cm.records))


class ASGILogRequestsTests(unittest.TestCase):
    """Proves: HTM-007"""

    def test_enabled_emits_one_info_event_with_the_documented_fields(self):
        middleware = ASGIMiddleware(_ok_asgi_app, log_requests=True)
        scope = http_scope(path="/orders", method="GET")
        scope["query_string"] = b"token=secret"
        with self.assertLogs(_LOGGER_NAME, level="INFO") as cm:
            sent = asyncio.run(call_asgi(middleware, scope))
        body = next(m["body"] for m in sent if m["type"] == "http.response.body")
        self.assertEqual(b"ok", body)
        self.assertEqual(1, len(cm.records))
        record = cm.records[0]
        self.assertEqual(logging.INFO, record.levelno)
        self.assertEqual("GET", record.__dict__["http.request.method"])
        self.assertEqual("/orders", record.__dict__["url.path"])
        self.assertEqual(200, record.__dict__["http.response.status_code"])
        self.assertIsInstance(record.__dict__["event.duration"], int)
        self.assertNotIn("url.query", record.__dict__)

    def test_enabled_emits_one_error_event_on_unhandled_exception_and_reraises(self):
        middleware = ASGIMiddleware(_raising_asgi_app, log_requests=True)
        with (
            self.assertLogs(_LOGGER_NAME, level="ERROR") as cm,
            self.assertRaises(ValueError),
        ):
            asyncio.run(call_asgi(middleware, http_scope()))
        self.assertEqual(1, len(cm.records))
        record = cm.records[0]
        self.assertEqual(logging.ERROR, record.levelno)
        self.assertIsNotNone(record.exc_info)
        self.assertIsNone(record.__dict__["http.response.status_code"])


class RequestEventDurationTests(unittest.TestCase):
    """Proves: HTM-007"""

    def test_duration_uses_a_monotonic_clock(self):
        middleware = WSGIMiddleware(_ok_wsgi_app, log_requests=True)
        with (
            mock.patch(
                "semlog._middleware.time.perf_counter_ns",
                side_effect=[1_000_000, 1_250_000],
            ),
            self.assertLogs(_LOGGER_NAME, level="INFO") as cm,
        ):
            list(middleware(environ(), recording_start_response()))
        self.assertEqual(250_000, cm.records[0].__dict__["event.duration"])


class RequestEventNeverBreaksTheAppTests(unittest.TestCase):
    """Proves: HTM-007"""

    def test_wsgi_success_path_survives_a_logging_failure(self):
        middleware = WSGIMiddleware(_ok_wsgi_app, log_requests=True)
        with mock.patch(
            "semlog._middleware._REQUEST_LOGGER.log", side_effect=RuntimeError("boom")
        ):
            body = b"".join(middleware(environ(), recording_start_response()))
        self.assertEqual(b"ok", body)

    def test_wsgi_exception_path_still_reraises_despite_a_logging_failure(self):
        middleware = WSGIMiddleware(_raising_wsgi_app, log_requests=True)
        with (
            mock.patch(
                "semlog._middleware._REQUEST_LOGGER.log",
                side_effect=RuntimeError("boom"),
            ),
            self.assertRaises(ValueError),
        ):
            middleware(environ(), recording_start_response())

    def test_asgi_success_path_survives_a_logging_failure(self):
        middleware = ASGIMiddleware(_ok_asgi_app, log_requests=True)
        with mock.patch(
            "semlog._middleware._REQUEST_LOGGER.log", side_effect=RuntimeError("boom")
        ):
            sent = asyncio.run(call_asgi(middleware, http_scope()))
        body = next(m["body"] for m in sent if m["type"] == "http.response.body")
        self.assertEqual(b"ok", body)

    def test_asgi_exception_path_still_reraises_despite_a_logging_failure(self):
        middleware = ASGIMiddleware(_raising_asgi_app, log_requests=True)
        with (
            mock.patch(
                "semlog._middleware._REQUEST_LOGGER.log",
                side_effect=RuntimeError("boom"),
            ),
            self.assertRaises(ValueError),
        ):
            asyncio.run(call_asgi(middleware, http_scope()))


if __name__ == "__main__":
    unittest.main()
