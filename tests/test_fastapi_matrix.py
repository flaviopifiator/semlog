"""Compatibility matrix: FastAPI + Starlette, both the floor and the
latest row (STANDARDS.md CP-002, section 11; design #162 section 9;
design-decisions #170 section 9b; spec #176 CP-002 revision 3).

Skips gracefully, with an explicit reason, when `fastapi` is not
installed, so `python -m unittest discover` stays stdlib-only
(CP-008/CP-013). The matrix itself is proven by running this exact module
inside throwaway virtual environments pinned to each row's exact
FastAPI/Starlette/Python combination:

- floor: FastAPI 0.71.0 (Starlette 0.17.1 pinned), Python 3.10 only.
- latest: FastAPI 0.141.1 / Starlette 1.6.0, Python 3.10-3.14.

See the sdd-apply evidence (engram `sdd/structured-logging-standard/
apply-progress`) for the per-cell results table.

Proves: CP-002 -- an `async def` endpoint and a `def` (sync) endpoint,
both wrapped by `semlog.ASGIMiddleware`, both propagate the inbound W3C
trace context (the sync endpoint across Starlette's `run_in_threadpool`
offload); each logged call emits exactly one JSON line; and two
sequential requests carrying different trace ids never leak context into
each other.
"""

from __future__ import annotations

import asyncio
import json
import logging
import unittest
from unittest import mock

try:
    from fastapi import FastAPI
except ImportError:  # pragma: no cover -- exercised only without fastapi installed
    FastAPI = None

from semlog import _transport
from semlog._middleware import ASGIMiddleware

from ._matrix_support import call_asgi_app
from ._pipeline_support import FakeStdout, reset_pipeline

_TRACE_A = "00-" + "a" * 32 + "-" + "1" * 16 + "-01"
_TRACE_B = "00-" + "b" * 32 + "-" + "2" * 16 + "-01"
_LOGGER_NAME = "semlog.tests.fastapi_matrix"


def _build_app():
    app = FastAPI()
    logger = logging.getLogger(_LOGGER_NAME)

    @app.get("/async-endpoint")
    async def async_endpoint():
        logger.info("matrix.async_endpoint")
        return {"kind": "async"}

    @app.get("/sync-endpoint")
    def sync_endpoint():
        # A plain `def` endpoint: Starlette offloads this to a worker
        # thread via `run_in_threadpool` (`anyio.to_thread.run_sync`,
        # which copies the current `contextvars.Context` into that
        # thread), so this call proves context survives that offload.
        logger.info("matrix.sync_endpoint")
        return {"kind": "sync"}

    return app


@unittest.skipIf(FastAPI is None, "fastapi is not installed in this environment")
class FastAPIMatrixTests(unittest.TestCase):
    """Proves: CP-002"""

    def setUp(self):
        self.fake_stdout = FakeStdout()
        self._stdout_patch = mock.patch("sys.stdout", self.fake_stdout)
        self._stdout_patch.start()
        with mock.patch.dict("os.environ", {}, clear=True):
            from semlog import configure

            configure(service_name="fastapi-matrix", search_dir=".")
        self.app = ASGIMiddleware(_build_app())

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

    def test_async_endpoint_propagates_inbound_trace_context(self):
        status = self._get("/async-endpoint", _TRACE_A)
        self.assertEqual(200, status)
        lines = self._lines()
        self.assertEqual(1, len(lines))
        self.assertEqual("a" * 32, lines[0]["trace_id"])
        self.assertEqual("matrix.async_endpoint", lines[0]["event_name"])

    def test_sync_endpoint_propagates_inbound_trace_context_via_threadpool(self):
        status = self._get("/sync-endpoint", _TRACE_A)
        self.assertEqual(200, status)
        lines = self._lines()
        self.assertEqual(1, len(lines))
        self.assertEqual("a" * 32, lines[0]["trace_id"])
        self.assertEqual("matrix.sync_endpoint", lines[0]["event_name"])

    def test_two_sequential_requests_do_not_leak_trace_context(self):
        self._get("/async-endpoint", _TRACE_A)
        self._get("/sync-endpoint", _TRACE_B)
        lines = self._lines()
        self.assertEqual(2, len(lines))
        self.assertEqual("a" * 32, lines[0]["trace_id"])
        self.assertEqual("b" * 32, lines[1]["trace_id"])


if __name__ == "__main__":
    unittest.main()
