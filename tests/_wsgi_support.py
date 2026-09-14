"""Shared WSGI test helper (CP-010: stdlib `wsgiref` only, no third-party
WSGI test client); not itself a `test*.py` file, so the traceability
checker skips it.
"""

from __future__ import annotations

from wsgiref.util import setup_testing_defaults


def environ(headers=None, **overrides):
    """A stdlib-only WSGI `environ`, seeded via `wsgiref.util.setup_testing_defaults`."""
    built = {}
    setup_testing_defaults(built)
    for key, value in (headers or {}).items():
        built["HTTP_" + key.upper().replace("-", "_")] = value
    built.update(overrides)
    return built


def recording_start_response():
    """A fresh PEP 3333 `start_response` callable with its own `.calls` list."""
    calls = []

    def start_response(status, headers, exc_info=None):
        calls.append((status, headers))

    start_response.calls = calls
    return start_response
