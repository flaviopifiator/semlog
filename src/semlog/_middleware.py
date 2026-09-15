"""WSGI (PEP 3333) and pure ASGI 3.0 middlewares (design #162 §2.4/§2.5;
HTM-001..007, CP-010, CP-011). Each request/scope gets its own fresh
`contextvars.Context`; the caller's real thread/task context is never
mutated directly, so nothing can leak across requests (HTM-003/006)."""

from __future__ import annotations

import contextvars
import logging
import time

from . import _modes
from ._context import pop, push, snapshot_from_headers

# The optional HTM-007 completion event's own scope (design #162 §2.4);
# distinct from the internal "semlog" diagnostics channel (SI-003).
_REQUEST_LOGGER = logging.getLogger(__name__)


def _wsgi_header(environ):
    """A W3C-header getter reading a WSGI `environ` (`HTTP_TRACEPARENT`, ...)."""

    def get_header(name):
        return environ.get("HTTP_" + name.upper().replace("-", "_"))

    return get_header


def _emit_request_event(method, path, status_code, duration_ns, exc_info=None):
    """HTM-007's optional `http.server.request` event, shared by both
    middlewares: `http.request.method`, `url.path`,
    `http.response.status_code`, `event.duration` (ns); never `url.query`.
    A failure here must never reach the wrapped application. `off` mode
    emits nothing at all (LM-005); every other mode marks the event
    `semlog=True`, so hybrid routes it to JSON only, never legacy text
    (LM-003), while full keeps its existing unmarked-equivalent behavior."""
    if _modes.state.mode == "off":
        return
    try:
        _REQUEST_LOGGER.log(
            logging.ERROR if exc_info else logging.INFO,
            "http.server.request",
            extra={
                "http.request.method": method,
                "url.path": path,
                "http.response.status_code": status_code,
                "event.duration": duration_ns,
            },
            exc_info=exc_info,
            semlog=True,
        )
    except Exception:  # noqa: BLE001, S110 -- HTM-007: never break the app
        pass


def _wrap_iterable(ctx, iterable, token, on_complete=None):
    """Iterate/close `iterable` inside `ctx`, resetting only once fully
    consumed or closed (HTM-001/003): the WSGI streaming-body contract.
    `on_complete` (HTM-007), when given, runs just before the reset, so the
    request event covers the full streamed response lifecycle."""
    it = iter(iterable)
    try:
        while True:
            try:
                chunk = ctx.run(next, it)
            except StopIteration:
                return
            yield chunk
    finally:
        close = getattr(iterable, "close", None)
        if close is not None:
            ctx.run(close)
        if on_complete is not None:
            ctx.run(on_complete)
        ctx.run(pop, token)


def _capture_wsgi_status(start_response, holder):
    """Wrap `start_response` to capture the numeric status for HTM-007; a
    malformed status string never prevents the real call."""

    def wrapped(status, response_headers, exc_info=None):
        try:
            holder["status"] = int(status.split(" ", 1)[0])
        except (ValueError, AttributeError, IndexError):
            holder["status"] = None
        return start_response(status, response_headers, exc_info)

    return wrapped


class WSGIMiddleware:
    """PEP 3333 middleware binding request-scoped trace context (HTM-001)."""

    def __init__(self, app, *, baggage_allow=None, log_requests=False):
        self.app = app
        self._baggage_allow = baggage_allow
        self._log_requests = log_requests

    def __call__(self, environ, start_response):
        snapshot = snapshot_from_headers(
            _wsgi_header(environ), baggage_allow=self._baggage_allow
        )
        ctx = contextvars.Context()
        token = ctx.run(push, snapshot)
        if not self._log_requests:
            try:
                result = ctx.run(self.app, environ, start_response)
            except BaseException:
                ctx.run(pop, token)
                raise
            return _wrap_iterable(ctx, result, token)

        method = environ.get("REQUEST_METHOD")
        path = environ.get("PATH_INFO", "")
        holder = {"status": None}
        wrapped_start_response = _capture_wsgi_status(start_response, holder)
        start = time.perf_counter_ns()
        try:
            result = ctx.run(self.app, environ, wrapped_start_response)
        except BaseException as exc:
            duration = time.perf_counter_ns() - start
            ctx.run(
                _emit_request_event,
                method,
                path,
                holder["status"],
                duration,
                (type(exc), exc, exc.__traceback__),
            )
            ctx.run(pop, token)
            raise

        def on_complete():
            _emit_request_event(
                method, path, holder["status"], time.perf_counter_ns() - start
            )

        return _wrap_iterable(ctx, result, token, on_complete=on_complete)


def _asgi_header(scope):
    """A W3C-header getter reading raw ASGI `scope["headers"]` byte pairs."""
    headers = {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers", ())
    }
    return headers.get


def _capture_asgi_status(send, holder):
    """Wrap ASGI `send` to capture the response status for HTM-007."""

    async def wrapped(message):
        if message.get("type") == "http.response.start":
            holder["status"] = message.get("status")
        await send(message)

    return wrapped


class ASGIMiddleware:
    """Pure ASGI 3.0 middleware, `http` scope only (HTM-002/005); never
    `BaseHTTPMiddleware`."""

    def __init__(self, app, *, baggage_allow=None, log_requests=False):
        self.app = app
        self._baggage_allow = baggage_allow
        self._log_requests = log_requests

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        snapshot = snapshot_from_headers(
            _asgi_header(scope), baggage_allow=self._baggage_allow
        )
        token = push(snapshot)
        if not self._log_requests:
            try:
                await self.app(scope, receive, send)
            finally:
                pop(token)
            return

        method = scope.get("method")
        path = scope.get("path", "")
        holder = {"status": None}
        wrapped_send = _capture_asgi_status(send, holder)
        start = time.perf_counter_ns()
        try:
            try:
                await self.app(scope, receive, wrapped_send)
            except BaseException as exc:
                _emit_request_event(
                    method,
                    path,
                    holder["status"],
                    time.perf_counter_ns() - start,
                    (type(exc), exc, exc.__traceback__),
                )
                raise
            else:
                _emit_request_event(
                    method, path, holder["status"], time.perf_counter_ns() - start
                )
        finally:
            pop(token)
