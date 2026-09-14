"""Request-scoped isolation tests, WSGI and ASGI (STANDARDS HTM-006, task
3.5/3.6).

Two requests handled interleaved by the same worker (a WSGI generator
manually stepped in alternation, and two ASGI coroutines interleaved via a
controlled `asyncio.Event`) must each log only their own trace context,
with zero cross-request leakage. Deterministic: no sleep-based races.

Confirmation-only, like task 2.40 (engram #188): both middlewares (tasks
3.1-3.4) already bind a fresh `contextvars.Context`/copied task context per
request and never mutate the calling thread's own context, so these tests
pass on first run with no production code change -- they pin that
correctness down as a regression guard.
"""

from __future__ import annotations

import asyncio
import unittest

from semlog._context import current
from semlog._middleware import ASGIMiddleware, WSGIMiddleware

from ._asgi_support import http_scope
from ._wsgi_support import environ, recording_start_response


def _streaming_app(environ, start_response):
    start_response("200 OK", [])

    def body():
        yield (current().trace_id or "").encode("ascii")
        yield (current().trace_id or "").encode("ascii")

    return body()


class WSGIRequestScopedIsolationTests(unittest.TestCase):
    """Proves: HTM-006"""

    def test_two_interleaved_requests_on_the_same_worker_do_not_leak(self):
        middleware = WSGIMiddleware(_streaming_app)
        env_a = environ(
            headers={"traceparent": "00-" + "a" * 32 + "-" + "1" * 16 + "-01"}
        )
        env_b = environ(
            headers={"traceparent": "00-" + "b" * 32 + "-" + "2" * 16 + "-01"}
        )

        it_a = iter(middleware(env_a, recording_start_response()))
        it_b = iter(middleware(env_b, recording_start_response()))

        # Deterministic manual interleaving on one worker: A, B, A, B.
        first_a = next(it_a)
        first_b = next(it_b)
        second_a = next(it_a)
        second_b = next(it_b)

        self.assertEqual((b"a" * 32, b"a" * 32), (first_a, second_a))
        self.assertEqual((b"b" * 32, b"b" * 32), (first_b, second_b))
        self.assertIsNone(current())  # the real worker thread was never touched


class ASGIRequestScopedIsolationTests(unittest.TestCase):
    """Proves: HTM-006"""

    def test_two_interleaved_tasks_on_the_same_worker_do_not_leak(self):
        step_a = asyncio.Event()
        step_b = asyncio.Event()
        seen = {}

        async def app_a(scope, receive, send):
            seen["a_first"] = current().trace_id
            step_a.set()
            await step_b.wait()
            seen["a_second"] = current().trace_id

        async def app_b(scope, receive, send):
            await step_a.wait()
            seen["b_first"] = current().trace_id
            step_b.set()
            seen["b_second"] = current().trace_id

        middleware_a = ASGIMiddleware(app_a)
        middleware_b = ASGIMiddleware(app_b)
        scope_a = http_scope(
            headers={"traceparent": "00-" + "a" * 32 + "-" + "1" * 16 + "-01"}
        )
        scope_b = http_scope(
            headers={"traceparent": "00-" + "b" * 32 + "-" + "2" * 16 + "-01"}
        )

        async def run_both():
            await asyncio.gather(
                middleware_a(scope_a, lambda: None, _noop_send),
                middleware_b(scope_b, lambda: None, _noop_send),
            )

        asyncio.run(run_both())

        self.assertEqual("a" * 32, seen["a_first"])
        self.assertEqual("a" * 32, seen["a_second"])
        self.assertEqual("b" * 32, seen["b_first"])
        self.assertEqual("b" * 32, seen["b_second"])


async def _noop_send(message):
    return None


if __name__ == "__main__":
    unittest.main()
