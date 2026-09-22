"""aiohttp middleware tests (STANDARDS.md HTM-014..HTM-017).

Skips gracefully, with an explicit reason, when `aiohttp` is not
installed, so `python -m unittest discover` stays stdlib-only
(CP-008/CP-013). The matrix itself is proven by running this exact
module inside throwaway virtual environments pinned to each row's exact
aiohttp/Python combination:

- floor: aiohttp 3.10.0, Python 3.10 only.
- latest: aiohttp 3.14.3, Python 3.10 and 3.14.

Two test shapes live here on purpose. The unit tests drive
`AiohttpMiddleware.__call__` directly with a minimal request stand-in
(the two attributes the middleware reads), which is the only way to
assert on an exception the middleware re-raises. The real-application
tests run an `aiohttp.web.Application` over a real loopback socket,
because aiohttp's own request lifecycle -- keep-alive connections,
`StreamResponse` writes, a deferred async body sent after the middleware
chain has already returned -- is exactly what the context and duration
rules are about.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import unittest
from unittest import mock

try:
    from aiohttp import ClientResponseError, ClientSession, web
except ImportError:  # pragma: no cover -- exercised only without aiohttp
    web = None

from semlog import AiohttpMiddleware, _modes, _transport
from semlog._config import configure
from semlog._context import current

from ._pipeline_support import FakeStdout, reset_pipeline

_TRACE_A = "00-" + "a" * 32 + "-" + "1" * 16 + "-01"
_TRACE_B = "00-" + "b" * 32 + "-" + "2" * 16 + "-01"
_MIDDLEWARE_LOGGER = "semlog._middleware"


class _Request:
    """The three attributes `AiohttpMiddleware` reads off a request."""

    def __init__(self, headers=None, method="GET", path="/orders"):
        self.headers = headers if headers is not None else {}
        self.method = method
        self.path = path


class _Response:
    """A response stand-in carrying only the status the event reports."""

    def __init__(self, status=200):
        self.status = status


def _reset_state():
    reset_pipeline()
    _modes.state.mode = "full"
    _modes.state.marking = False


def _off_configure():
    with mock.patch.dict("os.environ", {}, clear=True):
        configure(mode="off", service_name="svc", search_dir=".")


def _hybrid_configure():
    with mock.patch.dict("os.environ", {}, clear=True):
        configure(mode="hybrid", service_name="svc", search_dir=".")


@contextlib.asynccontextmanager
async def _serving(app):
    """Run `app` on a real loopback socket, yielding its base URL."""
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    try:
        # `runner.addresses` carries the port the kernel really bound;
        # `TCPSite.name` still reports the requested port 0 on the floor
        # row, so it cannot be used here.
        host, port = runner.addresses[0][:2]
        yield f"http://{host}:{port}"
    finally:
        await runner.cleanup()


@unittest.skipIf(web is None, "aiohttp is not installed in this environment")
class AiohttpMiddlewareContextTests(unittest.TestCase):
    """Proves: HTM-014"""

    def test_inbound_traceparent_is_bound_while_the_handler_runs(self):
        async def handler(request):
            return current().trace_id

        middleware = AiohttpMiddleware()
        request = _Request({"traceparent": _TRACE_A})
        self.assertEqual("a" * 32, asyncio.run(middleware(request, handler)))

    def test_the_scope_is_popped_before_the_middleware_returns(self):
        # Read back INSIDE the same coroutine, never after `asyncio.run`
        # returned: `asyncio.run` runs the coroutine in a fresh task with
        # its own copy of the context, so a `current()` read made after it
        # returns is `None` whatever the middleware did, and a middleware
        # that never pops passes such an assertion. Asserting here, in the
        # context the middleware actually mutated, is what makes the leak
        # observable.
        async def handler(request):
            return _Response()

        async def scenario():
            await AiohttpMiddleware()(_Request({"traceparent": _TRACE_A}), handler)
            return current()

        self.assertIsNone(asyncio.run(scenario()))

    def test_the_scope_is_popped_when_the_handler_raises(self):
        async def handler(request):
            raise ValueError("boom")

        async def scenario():
            with contextlib.suppress(ValueError):
                await AiohttpMiddleware()(_Request({"traceparent": _TRACE_A}), handler)
            return current()

        self.assertIsNone(asyncio.run(scenario()))

    def test_the_scope_is_popped_when_the_handler_is_cancelled(self):
        async def handler(request):
            raise asyncio.CancelledError

        async def scenario():
            with contextlib.suppress(asyncio.CancelledError):
                await AiohttpMiddleware()(_Request({"traceparent": _TRACE_A}), handler)
            return current()

        self.assertIsNone(asyncio.run(scenario()))

    def test_an_outer_middleware_sees_the_scope_gone_after_a_real_request(self):
        # A real application, with a probe middleware registered OUTSIDE
        # semlog's: aiohttp chains middlewares inside one task, so the
        # probe reads back the very context semlog mutated, once semlog's
        # own call has returned.
        seen = []

        @web.middleware
        async def probe(request, handler):
            response = await handler(request)
            seen.append(current())
            return response

        async def endpoint(request):
            return web.Response(text=current().trace_id)

        async def scenario():
            app = web.Application(middlewares=[probe, AiohttpMiddleware()])
            app.router.add_get("/orders", endpoint)
            async with (
                _serving(app) as base,
                ClientSession() as session,
                session.get(
                    f"{base}/orders", headers={"traceparent": _TRACE_A}
                ) as response,
            ):
                return await response.text()

        self.assertEqual("a" * 32, asyncio.run(scenario()))
        self.assertEqual([None], seen)

    def test_two_requests_on_one_keep_alive_connection_never_share_a_scope(self):
        # Both requests are served by the same connection task, so they
        # share one `contextvars.Context`: a scope left bound by the first
        # would still be bound when the second arrives.
        seen = []

        @web.middleware
        async def probe(request, handler):
            seen.append(current())
            return await handler(request)

        async def endpoint(request):
            return web.Response(text=current().trace_id)

        async def scenario():
            app = web.Application(middlewares=[probe, AiohttpMiddleware()])
            app.router.add_get("/orders", endpoint)
            traces = []
            async with _serving(app) as base, ClientSession() as session:
                for traceparent in (_TRACE_A, _TRACE_B):
                    async with session.get(
                        f"{base}/orders", headers={"traceparent": traceparent}
                    ) as response:
                        traces.append(await response.text())
                        # Keep-alive is only reused once the body is read.
                        self.assertEqual(1, len(session.connector._conns))
            return traces

        self.assertEqual(["a" * 32, "b" * 32], asyncio.run(scenario()))
        self.assertEqual([None, None], seen)

    def test_stream_response_writes_stay_in_the_request_scope(self):
        seen = []

        async def endpoint(request):
            response = web.StreamResponse()
            await response.prepare(request)
            seen.append(current().trace_id)
            await response.write(b"ok")
            seen.append(current().trace_id)
            await response.write_eof()
            return response

        async def scenario():
            app = web.Application(middlewares=[AiohttpMiddleware()])
            app.router.add_get("/stream", endpoint)
            async with (
                _serving(app) as base,
                ClientSession() as session,
                session.get(
                    f"{base}/stream", headers={"traceparent": _TRACE_A}
                ) as response,
            ):
                return await response.read()

        self.assertEqual(b"ok", asyncio.run(scenario()))
        self.assertEqual(["a" * 32, "a" * 32], seen)

    def test_websocket_activity_stays_in_the_request_scope(self):
        async def endpoint(request):
            websocket = web.WebSocketResponse()
            await websocket.prepare(request)
            await websocket.send_str(current().trace_id)
            return websocket

        async def scenario():
            app = web.Application(middlewares=[AiohttpMiddleware()])
            app.router.add_get("/socket", endpoint)
            async with (
                _serving(app) as base,
                ClientSession() as session,
                session.ws_connect(
                    f"{base}/socket", headers={"traceparent": _TRACE_A}
                ) as websocket,
            ):
                return (await websocket.receive()).data

        self.assertEqual("a" * 32, asyncio.run(scenario()))

    def test_a_deferred_async_body_runs_outside_the_request_scope(self):
        # aiohttp sends the body after the middleware chain has returned,
        # so this generator runs with the scope already popped. Documented
        # behavior, not a defect: the recipe says to read context inside
        # the handler.
        seen = {}

        async def body():
            seen["context"] = current()
            yield b"ok"

        async def endpoint(request):
            return web.Response(body=body())

        async def scenario():
            app = web.Application(middlewares=[AiohttpMiddleware()])
            app.router.add_get("/deferred", endpoint)
            async with (
                _serving(app) as base,
                ClientSession() as session,
                session.get(f"{base}/deferred") as response,
            ):
                return await response.read()

        self.assertEqual(b"ok", asyncio.run(scenario()))
        self.assertIsNone(seen["context"])

    def test_the_class_declares_the_new_style_middleware_version(self):
        # aiohttp warns `old-style middleware ... deprecated` and calls the
        # object as a factory without this attribute.
        self.assertEqual(1, AiohttpMiddleware.__middleware_version__)

        async def endpoint(request):
            return web.Response(text="ok")

        async def scenario():
            app = web.Application(middlewares=[AiohttpMiddleware()])
            app.router.add_get("/orders", endpoint)
            async with (
                _serving(app) as base,
                ClientSession() as session,
                session.get(f"{base}/orders") as response,
            ):
                return response.status, await response.text()

        with mock.patch("warnings.warn") as warn:
            self.assertEqual((200, "ok"), asyncio.run(scenario()))
        deprecations = [
            call for call in warn.call_args_list if "old-style middleware" in str(call)
        ]
        self.assertEqual([], deprecations)


@unittest.skipIf(web is None, "aiohttp is not installed in this environment")
class AiohttpMiddlewareRequestEventTests(unittest.TestCase):
    """Proves: HTM-015"""

    def _run(self, handler, request=None, **kwargs):
        middleware = AiohttpMiddleware(log_requests=True, **kwargs)
        return asyncio.run(middleware(request or _Request(), handler))

    def test_a_normal_response_emits_one_info_event(self):
        async def handler(request):
            return _Response(201)

        with self.assertLogs(_MIDDLEWARE_LOGGER, level="INFO") as captured:
            response = self._run(handler)

        self.assertEqual(201, response.status)
        self.assertEqual(1, len(captured.records))
        record = captured.records[0]
        self.assertEqual(logging.INFO, record.levelno)
        self.assertEqual("http.server.request", record.msg)
        self.assertEqual("GET", record.__dict__["http.request.method"])
        self.assertEqual("/orders", record.__dict__["url.path"])
        self.assertEqual(201, record.__dict__["http.response.status_code"])
        self.assertIsInstance(record.__dict__["event.duration"], int)
        self.assertIsNone(record.exc_info)
        self.assertNotIn("url.query", record.__dict__)

    def test_an_http_exception_emits_an_info_event_at_its_own_status(self):
        for exception, status in (
            (web.HTTPNotFound(), 404),
            (web.HTTPMovedPermanently("/elsewhere"), 301),
            (web.HTTPInternalServerError(), 500),
        ):
            with self.subTest(status=status):

                async def handler(request, exception=exception):
                    raise exception

                with (
                    self.assertLogs(_MIDDLEWARE_LOGGER, level="INFO") as captured,
                    self.assertRaises(web.HTTPException),
                ):
                    self._run(handler)

                self.assertEqual(1, len(captured.records))
                record = captured.records[0]
                self.assertEqual(logging.INFO, record.levelno)
                self.assertIsNone(record.exc_info)
                self.assertEqual(status, record.__dict__["http.response.status_code"])

    def test_an_ordinary_exception_emits_an_error_event_with_the_traceback(self):
        async def handler(request):
            raise ValueError("boom")

        with (
            self.assertLogs(_MIDDLEWARE_LOGGER, level="ERROR") as captured,
            self.assertRaisesRegex(ValueError, "boom"),
        ):
            self._run(handler)

        self.assertEqual(1, len(captured.records))
        record = captured.records[0]
        self.assertEqual(logging.ERROR, record.levelno)
        self.assertIs(ValueError, record.exc_info[0])
        self.assertIsNone(record.__dict__["http.response.status_code"])

    def test_an_outbound_client_error_carrying_a_status_is_still_an_error(self):
        # `ClientResponseError` carries a `.status` of its own, but it is
        # the status of the OUTBOUND call the handler made, not of this
        # request: aiohttp answers 500 and the traceback is the point.
        async def handler(request):
            raise ClientResponseError(
                request_info=None, history=(), status=404, message="upstream said 404"
            )

        with (
            self.assertLogs(_MIDDLEWARE_LOGGER, level="ERROR") as captured,
            self.assertRaises(ClientResponseError),
        ):
            self._run(handler)

        self.assertEqual(1, len(captured.records))
        record = captured.records[0]
        self.assertEqual(logging.ERROR, record.levelno)
        self.assertIs(ClientResponseError, record.exc_info[0])
        self.assertIsNone(record.__dict__["http.response.status_code"])

    def test_a_connection_error_emits_an_error_event_and_is_re_raised(self):
        for exception_type in (ConnectionResetError, BrokenPipeError):
            with self.subTest(exception_type=exception_type):

                async def handler(request, exception_type=exception_type):
                    raise exception_type("peer went away")

                with (
                    self.assertLogs(_MIDDLEWARE_LOGGER, level="ERROR") as captured,
                    self.assertRaises(exception_type),
                ):
                    self._run(handler)

                self.assertEqual(1, len(captured.records))
                record = captured.records[0]
                self.assertEqual(logging.ERROR, record.levelno)
                self.assertIs(exception_type, record.exc_info[0])

    def test_a_cancellation_emits_no_event_and_is_re_raised(self):
        async def handler(request):
            raise asyncio.CancelledError

        with (
            self.assertNoLogs(_MIDDLEWARE_LOGGER, level="INFO"),
            self.assertRaises(asyncio.CancelledError),
        ):
            self._run(handler)

    def test_a_handler_returning_no_response_never_raises_inside_semlog(self):
        # A handler that forgot its `return`. Reading `.status` off it
        # inside the middleware's own `try` would raise an `AttributeError`
        # there, log it as this request's failure, and destroy aiohttp's
        # own "Handler should return response instance" diagnostic.
        async def handler(request):
            return None

        with self.assertLogs(_MIDDLEWARE_LOGGER, level="INFO") as captured:
            response = self._run(handler)

        self.assertIsNone(response)
        self.assertEqual(1, len(captured.records))
        record = captured.records[0]
        self.assertEqual(logging.INFO, record.levelno)
        self.assertIsNone(record.exc_info)
        self.assertIsNone(record.__dict__["http.response.status_code"])

    def test_log_requests_off_emits_nothing_at_all(self):
        async def handler(request):
            return _Response()

        middleware = AiohttpMiddleware()
        with self.assertNoLogs(_MIDDLEWARE_LOGGER, level="INFO"):
            asyncio.run(middleware(_Request(), handler))

    def test_a_real_application_reports_the_status_aiohttp_really_sent(self):
        async def ok(request):
            return web.Response(text="ok")

        async def not_found(request):
            raise web.HTTPNotFound

        async def upstream_404(request):
            raise ClientResponseError(
                request_info=None, history=(), status=404, message="upstream said 404"
            )

        async def scenario():
            app = web.Application(
                middlewares=[AiohttpMiddleware(log_requests=True)],
            )
            app.router.add_get("/ok", ok)
            app.router.add_get("/not-found", not_found)
            app.router.add_get("/upstream-404", upstream_404)
            statuses = []
            async with _serving(app) as base, ClientSession() as session:
                for path in ("/ok", "/not-found", "/upstream-404"):
                    async with session.get(f"{base}{path}") as response:
                        statuses.append(response.status)
            return statuses

        with self.assertLogs(_MIDDLEWARE_LOGGER, level="INFO") as captured:
            statuses = asyncio.run(scenario())

        self.assertEqual([200, 404, 500], statuses)
        events = [
            (
                record.levelno,
                record.__dict__["url.path"],
                record.__dict__["http.response.status_code"],
                None if record.exc_info is None else record.exc_info[0],
            )
            for record in captured.records
        ]
        self.assertEqual(
            [
                (logging.INFO, "/ok", 200, None),
                (logging.INFO, "/not-found", 404, None),
                (logging.ERROR, "/upstream-404", None, ClientResponseError),
            ],
            events,
        )

    def test_a_real_handler_returning_no_response_leaves_aiohttp_in_charge(self):
        # How aiohttp answers a handler that forgot its `return` is its own
        # decision and it has changed between supported rows (3.10.0 drops
        # the connection, 3.14.3 answers 500), so that is deliberately not
        # asserted here. What semlog owes is the same on every row: one
        # INFO completion event with a null status, and no exception of
        # semlog's own standing in for aiohttp's diagnostic.
        async def forgot_to_return(request):
            return None

        async def scenario():
            app = web.Application(middlewares=[AiohttpMiddleware(log_requests=True)])
            app.router.add_get("/forgot", forgot_to_return)
            async with _serving(app) as base, ClientSession() as session:
                with contextlib.suppress(Exception):
                    async with session.get(f"{base}/forgot") as response:
                        return response.status
            return None

        with self.assertLogs(_MIDDLEWARE_LOGGER, level="INFO") as captured:
            asyncio.run(scenario())

        # One event per handler call. The floor row's client retries the
        # idempotent GET once after the server drops the connection, so the
        # count is the row's business; every event's shape is semlog's.
        self.assertGreaterEqual(len(captured.records), 1)
        self.assertEqual(
            [(logging.INFO, "/forgot", None, None)] * len(captured.records),
            [
                (
                    record.levelno,
                    record.__dict__["url.path"],
                    record.__dict__["http.response.status_code"],
                    record.exc_info,
                )
                for record in captured.records
            ],
        )


@unittest.skipIf(web is None, "aiohttp is not installed in this environment")
class AiohttpMiddlewareOffModeTests(unittest.TestCase):
    """Proves: HTM-016

    `off` must behave as if semlog were not installed even INSIDE a
    request, matching `tests/test_off_mode.py::OffMiddlewareScopeTests`
    for the other middlewares: no header parsing, no scope pushed, so
    `inject()`/`bind()` called from the handler are inert and no
    `traceparent` of semlog's own ever reaches an outbound mapping."""

    def tearDown(self):
        _reset_state()

    def test_the_handler_still_runs_and_its_response_is_returned(self):
        _off_configure()
        called = []

        async def handler(request):
            called.append(request.path)
            return _Response(204)

        response = asyncio.run(AiohttpMiddleware()(_Request(), handler))
        self.assertEqual(["/orders"], called)
        self.assertEqual(204, response.status)

    def test_no_scope_is_pushed_so_inject_and_bind_are_inert(self):
        from semlog import bind, inject

        _off_configure()
        seen = {}

        async def handler(request):
            seen["context"] = current()
            bind({"app.a": 1})  # must not raise
            headers = {}
            inject(headers)
            seen["headers"] = headers
            return _Response()

        asyncio.run(
            AiohttpMiddleware()(_Request({"traceparent": _TRACE_A}), handler),
        )
        self.assertIsNone(seen["context"])
        self.assertEqual({}, seen["headers"])

    def test_log_requests_true_emits_nothing_in_off_mode(self):
        _off_configure()

        async def handler(request):
            return _Response()

        with self.assertNoLogs(_MIDDLEWARE_LOGGER, level="INFO"):
            asyncio.run(AiohttpMiddleware(log_requests=True)(_Request(), handler))

    def test_log_requests_true_emits_nothing_on_a_raising_handler_in_off_mode(self):
        _off_configure()

        async def handler(request):
            raise ValueError("boom")

        with (
            self.assertNoLogs(_MIDDLEWARE_LOGGER, level="ERROR"),
            self.assertRaises(ValueError),
        ):
            asyncio.run(AiohttpMiddleware(log_requests=True)(_Request(), handler))

    def test_an_http_exception_emits_nothing_in_off_mode(self):
        _off_configure()

        async def handler(request):
            raise web.HTTPNotFound

        with (
            self.assertNoLogs(_MIDDLEWARE_LOGGER, level="INFO"),
            self.assertRaises(web.HTTPException),
        ):
            asyncio.run(AiohttpMiddleware(log_requests=True)(_Request(), handler))


@unittest.skipIf(web is None, "aiohttp is not installed in this environment")
class AiohttpHybridRequestEventTests(unittest.TestCase):
    """Proves: HTM-015

    The hybrid half, mirroring
    `tests/test_hybrid.py::HybridRequestEventTests`: the completion event
    is marked `semlog=True`, so it routes to JSON only and never also
    renders as legacy text through a `StreamHandler` (LM-003)."""

    def setUp(self):
        self._logger = logging.getLogger(_MIDDLEWARE_LOGGER)
        self._original_handlers = list(self._logger.handlers)
        self._original_propagate = self._logger.propagate
        self._original_level = self._logger.level

    def tearDown(self):
        self._logger.handlers = self._original_handlers
        self._logger.propagate = self._original_propagate
        self._logger.setLevel(self._original_level)
        _reset_state()

    def test_http_server_request_routes_to_json_only_in_hybrid(self):
        async def handler(request):
            return _Response(200)

        fake_stdout = FakeStdout()
        captured = io.StringIO()
        self._logger.handlers = [logging.StreamHandler(captured)]
        self._logger.propagate = False
        self._logger.setLevel(logging.INFO)
        with mock.patch("sys.stdout", fake_stdout):
            _hybrid_configure()
            asyncio.run(AiohttpMiddleware(log_requests=True)(_Request(), handler))
            _transport.flush(timeout=2)

        self.assertEqual("", captured.getvalue())
        lines = fake_stdout.buffer.getvalue().decode("utf-8").strip().splitlines()
        self.assertEqual(1, len(lines))
        record = json.loads(lines[0])
        self.assertEqual("http.server.request", record["event_name"])
        self.assertEqual(200, record["http.response.status_code"])


@unittest.skipIf(web is None, "aiohttp is not installed in this environment")
class AiohttpEventDurationScopeTests(unittest.TestCase):
    """Proves: HTM-017"""

    def test_the_event_is_emitted_before_the_middleware_returns(self):
        # aiohttp sends the response only after the middleware chain has
        # returned, so an event emitted here can only measure the handler.
        order = []

        class _Recorder(logging.Handler):
            def emit(self, record):
                order.append("event")

        async def handler(request):
            return _Response()

        logger = logging.getLogger(_MIDDLEWARE_LOGGER)
        recorder = _Recorder()
        logger.addHandler(recorder)
        original_level = logger.level
        logger.setLevel(logging.INFO)
        try:

            async def scenario():
                await AiohttpMiddleware(log_requests=True)(_Request(), handler)
                order.append("middleware returned")

            asyncio.run(scenario())
        finally:
            logger.removeHandler(recorder)
            logger.setLevel(original_level)

        self.assertEqual(["event", "middleware returned"], order)

    def test_a_deferred_response_body_is_transmitted_after_the_event(self):
        # The same fact against a real server: the body of a deferred
        # async response is produced after the completion event, so its
        # transmission is outside `event.duration`.
        order = []

        class _Recorder(logging.Handler):
            def emit(self, record):
                order.append(("event", record.__dict__["event.duration"]))

        async def body():
            order.append(("body", None))
            yield b"ok"

        async def endpoint(request):
            return web.Response(body=body())

        async def scenario():
            app = web.Application(middlewares=[AiohttpMiddleware(log_requests=True)])
            app.router.add_get("/deferred", endpoint)
            async with (
                _serving(app) as base,
                ClientSession() as session,
                session.get(f"{base}/deferred") as response,
            ):
                return await response.read()

        logger = logging.getLogger(_MIDDLEWARE_LOGGER)
        recorder = _Recorder()
        logger.addHandler(recorder)
        original_level = logger.level
        logger.setLevel(logging.INFO)
        try:
            self.assertEqual(b"ok", asyncio.run(scenario()))
        finally:
            logger.removeHandler(recorder)
            logger.setLevel(original_level)

        self.assertEqual(["event", "body"], [kind for kind, _ in order])
        self.assertGreater(order[0][1], 0)

    def test_the_duration_covers_the_handler_it_wrapped(self):
        async def handler(request):
            await asyncio.sleep(0.02)
            return _Response()

        with self.assertLogs(_MIDDLEWARE_LOGGER, level="INFO") as captured:
            asyncio.run(AiohttpMiddleware(log_requests=True)(_Request(), handler))

        duration = captured.records[0].__dict__["event.duration"]
        self.assertGreaterEqual(duration, 20_000_000)


if __name__ == "__main__":
    unittest.main()
