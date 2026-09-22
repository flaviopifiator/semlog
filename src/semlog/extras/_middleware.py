"""Middleware for optional framework integrations."""

from __future__ import annotations

import time

from .. import _modes
from .._context import pop, push, snapshot_from_headers
from .._middleware import _emit_request_event


class AiohttpMiddleware:
    """Bind semlog request context through an aiohttp handler call."""

    __middleware_version__ = 1

    def __init__(self, *, baggage_allow=None, log_requests=False):
        self._baggage_allow = baggage_allow
        self._log_requests = log_requests

    async def __call__(self, request, handler):
        if _modes.is_off():
            return await handler(request)
        snapshot = snapshot_from_headers(
            request.headers.get, baggage_allow=self._baggage_allow
        )
        token = push(snapshot)
        start = time.perf_counter_ns() if self._log_requests else None
        try:
            response = await handler(request)
            if self._log_requests:
                _emit_request_event(
                    request.method,
                    request.path,
                    response.status,
                    time.perf_counter_ns() - start,
                )
            return response
        except (ConnectionResetError, BrokenPipeError):
            raise
        except BaseException as exc:
            if any(
                cls.__module__ == "asyncio.exceptions"
                and cls.__name__ == "CancelledError"
                for cls in type(exc).__mro__
            ):
                raise
            if self._log_requests:
                status = getattr(exc, "status", None)
                if isinstance(status, int):
                    _emit_request_event(
                        request.method,
                        request.path,
                        status,
                        time.perf_counter_ns() - start,
                    )
                else:
                    _emit_request_event(
                        request.method,
                        request.path,
                        None,
                        time.perf_counter_ns() - start,
                        (type(exc), exc, exc.__traceback__),
                    )
            raise
        finally:
            pop(token)
