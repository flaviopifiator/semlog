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
import logging
import os
import subprocess
import sys
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


@unittest.skipIf(django is None, "django is not installed in this environment")
class DjangoMiddlewareExceptionTests(_DjangoMiddlewareTestCase):
    """`process_exception` stashes the exception without swallowing it: a
    raising view still produces Django's own 500 response, and (with the
    completion event enabled) exactly one ERROR `http.server.request`
    record carries the real final status and a rendered traceback. No
    `Proves:` line yet: waits for STANDARDS.md's own HTM-011 entry
    (phase 5)."""

    middleware = ("tests._django_matrix_middleware.django_middleware_logging",)
    service_name = "django-exception"

    def setUp(self):
        super().setUp()
        # Django's own `django.request` logger propagates to root and
        # renders the SAME exception instance first, which would consume
        # `_format._render_exception`'s once-per-instance stacktrace
        # dedup marker before semlog's own event gets a chance to. Silence
        # it here so this class proves semlog's OWN mechanism in
        # isolation, not an incidental interaction with a second,
        # unrelated logging system.
        self._django_request_logger = logging.getLogger("django.request")
        self._django_request_was_disabled = self._django_request_logger.disabled
        self._django_request_logger.disabled = True

    def tearDown(self):
        self._django_request_logger.disabled = self._django_request_was_disabled
        super().tearDown()

    def test_view_exception_produces_one_error_event_with_traceback(self):
        status, _body = self._wsgi_get("/boom/")
        self.assertEqual("500 Internal Server Error", status)
        lines = self._lines()
        semlog_events = [
            line for line in lines if line.get("event_name") == "http.server.request"
        ]
        self.assertEqual(1, len(semlog_events))
        record = semlog_events[0]
        self.assertEqual("ERROR", record["severity_text"])
        self.assertEqual(500, record["http.response.status_code"])
        self.assertIn("exception.stacktrace", record)
        self.assertIn("matrix-boom", record["exception.stacktrace"])

    def test_process_exception_never_swallows_the_response_still_renders(self):
        status, body = self._wsgi_get("/boom/")
        self.assertEqual("500 Internal Server Error", status)
        self.assertTrue(body)  # Django's own rendered 500 body, untouched


@unittest.skipIf(django is None, "django is not installed in this environment")
class DjangoMiddlewareCoexistenceTests(_DjangoMiddlewareTestCase):
    """Using the class together with `WSGIMiddleware` wrapping the same
    application is not blocked at runtime: each layer parses the inbound
    `traceparent` independently and mints its own `span_id`, so with
    `log_requests=True` on both, two valid `http.server.request` events
    are emitted per request and neither layer raises. No `Proves:` line
    yet: waits for STANDARDS.md's own HTM-010 entry (phase 5)."""

    middleware = ("tests._django_matrix_middleware.django_middleware_logging",)
    service_name = "django-coexistence"

    def test_both_layers_emit_valid_events_sharing_one_trace_id(self):
        app = WSGIMiddleware(self._wsgi_app(), log_requests=True)
        env = environ(headers={"traceparent": _TRACE_A}, PATH_INFO="/sync/")
        start_response = recording_start_response()
        list(app(env, start_response))
        self.assertEqual("200 OK", start_response.calls[0][0])
        events = [
            line
            for line in self._lines()
            if line.get("event_name") == "http.server.request"
        ]
        self.assertEqual(2, len(events))
        self.assertEqual("a" * 32, events[0]["trace_id"])
        self.assertEqual("a" * 32, events[1]["trace_id"])
        self.assertNotEqual(events[0]["span_id"], events[1]["span_id"])


@unittest.skipIf(django is None, "django is not installed in this environment")
class DjangoMiddlewareStreamingTests(_DjangoMiddlewareTestCase):
    """The bound context stays correctly bound throughout a synchronous
    `StreamingHttpResponse` body, over both WSGI and ASGI: every chunk's
    own log line carries the request's `trace_id`, and the completion
    event's `event.duration` covers the whole streamed body, consumed
    well after the middleware chain itself has already returned. No
    `Proves:` line yet: waits for STANDARDS.md's own HTM-013 entry
    (phase 5)."""

    middleware = ("tests._django_matrix_middleware.django_middleware_logging",)
    service_name = "django-streaming"

    def test_wsgi_sync_streaming_body_keeps_context_bound_per_chunk(self):
        status, body = self._wsgi_get("/stream/", _TRACE_A)
        self.assertEqual("200 OK", status)
        self.assertEqual(b"chunk-0chunk-1chunk-2", body)
        lines = self._lines()
        chunk_lines = [
            line for line in lines if line.get("event_name") == "matrix.stream_chunk"
        ]
        self.assertEqual(3, len(chunk_lines))
        for line in chunk_lines:
            self.assertEqual("a" * 32, line["trace_id"])
        completion = [
            line for line in lines if line.get("event_name") == "http.server.request"
        ]
        self.assertEqual(1, len(completion))
        self.assertGreater(completion[0]["event.duration"], 0)

    def test_asgi_sync_streaming_body_keeps_context_bound_per_chunk(self):
        status, body = self._asgi_get("/stream/", _TRACE_A)
        self.assertEqual(200, status)
        self.assertEqual(b"chunk-0chunk-1chunk-2", body)
        lines = self._lines()
        chunk_lines = [
            line for line in lines if line.get("event_name") == "matrix.stream_chunk"
        ]
        self.assertEqual(3, len(chunk_lines))
        for line in chunk_lines:
            self.assertEqual("a" * 32, line["trace_id"])

    def test_reset_does_not_leak_into_a_later_request(self):
        self._wsgi_get("/stream/", _TRACE_A)
        self._wsgi_get("/sync/", _TRACE_B)
        lines = [
            line
            for line in self._lines()
            if line.get("event_name") == "matrix.sync_view"
        ]
        self.assertEqual(1, len(lines))
        self.assertEqual("b" * 32, lines[0]["trace_id"])


@unittest.skipIf(django is None, "django is not installed in this environment")
class DjangoMiddlewareAsyncStreamingTests(_DjangoMiddlewareTestCase):
    """The async half of HTM-013: an async-iterable streaming body keeps
    the same context-preservation guarantee. Skips entirely on Django's
    oldest supported row (3.2.9), which has no `__aiter__` on
    `StreamingHttpResponse` at all. No `Proves:` line yet: waits for
    STANDARDS.md's own HTM-013 entry (phase 5)."""

    middleware = ("tests._django_matrix_middleware.django_middleware_logging",)
    service_name = "django-async-streaming"

    def test_asgi_async_streaming_body_keeps_context_bound_per_chunk(self):
        from django.http import StreamingHttpResponse

        if not hasattr(StreamingHttpResponse, "__aiter__"):
            self.skipTest(
                "StreamingHttpResponse has no __aiter__ on this Django version"
            )
        status, body = self._asgi_get("/astream/", _TRACE_A)
        self.assertEqual(200, status)
        self.assertEqual(b"chunk-0chunk-1chunk-2", body)
        lines = self._lines()
        chunk_lines = [
            line for line in lines if line.get("event_name") == "matrix.astream_chunk"
        ]
        self.assertEqual(3, len(chunk_lines))
        for line in chunk_lines:
            self.assertEqual("a" * 32, line["trace_id"])


@unittest.skipIf(django is None, "django is not installed in this environment")
class DjangoMiddlewareFileResponseTests(_DjangoMiddlewareTestCase):
    """`FileResponse.file_to_stream` survives untouched (sendfile intact):
    the rebind carve-out leaves a `FileResponse` alone entirely, and a
    direct assertion confirms rebinding WOULD null it if attempted
    (design D5). No `Proves:` line yet: waits for STANDARDS.md's own
    HTM-013 entry (phase 5)."""

    middleware = ("tests._django_matrix_middleware.django_middleware_logging",)
    service_name = "django-file-response"

    def test_file_response_is_not_rebound_and_content_survives(self):
        status, body = self._wsgi_get("/file/")
        self.assertEqual("200 OK", status)
        self.assertEqual(b"file-body-bytes", body)

    def test_wsgi_file_wrapper_sendfile_path_reaches_the_wsgi_server(self):
        # The real proof the carve-out exists for: Django's own WSGIHandler
        # only substitutes environ["wsgi.file_wrapper"] for the response
        # when response.file_to_stream is still set (wsgi.py). If the
        # carve-out is ever removed, the rebind nulls file_to_stream
        # (design D5) before WSGIHandler makes that check, so the WSGI
        # server never sees the file wrapper at all -- asserting on the
        # returned iterable's own type is the only way to observe that.
        class _RecordingFileWrapper:
            def __init__(self, filelike, blksize=8192):
                self.filelike = filelike
                self.blksize = blksize

        env = environ(
            PATH_INFO="/file/", **{"wsgi.file_wrapper": _RecordingFileWrapper}
        )
        result = self._wsgi_app()(env, recording_start_response())
        self.assertIsInstance(
            result,
            _RecordingFileWrapper,
            "FileResponse must reach WSGIHandler's own file_wrapper path unrebound",
        )

    def test_rebinding_streaming_content_would_null_file_to_stream(self):
        # A direct assertion of the hazard the carve-out avoids: proves
        # the carve-out is load-bearing, not incidental.
        import tempfile

        from django.http import FileResponse

        with tempfile.NamedTemporaryFile(suffix=".bin") as handle:
            handle.write(b"probe-bytes")
            handle.flush()
            handle.seek(0)
            response = FileResponse(open(handle.name, "rb"))  # noqa: SIM115
            self.assertIsNotNone(response.file_to_stream)
            response.streaming_content = (chunk for chunk in response.streaming_content)
            self.assertIsNone(response.file_to_stream)
            response.close()


@unittest.skipIf(django is None, "django is not installed in this environment")
class DjangoLogRequestsSettingTests(_DjangoMiddlewareTestCase):
    """`SEMLOG_LOG_REQUESTS` reaches the class through the constraint that
    a dotted `settings.MIDDLEWARE` entry only ever receives one
    positional argument (design D6): `True` emits exactly one completion
    event per request, sync WSGI and async ASGI; `False` and absent emit
    none; a non-boolean value raises `ValueError` naming the value and
    its source; an explicit `log_requests=` keyword still wins; and `off`
    mode ignores the setting entirely. No `Proves:` line yet: waits for
    STANDARDS.md's own HTM-011 entry (phase 5)."""

    service_name = "django-log-requests-setting"

    def _events(self):
        return [
            line
            for line in self._lines()
            if line.get("event_name") == "http.server.request"
        ]

    def test_setting_true_emits_one_event_over_wsgi_and_over_asgi(self):
        from django.test import override_settings

        with override_settings(SEMLOG_LOG_REQUESTS=True):
            wsgi_status, _body = self._wsgi_get("/sync/")
            asgi_status, _body2 = self._asgi_get("/sync/")
        self.assertEqual("200 OK", wsgi_status)
        self.assertEqual(200, asgi_status)
        self.assertEqual(2, len(self._events()))

    def test_setting_false_emits_nothing(self):
        from django.test import override_settings

        with override_settings(SEMLOG_LOG_REQUESTS=False):
            self._wsgi_get("/sync/")
        self.assertEqual(0, len(self._events()))

    def test_setting_absent_emits_nothing(self):
        self._wsgi_get("/sync/")
        self.assertEqual(0, len(self._events()))

    def test_non_bool_value_raises_value_error_naming_value_and_source(self):
        from django.test import override_settings

        with (
            override_settings(SEMLOG_LOG_REQUESTS="true"),
            self.assertRaises(ValueError) as cm,
        ):
            self._wsgi_app()  # load_middleware() constructs DjangoMiddleware
        message = str(cm.exception)
        self.assertIn("'true'", message)
        self.assertIn("Django settings", message)

    def test_explicit_keyword_wins_over_the_setting(self):
        from django.test import override_settings

        from semlog import DjangoMiddleware

        with override_settings(SEMLOG_LOG_REQUESTS=True):
            mw = DjangoMiddleware(lambda request: None, log_requests=False)
        self.assertFalse(mw._log_requests)

    def test_off_mode_ignores_the_setting_entirely(self):
        from django.test import override_settings

        from semlog import _modes

        _modes.state.mode = "off"
        try:
            with override_settings(SEMLOG_LOG_REQUESTS=True):
                self._wsgi_get("/sync/")
            self.assertEqual(0, len(self._events()))
        finally:
            _modes.state.mode = "full"

    def test_unconfigured_settings_resolve_to_false_without_raising(self):
        # design D9: an unconfigured LazySettings raises ImproperlyConfigured
        # from django.conf, not AttributeError, so a bare getattr default
        # does not cover it -- only a guard broad enough to catch it does.
        # A child interpreter with Django genuinely importable but its
        # settings never configured (no configure() call, no
        # DJANGO_SETTINGS_MODULE) proves this half of the guard directly:
        # narrowing the guard to `except ImportError` alone would let
        # ImproperlyConfigured escape uncaught right here.
        script = """
import django  # noqa: F401 -- proves Django is genuinely importable here
import semlog

middleware = semlog.DjangoMiddleware(lambda request: request)
assert middleware._log_requests is False, middleware._log_requests
print("OK")
"""
        env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            env=env,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
