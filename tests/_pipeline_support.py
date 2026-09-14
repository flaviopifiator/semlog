"""Shared test helper: reset root-logger pipeline state after a `configure()`
call in a test (avoids leaking a `SemlogQueueHandler`/writer thread across
tests -- not itself a `test*.py` file, so the traceability checker skips it).
"""

from __future__ import annotations

import io
import logging

from semlog import _transport


class FakeStdout:
    """A `sys.stdout` stand-in exposing the `.buffer` attribute `Writer` writes to."""

    def __init__(self):
        self.buffer = io.BytesIO()


def reset_pipeline():
    """Stop any writer left running by `configure()` and detach root handlers."""
    if _transport._state.writer is not None:
        _transport._ignore(_transport._state.writer.stop, AttributeError)
    _transport._state.handler = None
    _transport._state.writer = None
    _transport._state.stream = None
    _transport._state.was_running = False
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(logging.WARNING)
