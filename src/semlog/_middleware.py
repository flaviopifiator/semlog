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
    if _modes.is_off():
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
        if _modes.is_off():
            # LM-005: as if semlog were not installed -- no scope pushed,
            # so inject()/bind() called from inside the app are inert.
            return self.app(environ, start_response)
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
        if scope["type"] != "http" or _modes.is_off():
            # LM-005: off is inert here too -- no scope, so inject()/bind()
            # called from inside the app are inert.
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


class DjangoMiddleware:
    """Django `settings.MIDDLEWARE` entry serving both synchronous and
    asynchronous deployments through exactly one class (HTM-008), shaped
    like Django's own dual-mode middleware idiom: `sync_capable`/
    `async_capable = True`, with the instance itself marked coroutine-like
    when `get_response` is async (design D1) so Django's own dispatch
    never hands our returned coroutine to a `sync_to_async` thread hop."""

    sync_capable = True
    async_capable = True

    def __init__(self, get_response, *, baggage_allow=None, log_requests=None):
        self.get_response = get_response
        self._baggage_allow = baggage_allow
        # Resolution from the `SEMLOG_LOG_REQUESTS` Django setting, and the
        # event this flag enables, both land in a later work unit; a plain
        # falsy default keeps the constructor's full signature (design's
        # interface) without behaving on it yet.
        self._log_requests = bool(log_requests)
        # HTM-008: `asyncio`/`inspect` are imported lazily here, never at
        # `semlog` import time (design D2), so `import semlog` stays free
        # of both even when Django itself is entirely absent.
        import inspect

        if hasattr(inspect, "markcoroutinefunction"):  # Python 3.12+
            self.async_mode = inspect.iscoroutinefunction(get_response)
            if self.async_mode:
                inspect.markcoroutinefunction(self)
            return
        # Python 3.10/3.11 (asgiref/sync.py:56-69, reproduced): a native
        # `async def` is detected by plain `inspect`, no `asyncio` import
        # needed. `asyncio` is only imported when either that already
        # found a coroutine function (to mark `self`) or `get_response`
        # itself carries a `_is_coroutine` marker (another dual-mode
        # middleware further down the chain) -- a plain, unmarked sync
        # callable never triggers the import at all (CP-008/HTM-008).
        self.async_mode = inspect.iscoroutinefunction(get_response)
        if not self.async_mode and hasattr(get_response, "_is_coroutine"):
            import asyncio

            self.async_mode = (
                get_response._is_coroutine is asyncio.coroutines._is_coroutine
            )
        if self.async_mode:
            import asyncio

            self._is_coroutine = asyncio.coroutines._is_coroutine

    def __call__(self, request):
        # Async marking check first: an async `get_response` always routes
        # through `__acall__`, which owns its own `is_off()` check.
        if self.async_mode:
            return self.__acall__(request)
        if _modes.is_off():
            # LM-005: as if semlog were not installed -- no scope pushed,
            # so inject()/bind() called from inside the view are inert.
            return self.get_response(request)
        snapshot = snapshot_from_headers(
            request.headers.get, baggage_allow=self._baggage_allow
        )
        token = push(snapshot)
        start = time.perf_counter_ns() if self._log_requests else None
        try:
            response = self.get_response(request)
            # Emit while OUR OWN snapshot is still bound, not after
            # popping (matches WSGIMiddleware/ASGIMiddleware precedent):
            # popping first would make the event read whatever context was
            # current before this middleware ran instead of its own.
            self._emit_completion_event(request, response, start)
        finally:
            pop(token)
        return response

    async def __acall__(self, request):
        if _modes.is_off():
            return await self.get_response(request)
        snapshot = snapshot_from_headers(
            request.headers.get, baggage_allow=self._baggage_allow
        )
        token = push(snapshot)
        start = time.perf_counter_ns() if self._log_requests else None
        try:
            response = await self.get_response(request)
            # Synchronous post-processing of the response, on the loop
            # thread (design D1): no `sync_to_async` hop occurred, so
            # reading a plain attribute here never raises.
            self._emit_completion_event(request, response, start)
        finally:
            pop(token)
        return response

    def process_exception(self, request, exception):
        """HTM-011: stash the exception without swallowing it, and always
        return `None` so Django's own exception handling is never
        modified. Short-circuits in `off` mode too, matching `__call__`/
        `__acall__`'s own true pass-through."""
        if _modes.is_off():
            return
        request._semlog_exc_info = (
            type(exception),
            exception,
            exception.__traceback__,
        )
        return

    def _emit_completion_event(self, request, response, start):
        """HTM-007/HTM-011: read then clear whatever `process_exception`
        stashed on `request`, regardless of `log_requests`, so no
        internal attribute survives on the request object; emit the
        optional completion event only when enabled, applying
        `_emit_request_event`'s existing INFO/ERROR selection against the
        real final `status_code`."""
        exc_info = request.__dict__.pop("_semlog_exc_info", None)
        if not self._log_requests:
            return
        _emit_request_event(
            request.method,
            request.path,
            response.status_code,
            time.perf_counter_ns() - start,
            exc_info,
        )
