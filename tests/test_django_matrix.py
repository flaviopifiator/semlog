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
import json
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


if __name__ == "__main__":
    unittest.main()
