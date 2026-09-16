"""Minimal Django URLconf used only by the compatibility-matrix tests
(`tests/test_django_matrix.py`, CP-002). Imports Django names at module
scope deliberately: this module is reachable only through Django's own
`ROOT_URLCONF` import machinery, triggered lazily and only once Django is
confirmed installed and `django.setup()` is about to run -- it is never
imported when Django is absent. Not itself a `test*.py` file, so the
traceability checker skips it.
"""

from __future__ import annotations

import logging
import threading

from django.http import JsonResponse
from django.urls import path

_LOGGER = logging.getLogger("semlog.tests.django_matrix")


def sync_view(request):
    _LOGGER.info("matrix.sync_view")
    return JsonResponse({"kind": "sync"})


async def async_view(request):
    _LOGGER.info("matrix.async_view")
    return JsonResponse({"kind": "async"})


async def async_thread_view(request):
    """No JSON logging: the body itself carries the thread ident so a test
    can compare it against the thread that started the event loop, proving
    `DjangoMiddleware.__acall__` never hops to a `sync_to_async` worker
    thread (HTM-008, design decision D1)."""
    return JsonResponse({"thread_ident": threading.get_ident()})


urlpatterns = [
    path("sync/", sync_view),
    path("async/", async_view),
    path("async-thread/", async_thread_view),
]
