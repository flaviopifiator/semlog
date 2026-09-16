"""Django `MIDDLEWARE`-list factory functions for `tests/test_django_matrix.py`
(HTM-010, HTM-011): a plain function is itself a fully valid `MIDDLEWARE`
entry (Django's own documented mechanism, `load_middleware` calls it with
exactly one positional argument just like a class), which lets a test
construct `DjangoMiddleware` with `log_requests=True` while still going
through the real dispatch path (`process_exception`, `convert_exception_
to_response`, the exception-middleware loop) -- something a bare dotted
class path in `MIDDLEWARE` cannot do, since it never receives keyword
arguments. `SEMLOG_LOG_REQUESTS` (a later work unit) is the only route a
real deployment has for this; these factories exist purely so exception
capture and coexistence, tested before that setting lands, can still
prove the request-completion event fires with the real final status.

Not itself a `test*.py` file, so the traceability checker skips it.
"""

from __future__ import annotations

import semlog


def django_middleware_logging(get_response):
    """`DjangoMiddleware` with `log_requests=True`, for `settings.MIDDLEWARE`."""
    return semlog.DjangoMiddleware(get_response, log_requests=True)
