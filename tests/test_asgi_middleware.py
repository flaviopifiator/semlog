"""`ASGIMiddleware` tests, driven only through a hand-written `scope`/
`receive`/`send` caller on `asyncio.run` (CP-011).

Proves HTM-002 (pure ASGI 3.0, no `BaseHTTPMiddleware` dependency),
HTM-003's ASGI half (context stays bound across every streamed
`http.response.body` message), HTM-004's ASGI half (an app exception
propagates unchanged and context still resets), and HTM-005 (`lifespan`
passes through untouched, no header parsing, no raise).
"""

from __future__ import annotations

import asyncio
import unittest

from semlog._context import current
from semlog._middleware import ASGIMiddleware

from ._asgi_support import call_asgi, http_scope


async def _echo_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200})
    trace_id = (current().trace_id or "").encode("ascii")
    await send({"type": "http.response.body", "body": trace_id})


async def _streaming_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200})
    for more_body in (True, True, False):
        trace_id = (current().trace_id or "").encode("ascii")
        await send(
            {"type": "http.response.body", "body": trace_id, "more_body": more_body}
        )


async def _raising_app(scope, receive, send):
    raise ValueError("boom")


async def _lifespan_app(scope, receive, send):
    message = await receive()
    if message["type"] == "lifespan.startup":
        await send({"type": "lifespan.startup.complete"})
    message = await receive()
    if message["type"] == "lifespan.shutdown":
        await send({"type": "lifespan.shutdown.complete"})


class ASGIMiddlewareTests(unittest.TestCase):
    """Proves: HTM-002, CP-011"""

    def test_valid_traceparent_binds_the_inbound_trace_id(self):
        middleware = ASGIMiddleware(_echo_app)
        scope = http_scope(
            headers={
                "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
            }
        )
        sent = asyncio.run(call_asgi(middleware, scope))
        body = next(m["body"] for m in sent if m["type"] == "http.response.body")
        self.assertEqual(b"4bf92f3577b34da6a3ce929d0e0e4736", body)

    def test_absent_traceparent_generates_a_fresh_trace(self):
        middleware = ASGIMiddleware(_echo_app)
        sent = asyncio.run(call_asgi(middleware, http_scope()))
        body = next(m["body"] for m in sent if m["type"] == "http.response.body")
        self.assertEqual(32, len(body))

    def test_middleware_module_never_imports_starlette(self):
        import ast
        from pathlib import Path

        import semlog._middleware as middleware_module

        source = Path(middleware_module.__file__).read_text(encoding="utf-8")
        names = {
            alias.name.split(".")[0]
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module.split(".")[0]
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertNotIn("starlette", names)

    def test_context_reset_after_the_call(self):
        middleware = ASGIMiddleware(_echo_app)
        asyncio.run(call_asgi(middleware, http_scope()))
        self.assertIsNone(current())


class ASGIStreamingContextTests(unittest.TestCase):
    """ASGI half of HTM-003 -- combined with the WSGI half
    (`tests/test_wsgi_middleware.py::WSGIStreamingContextTests`), this
    completes the requirement; see `ASGICompletionTests` below."""

    def test_context_stays_bound_across_every_streamed_message(self):
        middleware = ASGIMiddleware(_streaming_app)
        scope = http_scope(
            headers={"traceparent": "00-" + "a" * 32 + "-" + "1" * 16 + "-01"}
        )
        sent = asyncio.run(call_asgi(middleware, scope))
        bodies = [m["body"] for m in sent if m["type"] == "http.response.body"]
        self.assertEqual([b"a" * 32] * 3, bodies)


class ASGIExceptionPropagationTests(unittest.TestCase):
    """ASGI half of HTM-004; see `ASGICompletionTests` below."""

    def test_app_exception_propagates_unchanged_and_context_still_resets(self):
        middleware = ASGIMiddleware(_raising_app)
        with self.assertRaises(ValueError):
            asyncio.run(call_asgi(middleware, http_scope()))
        self.assertIsNone(current())


class ASGICompletionTests(unittest.TestCase):
    """Proves: HTM-003, HTM-004

    Both requirements need their WSGI half (proven in
    `tests/test_wsgi_middleware.py`) and their ASGI half (proven above in
    this file) together; this class exists only to carry the completing
    `Proves:` line once both halves exist, matching the LRC-013/CP-016
    precedent (engram #188)."""

    def test_wsgi_and_asgi_halves_both_exist(self):
        # A structural regression guard, not new behavior: both halves live
        # in their own test classes above / in test_wsgi_middleware.py.
        from . import test_wsgi_middleware

        self.assertTrue(hasattr(test_wsgi_middleware, "WSGIStreamingContextTests"))
        self.assertTrue(hasattr(test_wsgi_middleware, "WSGIExceptionPropagationTests"))


class ASGILifespanTests(unittest.TestCase):
    """Proves: HTM-005"""

    def test_lifespan_events_pass_through_untouched(self):
        middleware = ASGIMiddleware(_lifespan_app)
        scope = {"type": "lifespan"}
        messages_in = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
        sent = asyncio.run(call_asgi(middleware, scope, messages_in))
        types = [m["type"] for m in sent]
        self.assertEqual(
            ["lifespan.startup.complete", "lifespan.shutdown.complete"], types
        )

    def test_lifespan_does_not_bind_or_parse_headers(self):
        seen = {}

        async def app(scope, receive, send):
            seen["snapshot"] = current()
            await receive()

        middleware = ASGIMiddleware(app)
        scope = {"type": "lifespan"}
        asyncio.run(call_asgi(middleware, scope, [{"type": "lifespan.startup"}]))
        self.assertIsNone(seen["snapshot"])


if __name__ == "__main__":
    unittest.main()
