"""Compatibility matrix: Django, the floor row and both latest rows
(STANDARDS.md CP-002, section 11; design #162 section 9; design-decisions
#170 section 9b; spec #176 CP-002 revision 3).

Skips gracefully, with an explicit reason, when `django` is not
installed, so `python -m unittest discover` stays stdlib-only
(CP-008/CP-013). The matrix itself is proven by running this exact module
inside throwaway virtual environments pinned to each row's exact
Django/Python combination:

- floor: Django 3.2.9, Python 3.10 only.
- latest: Django 5.2 LTS, Python 3.10-3.14; Django 6.1, Python 3.12-3.14.

See the sdd-apply evidence (engram `sdd/structured-logging-standard/
apply-progress`) for the per-cell results table.

Proves: CP-002 -- a sync view served over WSGI (wrapped by
`semlog.WSGIMiddleware`), an async view served over ASGI, and a sync view
served over ASGI (Django's own `sync_to_async` offload), all wrapped by
`semlog.ASGIMiddleware` where applicable, all propagate the inbound W3C
trace context; each logged call emits exactly one JSON line; and two
sequential requests carrying different trace ids never leak context into
each other.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import threading
import unittest
from unittest import mock

try:
    import django
except ImportError:  # pragma: no cover -- exercised only without django installed
    django = None

from semlog import _transport
from semlog._middleware import ASGIMiddleware, WSGIMiddleware

from ._matrix_support import call_asgi_app
from ._pipeline_support import FakeStdout, reset_pipeline
from ._wsgi_support import environ, recording_start_response

_TRACE_A = "00-" + "a" * 32 + "-" + "1" * 16 + "-01"
_TRACE_B = "00-" + "b" * 32 + "-" + "2" * 16 + "-01"


def _configure_django_once():
    from django.conf import settings

    if not settings.configured:
        settings.configure(
            SECRET_KEY="matrix-test-key",
            DEBUG=True,
            ALLOWED_HOSTS=["*"],
            ROOT_URLCONF="tests._django_matrix_urls",
            DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
            USE_TZ=True,
        )
        django.setup()


def _django_wsgi_app():
    from django.core.handlers.wsgi import WSGIHandler

    _configure_django_once()
    return WSGIHandler()


def _django_asgi_app():
    from django.core.handlers.asgi import ASGIHandler

    _configure_django_once()
    return ASGIHandler()


@unittest.skipIf(django is None, "django is not installed in this environment")
class DjangoWSGIMatrixTests(unittest.TestCase):
    """Proves: CP-002"""

    def setUp(self):
        self.fake_stdout = FakeStdout()
        self._stdout_patch = mock.patch("sys.stdout", self.fake_stdout)
        self._stdout_patch.start()
        with mock.patch.dict("os.environ", {}, clear=True):
            from semlog import configure

            configure(service_name="django-matrix-wsgi", search_dir=".")
        self.app = WSGIMiddleware(_django_wsgi_app())

    def tearDown(self):
        reset_pipeline()
        self._stdout_patch.stop()

    def _lines(self):
        _transport.flush(timeout=2)
        raw = self.fake_stdout.buffer.getvalue().decode("utf-8")
        return [json.loads(line) for line in raw.strip().splitlines() if line]

    def _get(self, path, traceparent):
        env = environ(headers={"traceparent": traceparent}, PATH_INFO=path)
        start_response = recording_start_response()
        body = b"".join(self.app(env, start_response))
        return start_response.calls[0][0], body

    def test_sync_view_over_wsgi_propagates_inbound_trace_context(self):
        status, _body = self._get("/sync/", _TRACE_A)
        self.assertEqual("200 OK", status)
        lines = self._lines()
        self.assertEqual(1, len(lines))
        self.assertEqual("a" * 32, lines[0]["trace_id"])
        self.assertEqual("matrix.sync_view", lines[0]["event_name"])

    def test_two_sequential_wsgi_requests_do_not_leak_trace_context(self):
        self._get("/sync/", _TRACE_A)
        self._get("/sync/", _TRACE_B)
        lines = self._lines()
        self.assertEqual(2, len(lines))
        self.assertEqual("a" * 32, lines[0]["trace_id"])
        self.assertEqual("b" * 32, lines[1]["trace_id"])


@unittest.skipIf(django is None, "django is not installed in this environment")
class DjangoASGIMatrixTests(unittest.TestCase):
    """Proves: CP-002"""

    def setUp(self):
        self.fake_stdout = FakeStdout()
        self._stdout_patch = mock.patch("sys.stdout", self.fake_stdout)
        self._stdout_patch.start()
        with mock.patch.dict("os.environ", {}, clear=True):
            from semlog import configure

            configure(service_name="django-matrix-asgi", search_dir=".")
        self.app = ASGIMiddleware(_django_asgi_app())

    def tearDown(self):
        reset_pipeline()
        self._stdout_patch.stop()

    def _lines(self):
        _transport.flush(timeout=2)
        raw = self.fake_stdout.buffer.getvalue().decode("utf-8")
        return [json.loads(line) for line in raw.strip().splitlines() if line]

    def _get(self, path, traceparent):
        scope = {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [(b"traceparent", traceparent.encode("ascii"))],
            "query_string": b"",
        }
        sent = asyncio.run(call_asgi_app(self.app, scope))
        return next(m["status"] for m in sent if m["type"] == "http.response.start")

    def test_async_view_over_asgi_propagates_inbound_trace_context(self):
        status = self._get("/async/", _TRACE_A)
        self.assertEqual(200, status)
        lines = self._lines()
        self.assertEqual(1, len(lines))
        self.assertEqual("a" * 32, lines[0]["trace_id"])
        self.assertEqual("matrix.async_view", lines[0]["event_name"])

    def test_sync_view_over_asgi_propagates_inbound_trace_context(self):
        # Django's `sync_to_async(thread_sensitive=True)` offload path.
        status = self._get("/sync/", _TRACE_A)
        self.assertEqual(200, status)
        lines = self._lines()
        self.assertEqual(1, len(lines))
        self.assertEqual("a" * 32, lines[0]["trace_id"])
        self.assertEqual("matrix.sync_view", lines[0]["event_name"])

    def test_two_sequential_asgi_requests_do_not_leak_trace_context(self):
        self._get("/async/", _TRACE_A)
        self._get("/sync/", _TRACE_B)
        lines = self._lines()
        self.assertEqual(2, len(lines))
        self.assertEqual("a" * 32, lines[0]["trace_id"])
        self.assertEqual("b" * 32, lines[1]["trace_id"])


class _DjangoMiddlewareTestCase(unittest.TestCase):
    """Shared harness for the `DjangoMiddleware`-specific classes below
    (HTM-008..013): a fresh `configure()` with stdout capture, and an
    `override_settings(MIDDLEWARE=...)` override enabled per test, since
    each class needs to vary the registered middleware list independently
    (`settings.configure()` runs exactly once per process)."""

    middleware = ("semlog.DjangoMiddleware",)
    service_name = "django-middleware"

    def setUp(self):
        _configure_django_once()
        from django.test import override_settings

        self._override = override_settings(MIDDLEWARE=list(self.middleware))
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.fake_stdout = FakeStdout()
        self._stdout_patch = mock.patch("sys.stdout", self.fake_stdout)
        self._stdout_patch.start()
        with mock.patch.dict("os.environ", {}, clear=True):
            from semlog import configure

            configure(service_name=self.service_name, search_dir=".")

    def tearDown(self):
        reset_pipeline()
        self._stdout_patch.stop()

    def _lines(self):
        _transport.flush(timeout=2)
        raw = self.fake_stdout.buffer.getvalue().decode("utf-8")
        return [json.loads(line) for line in raw.strip().splitlines() if line]

    def _wsgi_app(self):
        from django.core.handlers.wsgi import WSGIHandler

        return WSGIHandler()

    def _asgi_app(self):
        from django.core.handlers.asgi import ASGIHandler

        return ASGIHandler()

    def _wsgi_get(self, path, traceparent=None, method="GET"):
        headers = {"traceparent": traceparent} if traceparent else {}
        env = environ(headers=headers, PATH_INFO=path, REQUEST_METHOD=method)
        start_response = recording_start_response()
        body = b"".join(self._wsgi_app()(env, start_response))
        return start_response.calls[0][0], body

    def _asgi_get(self, path, traceparent=None, method="GET"):
        headers = [(b"traceparent", traceparent.encode("ascii"))] if traceparent else []
        scope = {
            "type": "http",
            "method": method,
            "path": path,
            "headers": headers,
            "query_string": b"",
        }
        sent = asyncio.run(call_asgi_app(self._asgi_app(), scope))
        status = next(m["status"] for m in sent if m["type"] == "http.response.start")
        body = b"".join(
            m.get("body", b"") for m in sent if m["type"] == "http.response.body"
        )
        return status, body

    def _request(self, path="/sync/", traceparent=None):
        from django.test import RequestFactory

        extra = {"HTTP_TRACEPARENT": traceparent} if traceparent else {}
        return RequestFactory().get(path, **extra)


@unittest.skipIf(django is None, "django is not installed in this environment")
class DjangoMiddlewareRegistrationTests(_DjangoMiddlewareTestCase):
    """Registers the class through a dotted path in `settings.MIDDLEWARE`;
    a sync view served over WSGI propagates the inbound W3C trace context,
    and two sequential requests never leak context into each other. No
    `Proves:` line yet: the citation waits for STANDARDS.md's own
    HTM-008 entry (phase 5)."""

    def test_dotted_path_in_middleware_binds_a_sync_view_over_wsgi(self):
        status, _body = self._wsgi_get("/sync/", _TRACE_A)
        self.assertEqual("200 OK", status)
        lines = self._lines()
        self.assertEqual(1, len(lines))
        self.assertEqual("a" * 32, lines[0]["trace_id"])
        self.assertEqual("matrix.sync_view", lines[0]["event_name"])

    def test_two_sequential_requests_do_not_leak_trace_context(self):
        self._wsgi_get("/sync/", _TRACE_A)
        self._wsgi_get("/sync/", _TRACE_B)
        lines = self._lines()
        self.assertEqual(2, len(lines))
        self.assertEqual("a" * 32, lines[0]["trace_id"])
        self.assertEqual("b" * 32, lines[1]["trace_id"])


def _is_coroutine_like(obj):
    """The same coroutine-function check Django's own dispatch uses
    (`asgiref.sync.iscoroutinefunction`, mirrored here without importing
    asgiref): plain `inspect.iscoroutinefunction` alone only recognizes
    `markcoroutinefunction`'s marker on Python 3.12+, so a portable check
    must also fall back to `asyncio.iscoroutinefunction` on 3.10/3.11,
    which is the version that understands the `_is_coroutine` sentinel."""
    if inspect.iscoroutinefunction(obj):
        return True
    import asyncio

    return asyncio.iscoroutinefunction(obj)


@unittest.skipIf(django is None, "django is not installed in this environment")
class DjangoMiddlewareAsyncMarkingTests(_DjangoMiddlewareTestCase):
    """An async `get_response` marks the instance coroutine-like, a sync
    one leaves it unmarked, and `__acall__` runs on the same thread that
    started the event loop -- no `sync_to_async` hop. No `Proves:` line
    yet: the citation waits for STANDARDS.md's own HTM-008 entry
    (phase 5)."""

    def test_async_get_response_marks_the_instance_as_coroutine_like(self):
        from semlog import DjangoMiddleware

        async def get_response(request):
            return None

        mw = DjangoMiddleware(get_response)
        self.assertTrue(mw.async_mode)
        self.assertTrue(_is_coroutine_like(mw))

    def test_sync_get_response_leaves_the_instance_unmarked(self):
        from semlog import DjangoMiddleware

        mw = DjangoMiddleware(lambda request: None)
        self.assertFalse(mw.async_mode)
        self.assertFalse(_is_coroutine_like(mw))

    def test_acall_runs_on_the_event_loop_thread_with_no_sync_to_async_hop(self):
        loop_thread = threading.get_ident()
        status, body = self._asgi_get("/async-thread/")
        self.assertEqual(200, status)
        payload = json.loads(body)
        self.assertEqual(loop_thread, payload["thread_ident"])

    def test_sync_post_processing_of_the_async_response_does_not_raise(self):
        # `response.status_code` is read synchronously right after `await
        # get_response(...)` returns; a real thread hop would make this
        # attribute access explode (design D1, probe check 1c).
        status, _body = self._asgi_get("/async/")
        self.assertEqual(200, status)


@unittest.skipIf(django is None, "django is not installed in this environment")
class DjangoMiddlewareModeTests(_DjangoMiddlewareTestCase):
    """Proves: LM-005

    `off` mode binds nothing and the wrapped view still runs, matching
    the existing WSGI/ASGI guarantee; `full`/`hybrid` still bind the
    inbound trace id. The Django-specific request event's own off-mode
    parity (HTM-011) has no dedicated test yet: it lands with the event
    itself in a later work unit, and has no `Proves:` citation yet
    either, pending STANDARDS.md's own HTM-011 entry (phase 5)."""

    def test_off_mode_binds_nothing_and_still_calls_the_view(self):
        from django.http import HttpResponse

        from semlog import DjangoMiddleware, _modes
        from semlog._context import current

        _modes.state.mode = "off"
        try:
            seen = {}

            def get_response(request):
                seen["current"] = current()
                return HttpResponse("ok")

            mw = DjangoMiddleware(get_response)
            response = mw(self._request())
            self.assertEqual(b"ok", response.content)
            self.assertIsNone(seen["current"])
        finally:
            _modes.state.mode = "full"

    def test_full_mode_binds_the_inbound_trace_id(self):
        from django.http import HttpResponse

        from semlog import DjangoMiddleware
        from semlog._context import current

        seen = {}

        def get_response(request):
            seen["trace_id"] = current().trace_id
            return HttpResponse("ok")

        mw = DjangoMiddleware(get_response)
        mw(self._request(traceparent=_TRACE_A))
        self.assertEqual("a" * 32, seen["trace_id"])

    def test_hybrid_mode_still_binds_context_like_full_mode(self):
        from django.http import HttpResponse

        from semlog import DjangoMiddleware, _modes
        from semlog._context import current

        _modes.state.mode = "hybrid"
        try:
            seen = {}

            def get_response(request):
                seen["trace_id"] = current().trace_id
                return HttpResponse("ok")

            mw = DjangoMiddleware(get_response)
            mw(self._request(traceparent=_TRACE_A))
            self.assertEqual("a" * 32, seen["trace_id"])
        finally:
            _modes.state.mode = "full"


if __name__ == "__main__":
    unittest.main()
