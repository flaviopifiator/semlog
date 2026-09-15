"""`off` mode tests: as if semlog were not installed at all, except that
`semlog=True` never raises (LM-005). No handler is installed and the
root logger is left completely untouched. `operation()` still works as
a context manager and `bind()` returns without effect; every internal
diagnostic is silent, including the out-of-scope `bind()` warning
(TCP-012). `http.server.request` (HTM-007) is covered in `test_hybrid.py`
and `test_log_requests.py`; this module covers the off-specific half
already landed in an earlier work unit's `_middleware.py` change.

No `Proves:` line yet: LM-005 is not declared in STANDARDS.md until
Phase 7 of this change (same deferred-citation precedent as
`test_class_budget.py`/`test_source_budget.py`); tag the relevant
classes below `Proves: LM-005` once that declaration lands.
"""

from __future__ import annotations

import io
import logging
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from semlog import _modes, _transport
from semlog._config import configure
from semlog._context import bind, current, inject, operation
from semlog._middleware import ASGIMiddleware, WSGIMiddleware

from ._asgi_support import call_asgi, http_scope
from ._pipeline_support import reset_pipeline
from ._wsgi_support import environ, recording_start_response


def _reset_state():
    reset_pipeline()
    _modes.state.mode = "full"
    _modes.state.marking = False


def _off_configure():
    with mock.patch.dict("os.environ", {}, clear=True):
        configure(mode="off", service_name="svc", search_dir=".")


class OffPassThroughTests(unittest.TestCase):
    def tearDown(self):
        _reset_state()

    def test_no_handler_installed_and_root_untouched(self):
        root = logging.getLogger()
        sentinel = logging.StreamHandler(io.StringIO())
        root.addHandler(sentinel)
        root.setLevel(logging.WARNING)
        try:
            _off_configure()
            self.assertEqual([sentinel], root.handlers)
            self.assertEqual(logging.WARNING, root.level)
            self.assertIsNone(_transport._state.handler)
        finally:
            root.removeHandler(sentinel)
            root.setLevel(logging.WARNING)

    def test_operation_works_as_a_context_manager_with_no_binding(self):
        _off_configure()
        with operation() as snapshot:
            self.assertIsNone(snapshot.trace_id)
            self.assertIsNone(current())  # never actually bound
        self.assertIsNone(current())

    def test_operation_with_headers_still_works_and_binds_nothing(self):
        _off_configure()
        headers = {
            "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        }
        with operation(headers=headers) as snapshot:
            self.assertIsNone(snapshot.trace_id)
            self.assertIsNone(current())

    def test_bind_returns_without_effect_inside_an_operation(self):
        _off_configure()
        with operation():
            bind({"app.a": 1})  # must not raise
            self.assertIsNone(current())

    def test_bind_outside_any_scope_does_not_raise(self):
        _off_configure()
        bind({"app.a": 1})  # must not raise
        self.assertIsNone(current())

    def test_wsgi_middleware_passes_through_and_calls_the_app(self):
        _off_configure()

        def app(environ, start_response):
            start_response("200 OK", [("Content-Type", "text/plain")])
            return [b"ok"]

        middleware = WSGIMiddleware(app)
        body = b"".join(middleware(environ(), recording_start_response()))
        self.assertEqual(b"ok", body)

    def test_asgi_middleware_passes_through_and_calls_the_app(self):
        import asyncio

        _off_configure()

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200})
            await send({"type": "http.response.body", "body": b"ok"})

        middleware = ASGIMiddleware(app)
        sent = asyncio.run(call_asgi(middleware, http_scope()))
        body = next(m["body"] for m in sent if m["type"] == "http.response.body")
        self.assertEqual(b"ok", body)


_INBOUND_TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


class OffMiddlewareScopeTests(unittest.TestCase):
    """MAJOR M1 (validate-wu25 #333): off must behave as if semlog were
    not installed even INSIDE a request handled by either middleware --
    no header parsing, no trace/span/request id minting, no pushed scope
    -- so application code calling `inject()`/`bind()` during the request
    sees exactly the same inert behavior as outside any middleware at
    all, and the middleware never writes a semlog-generated `traceparent`
    into outbound headers. Covers both an inbound `traceparent` and none,
    since a scope was previously pushed either way."""

    def tearDown(self):
        _reset_state()

    def test_wsgi_inject_and_bind_are_inert_without_an_inbound_header(self):
        _off_configure()
        seen = {}

        def app(wsgi_environ, start_response):
            start_response("200 OK", [])
            bind({"app.a": 1})
            seen["current"] = current()
            headers = {}
            inject(headers)
            seen["headers"] = dict(headers)
            return [b"ok"]

        middleware = WSGIMiddleware(app)
        body = b"".join(middleware(environ(), recording_start_response()))
        self.assertEqual(b"ok", body)
        self.assertIsNone(seen["current"])
        self.assertEqual({}, seen["headers"])

    def test_wsgi_inject_and_bind_are_inert_with_an_inbound_header(self):
        _off_configure()
        seen = {}

        def app(wsgi_environ, start_response):
            start_response("200 OK", [])
            bind({"app.a": 1})
            seen["current"] = current()
            headers = {}
            inject(headers)
            seen["headers"] = dict(headers)
            return [b"ok"]

        middleware = WSGIMiddleware(app)
        env = environ(headers={"traceparent": _INBOUND_TRACEPARENT})
        body = b"".join(middleware(env, recording_start_response()))
        self.assertEqual(b"ok", body)
        self.assertIsNone(seen["current"])
        self.assertEqual({}, seen["headers"])

    def test_wsgi_exception_still_propagates_in_off_mode(self):
        _off_configure()

        def raising_app(wsgi_environ, start_response):
            raise ValueError("boom")

        middleware = WSGIMiddleware(raising_app)
        with self.assertRaises(ValueError):
            middleware(environ(), recording_start_response())

    def test_asgi_inject_and_bind_are_inert_without_an_inbound_header(self):
        import asyncio

        _off_configure()
        seen = {}

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200})
            bind({"app.a": 1})
            seen["current"] = current()
            headers = {}
            inject(headers)
            seen["headers"] = dict(headers)
            await send({"type": "http.response.body", "body": b"ok"})

        middleware = ASGIMiddleware(app)
        sent = asyncio.run(call_asgi(middleware, http_scope()))
        body = next(m["body"] for m in sent if m["type"] == "http.response.body")
        self.assertEqual(b"ok", body)
        self.assertIsNone(seen["current"])
        self.assertEqual({}, seen["headers"])

    def test_asgi_inject_and_bind_are_inert_with_an_inbound_header(self):
        import asyncio

        _off_configure()
        seen = {}

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200})
            bind({"app.a": 1})
            seen["current"] = current()
            headers = {}
            inject(headers)
            seen["headers"] = dict(headers)
            await send({"type": "http.response.body", "body": b"ok"})

        middleware = ASGIMiddleware(app)
        scope = http_scope(headers={"traceparent": _INBOUND_TRACEPARENT})
        asyncio.run(call_asgi(middleware, scope))
        self.assertIsNone(seen["current"])
        self.assertEqual({}, seen["headers"])

    def test_asgi_exception_still_propagates_in_off_mode(self):
        import asyncio

        _off_configure()

        async def raising_app(scope, receive, send):
            raise ValueError("boom")

        middleware = ASGIMiddleware(raising_app)
        with self.assertRaises(ValueError):
            asyncio.run(call_asgi(middleware, http_scope()))


class OffRequestEventTests(unittest.TestCase):
    """Proves: HTM-007

    The hybrid-mode half of HTM-007 is covered in `test_hybrid.py`."""

    def tearDown(self):
        _reset_state()

    def test_log_requests_true_emits_nothing_in_off_mode(self):
        _off_configure()

        def app(environ, start_response):
            start_response("200 OK", [("Content-Type", "text/plain")])
            return [b"ok"]

        middleware = WSGIMiddleware(app, log_requests=True)
        logger = logging.getLogger("semlog._middleware")
        with self.assertNoLogs(logger.name, level="INFO"):
            list(middleware(environ(), recording_start_response()))

    def test_log_requests_true_emits_nothing_on_a_raising_app_in_off_mode(self):
        _off_configure()

        def raising_app(environ, start_response):
            raise ValueError("boom")

        middleware = WSGIMiddleware(raising_app, log_requests=True)
        logger = logging.getLogger("semlog._middleware")
        with (
            self.assertNoLogs(logger.name, level="ERROR"),
            self.assertRaises(ValueError),
        ):
            middleware(environ(), recording_start_response())


class OffDiagnosticsTests(unittest.TestCase):
    """Proves: TCP-012

    Covers the off-mode scope; the in-scope/out-of-scope merge semantics
    are covered in `test_bind.py`."""

    def setUp(self):
        import semlog._context as context_module

        self._context_module = context_module
        self._saved_warned = context_module._bind_warned
        context_module._bind_warned = False

    def tearDown(self):
        self._context_module._bind_warned = self._saved_warned
        _reset_state()

    def test_out_of_scope_bind_emits_no_diagnostic_in_off_mode(self):
        _off_configure()
        logger = logging.getLogger("semlog")
        sentinel = []
        handler = logging.Handler()
        handler.emit = lambda record: sentinel.append(record)
        logger.addHandler(handler)
        try:
            bind({"app.a": 1})
            bind({"app.b": 2})  # a second call: still nothing, in every mode
        finally:
            logger.removeHandler(handler)
        self.assertEqual([], sentinel)


class OffByteIdentityTests(unittest.TestCase):
    """Subprocess A/B: marked and unmarked calls print byte-identical
    output in `off` mode, using `%(funcName)s`/`%(lineno)d`."""

    def test_marked_and_unmarked_output_are_byte_identical(self):
        src_dir = str(Path(__file__).resolve().parents[1] / "src")
        fmt = "%(funcName)s:%(lineno)d:%(levelname)s:%(message)s"
        script = f"""
import logging, sys
sys.path.insert(0, {src_dir!r})
logging.basicConfig(level=logging.INFO, format={fmt!r})
import semlog
semlog.configure(mode="off", service_name="svc", search_dir=".")
logger = logging.getLogger("app")


def emit(semlog_marked):
    logger.info("app.event", semlog=semlog_marked)


emit({{semlog}})
"""
        marked = subprocess.run(
            [sys.executable, "-c", script.format(semlog="True")],
            capture_output=True,
            timeout=15,
            check=False,
        )
        unmarked = subprocess.run(
            [sys.executable, "-c", script.format(semlog="False")],
            capture_output=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(0, marked.returncode, marked.stderr)
        self.assertEqual(0, unmarked.returncode, unmarked.stderr)
        self.assertEqual(unmarked.stderr, marked.stderr)
        self.assertIn(b"emit:", marked.stderr)

    def test_off_output_matches_a_process_where_semlog_was_never_imported(self):
        src_dir = str(Path(__file__).resolve().parents[1] / "src")
        fmt = "%(funcName)s:%(lineno)d:%(levelname)s:%(message)s"
        off_setup = [
            "import sys",
            f"sys.path.insert(0, {src_dir!r})",
            "import semlog",
            "semlog.configure(mode='off', service_name='svc', search_dir='.')",
        ]
        baseline_setup = [""] * len(off_setup)

        def build(setup_lines):
            lines = [
                "import logging",
                *setup_lines,
                f"logging.basicConfig(level=logging.INFO, format={fmt!r})",
                'logger = logging.getLogger("app")',
                "",
                "",
                "def emit():",
                '    logger.info("app.event")',
                "",
                "",
                "emit()",
            ]
            return "\n".join(lines) + "\n"

        baseline = subprocess.run(
            [sys.executable, "-c", build(baseline_setup)],
            capture_output=True,
            timeout=15,
            check=False,
        )
        with_semlog_off = subprocess.run(
            [sys.executable, "-c", build(off_setup)],
            capture_output=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(0, baseline.returncode, baseline.stderr)
        self.assertEqual(0, with_semlog_off.returncode, with_semlog_off.stderr)
        self.assertEqual(baseline.stderr, with_semlog_off.stderr)


if __name__ == "__main__":
    unittest.main()
