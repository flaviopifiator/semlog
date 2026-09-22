"""Tests for the aiohttp middleware integration."""

from __future__ import annotations

import asyncio
import logging
import unittest

try:
    from aiohttp import web
except ImportError:
    web = None

from semlog import AiohttpMiddleware
from semlog._context import current


class _Request:
    def __init__(self, headers):
        self.headers = headers
        self.method = "GET"
        self.path = "/orders"


class AiohttpMiddlewareTests(unittest.TestCase):
    def test_valid_traceparent_binds_the_inbound_trace_id(self):
        async def handler(request):
            return current().trace_id

        middleware = AiohttpMiddleware()
        request = _Request(
            {"traceparent": ("00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01")}
        )

        trace_id = asyncio.run(middleware(request, handler))

        self.assertEqual("4bf92f3577b34da6a3ce929d0e0e4736", trace_id)
        self.assertIsNone(current())

    def test_log_requests_emits_the_completion_event(self):
        class Response:
            status = 201

        async def handler(request):
            return Response()

        middleware = AiohttpMiddleware(log_requests=True)
        with self.assertLogs("semlog._middleware", level="INFO") as captured:
            response = asyncio.run(middleware(_Request({}), handler))

        self.assertEqual(201, response.status)
        self.assertEqual(1, len(captured.records))
        record = captured.records[0]
        self.assertEqual(logging.INFO, record.levelno)
        self.assertEqual("http.server.request", record.msg)
        self.assertEqual("GET", record.__dict__["http.request.method"])
        self.assertEqual("/orders", record.__dict__["url.path"])
        self.assertEqual(201, record.__dict__["http.response.status_code"])
        self.assertIsInstance(record.__dict__["event.duration"], int)
        self.assertNotIn("url.query", record.__dict__)

    def test_log_requests_emits_an_error_event_and_reraises(self):
        async def handler(request):
            raise ValueError("boom")

        middleware = AiohttpMiddleware(log_requests=True)
        with (
            self.assertLogs("semlog._middleware", level="ERROR") as captured,
            self.assertRaisesRegex(ValueError, "boom"),
        ):
            asyncio.run(middleware(_Request({}), handler))

        self.assertEqual(1, len(captured.records))
        record = captured.records[0]
        self.assertEqual(logging.ERROR, record.levelno)
        self.assertIsNotNone(record.exc_info)
        self.assertIsNone(record.__dict__["http.response.status_code"])
        self.assertIsNone(current())

    def test_cancellation_and_peer_disconnects_do_not_emit_an_event(self):
        for exception_type in (
            asyncio.CancelledError,
            ConnectionResetError,
            BrokenPipeError,
        ):
            with self.subTest(exception_type=exception_type):

                async def handler(request, exception_type=exception_type):
                    raise exception_type()

                middleware = AiohttpMiddleware(log_requests=True)
                with (
                    self.assertNoLogs("semlog._middleware", level="INFO"),
                    self.assertRaises(exception_type),
                ):
                    asyncio.run(middleware(_Request({}), handler))
                self.assertIsNone(current())

    def test_http_exception_emits_an_info_event_and_reraises(self):
        class HTTPException(Exception):
            status = 404

        async def handler(request):
            raise HTTPException()

        middleware = AiohttpMiddleware(log_requests=True)
        with (
            self.assertLogs("semlog._middleware", level="INFO") as captured,
            self.assertRaises(HTTPException),
        ):
            asyncio.run(middleware(_Request({}), handler))

        self.assertEqual(1, len(captured.records))
        record = captured.records[0]
        self.assertEqual(logging.INFO, record.levelno)
        self.assertIsNone(record.exc_info)
        self.assertEqual(404, record.__dict__["http.response.status_code"])

    @unittest.skipUnless(web is not None, "aiohttp extra is not installed")
    def test_real_application_registers_the_middleware(self):
        async def scenario():
            async def handler(request):
                return web.Response(text=current().trace_id)

            app = web.Application(middlewares=[AiohttpMiddleware()])
            app.router.add_get("/orders", handler)
            runner = web.AppRunner(app, access_log=None)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            try:
                from aiohttp import ClientSession

                headers = {
                    "traceparent": (
                        "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
                    )
                }
                async with (
                    ClientSession() as session,
                    session.get(
                        f"http://127.0.0.1:{port}/orders", headers=headers
                    ) as response,
                ):
                    self.assertEqual(200, response.status)
                    self.assertEqual(
                        "4bf92f3577b34da6a3ce929d0e0e4736",
                        await response.text(),
                    )
            finally:
                await runner.cleanup()

        asyncio.run(scenario())

    @unittest.skipUnless(web is not None, "aiohttp extra is not installed")
    def test_stream_response_writes_stay_in_the_request_context(self):
        async def scenario():
            seen = []

            async def handler(request):
                response = web.StreamResponse()
                await response.prepare(request)
                seen.append(current().trace_id)
                await response.write(b"ok")
                seen.append(current().trace_id)
                await response.write_eof()
                return response

            app = web.Application(middlewares=[AiohttpMiddleware()])
            app.router.add_get("/stream", handler)
            runner = web.AppRunner(app, access_log=None)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            try:
                from aiohttp import ClientSession

                headers = {
                    "traceparent": (
                        "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
                    )
                }
                async with (
                    ClientSession() as session,
                    session.get(
                        f"http://127.0.0.1:{port}/stream", headers=headers
                    ) as response,
                ):
                    self.assertEqual(b"ok", await response.read())
            finally:
                await runner.cleanup()

            self.assertEqual(["4bf92f3577b34da6a3ce929d0e0e4736"] * 2, seen)

        asyncio.run(scenario())

    @unittest.skipUnless(web is not None, "aiohttp extra is not installed")
    def test_websocket_session_stays_in_the_request_context(self):
        async def scenario():
            async def handler(request):
                websocket = web.WebSocketResponse()
                await websocket.prepare(request)
                await websocket.send_str(current().trace_id)
                return websocket

            app = web.Application(middlewares=[AiohttpMiddleware()])
            app.router.add_get("/socket", handler)
            runner = web.AppRunner(app, access_log=None)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            try:
                from aiohttp import ClientSession

                headers = {
                    "traceparent": (
                        "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
                    )
                }
                async with (
                    ClientSession() as session,
                    session.ws_connect(
                        f"http://127.0.0.1:{port}/socket", headers=headers
                    ) as websocket,
                ):
                    message = await websocket.receive()
            finally:
                await runner.cleanup()

            self.assertEqual("4bf92f3577b34da6a3ce929d0e0e4736", message.data)

        asyncio.run(scenario())

    @unittest.skipUnless(web is not None, "aiohttp extra is not installed")
    def test_deferred_async_response_body_runs_outside_the_request_context(self):
        async def scenario():
            seen = {}

            async def body():
                seen["context"] = current()
                yield b"ok"

            async def handler(request):
                return web.Response(body=body())

            app = web.Application(middlewares=[AiohttpMiddleware()])
            app.router.add_get("/deferred", handler)
            runner = web.AppRunner(app, access_log=None)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            try:
                from aiohttp import ClientSession

                async with (
                    ClientSession() as session,
                    session.get(f"http://127.0.0.1:{port}/deferred") as response,
                ):
                    self.assertEqual(b"ok", await response.read())
            finally:
                await runner.cleanup()

            self.assertIsNone(seen["context"])

        asyncio.run(scenario())
