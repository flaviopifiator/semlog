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
import tempfile
import threading

from django.http import FileResponse, JsonResponse, StreamingHttpResponse
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


def boom_view(request):
    """Raises unconditionally: Django's own `convert_exception_to_response`
    converts this into a 500 response, calling every registered
    middleware's `process_exception` first (HTM-011)."""
    raise RuntimeError("matrix-boom")


def stream_view(request):
    """A synchronous streaming body: each chunk's own log line proves the
    bound context survives generator consumption after the middleware
    chain has already returned (HTM-013)."""

    def body():
        for i in range(3):
            _LOGGER.info("matrix.stream_chunk", extra={"chunk": i})
            yield b"chunk-%d" % i

    return StreamingHttpResponse(body())


async def astream_view(request):
    """The async half of `stream_view` (HTM-013): absent on Django's
    oldest supported row, which has no `__aiter__` on
    `StreamingHttpResponse` at all."""

    async def body():
        for i in range(3):
            _LOGGER.info("matrix.astream_chunk", extra={"chunk": i})
            yield b"chunk-%d" % i

    return StreamingHttpResponse(body())


def file_view(request):
    """`FileResponse.file_to_stream` must survive untouched (HTM-013's
    carve-out, design D5): rebinding `streaming_content` would null it
    and disable the `wsgi.file_wrapper` sendfile path."""
    handle = tempfile.NamedTemporaryFile(suffix=".bin", delete=False)  # noqa: SIM115
    handle.write(b"file-body-bytes")
    handle.close()
    return FileResponse(open(handle.name, "rb"))


urlpatterns = [
    path("sync/", sync_view),
    path("async/", async_view),
    path("async-thread/", async_thread_view),
    path("boom/", boom_view),
    path("stream/", stream_view),
    path("astream/", astream_view),
    path("file/", file_view),
]
