"""`WSGIMiddleware` tests, driven only through stdlib `wsgiref` (CP-010).

Proves HTM-001 (PEP 3333-conformant: parses inbound trace/baggage headers,
binds context, generates a request id, resets only after the response
iterable is fully consumed), HTM-003's WSGI half (context stays bound
through a generator-based streaming body), and HTM-004's WSGI half (an app
exception propagates unchanged and context still resets for the next
request on the same thread).
"""

from __future__ import annotations

import unittest
from wsgiref.validate import validator

from semlog._context import current
from semlog._middleware import WSGIMiddleware

from ._wsgi_support import environ, recording_start_response


def _echo_app(environ, start_response):
    start_response("200 OK", [("Content-Type", "text/plain")])
    snapshot = current()
    return [(snapshot.trace_id or "").encode("ascii")]


def _streaming_app(environ, start_response):
    start_response("200 OK", [("Content-Type", "text/plain")])

    def body():
        yield (current().trace_id or "").encode("ascii")
        yield (current().trace_id or "").encode("ascii")

    return body()


def _raising_app(environ, start_response):
    raise ValueError("boom")


class WSGIMiddlewareTests(unittest.TestCase):
    """Proves: HTM-001, CP-010"""

    def test_valid_traceparent_binds_the_inbound_trace_id(self):
        middleware = WSGIMiddleware(_echo_app)
        env = environ(
            headers={
                "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
            }
        )
        start_response = recording_start_response()
        body = b"".join(middleware(env, start_response))
        self.assertEqual(b"4bf92f3577b34da6a3ce929d0e0e4736", body)
        self.assertEqual("200 OK", start_response.calls[0][0])

    def test_absent_traceparent_generates_a_fresh_trace(self):
        middleware = WSGIMiddleware(_echo_app)
        env = environ()
        start_response = recording_start_response()
        body = b"".join(middleware(env, start_response))
        self.assertEqual(32, len(body))

    def test_request_id_is_generated_and_bound(self):
        seen = {}

        def app(environ, start_response):
            start_response("200 OK", [])
            seen["request_id"] = current().request_id
            return [b""]

        middleware = WSGIMiddleware(app)
        list(middleware(environ(), recording_start_response()))
        self.assertIsNotNone(seen["request_id"])

    def test_context_reset_after_the_call_on_the_same_thread(self):
        middleware = WSGIMiddleware(_echo_app)
        list(middleware(environ(), recording_start_response()))
        self.assertIsNone(current())

    def test_via_wsgiref_validator_pep3333_conformance(self):
        validated = validator(WSGIMiddleware(_echo_app))
        iterable = validated(environ(), recording_start_response())
        body = b"".join(iterable)
        iterable.close()
        self.assertEqual(32, len(body))


class WSGIStreamingContextTests(unittest.TestCase):
    """WSGI half of HTM-003 (streaming coverage); the class deliberately
    carries no `Proves:` tag yet -- HTM-003 also requires the ASGI half
    (task 3.3/3.4) before it is fully proven, matching the LRC-013/CP-016
    precedent in engram #188."""

    def test_context_stays_bound_through_every_chunk_of_a_streaming_body(self):
        middleware = WSGIMiddleware(_streaming_app)
        env = environ(
            headers={"traceparent": "00-" + "a" * 32 + "-" + "1" * 16 + "-01"}
        )
        iterable = middleware(env, recording_start_response())
        it = iter(iterable)
        first, second = next(it), next(it)
        with self.assertRaises(StopIteration):
            next(it)  # the generator itself calls close() in its `finally`
        self.assertEqual((b"a" * 32, b"a" * 32), (first, second))

    def test_reset_does_not_leak_into_a_later_request_on_the_same_thread(self):
        streaming = WSGIMiddleware(_streaming_app)
        env = environ(
            headers={"traceparent": "00-" + "a" * 32 + "-" + "1" * 16 + "-01"}
        )
        list(streaming(env, recording_start_response()))  # fully consumed

        later = WSGIMiddleware(_echo_app)
        body = b"".join(later(environ(), recording_start_response()))
        self.assertNotEqual(b"a" * 32, body)  # a fresh, unrelated trace
        self.assertIsNone(current())


class WSGIExceptionPropagationTests(unittest.TestCase):
    """WSGI half of HTM-004; no `Proves:` tag yet for the same reason as
    `WSGIStreamingContextTests` above -- the ASGI half completes it."""

    def test_app_exception_propagates_unchanged_and_context_still_resets(self):
        middleware = WSGIMiddleware(_raising_app)
        with self.assertRaises(ValueError):
            middleware(environ(), recording_start_response())
        self.assertIsNone(current())

        # The next request on the same thread must start clean.
        clean_middleware = WSGIMiddleware(_echo_app)
        body = b"".join(clean_middleware(environ(), recording_start_response()))
        self.assertEqual(32, len(body))


if __name__ == "__main__":
    unittest.main()
