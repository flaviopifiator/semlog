"""Django `MIDDLEWARE`-list factory functions for `tests/test_django_matrix.py`
(HTM-010, HTM-011, HTM-012): a plain function is itself a fully valid
`MIDDLEWARE` entry (Django's own documented mechanism, `load_middleware`
calls it with exactly one positional argument just like a class), which
lets a test construct `DjangoMiddleware` with `log_requests=True` while
still going through the real dispatch path (`process_exception`,
`convert_exception_to_response`, the exception-middleware loop) --
something a bare dotted class path in `MIDDLEWARE` cannot do, since it
never receives keyword arguments. `SEMLOG_LOG_REQUESTS` is the only route
a real deployment has for this; `django_middleware_logging` exists purely
so exception capture and coexistence can prove the request-completion
event fires with the real final status.

`crashing_middleware` and `preempting_middleware` prove HTM-012's own two
permanent limitations: a middleware factory function can carry a
`process_exception` attribute directly, the same way Django's own
`load_middleware` looks for it on a class-based instance (`hasattr(mw_
instance, "process_exception")`).

Not itself a `test*.py` file, so the traceability checker skips it.
"""

from __future__ import annotations

from django.http import JsonResponse

import semlog


def django_middleware_logging(get_response):
    """`DjangoMiddleware` with `log_requests=True`, for `settings.MIDDLEWARE`."""
    return semlog.DjangoMiddleware(get_response, log_requests=True)


def crashing_middleware(get_response):
    """Raises in its own code, before ever calling `get_response` --
    Django's `convert_exception_to_response` wraps this middleware
    individually, so this exception is converted to a 500 at this exact
    layer and never reaches any other middleware's `process_exception`
    (HTM-012's first permanent limitation)."""

    def middleware(request):
        raise RuntimeError("outer-middleware-boom")

    return middleware


def preempting_middleware(get_response):
    """A `process_exception` that always returns a response, stopping
    Django's exception-middleware loop at the first non-`None` result
    (HTM-012's second permanent limitation): a middleware registered
    closer to the view than `DjangoMiddleware` pre-empts it."""

    def middleware(request):
        return get_response(request)

    def process_exception(request, exception):
        return JsonResponse({"preempted": True}, status=599)

    middleware.process_exception = process_exception
    return middleware
