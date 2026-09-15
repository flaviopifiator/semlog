"""Execution modes (LM-001, LP-010 scoped): `full` (default, today's
behavior), `hybrid` (root untouched, `semlog=True` routes to JSON once),
`off` (as if semlog were not installed, except the keyword never raises).
State lives in one module-level `types.SimpleNamespace`, not a class
(CP-017's class budget)."""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

from . import _transport

MODES = ("full", "hybrid", "off")

# LM-002: the internal marker attribute a marked, armed call attaches to
# its `extra`; never visible in any output, since hybrid's routing wrapper
# (WU4) pops it before any handler ever runs.
MARKER = "_semlog_marked"

# `marking` starts False: no hybrid routing is armed at import (design D2).
state = SimpleNamespace(mode="full", marking=False)


def _invalid_mode(value, source):
    return ValueError(f"mode must be one of {MODES}, got {value!r} from {source}")


def _pyproject_mode(search_dir):
    """Read `[tool.semlog].mode` from the nearest `pyproject.toml`, walking
    up from `search_dir` (SI-001/SI-005's existing precedent in
    `_identity._read_pyproject`); `None` when unavailable, unreadable, or
    on Python 3.10 (no `tomllib`)."""
    if sys.version_info < (3, 11):
        return None
    here = Path(search_dir if search_dir is not None else Path.cwd()).resolve()
    path = next(
        (c for d in (here, *here.parents) if (c := d / "pyproject.toml").is_file()),
        None,
    )
    if path is None:
        return None
    import tomllib

    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    return data.get("tool", {}).get("semlog", {}).get("mode")


def resolve_mode(explicit, *, search_dir=None):
    """LM-001's precedence: the `mode` parameter, then `SEMLOG_MODE`, then
    `[tool.semlog].mode` (3.11+ only), then the `"full"` default. Raises
    `ValueError` naming the invalid value and its source."""
    if explicit is not None:
        if explicit not in MODES:
            raise _invalid_mode(explicit, "the mode parameter")
        return explicit
    raw_env = os.environ.get("SEMLOG_MODE")
    if raw_env:
        if raw_env not in MODES:
            raise _invalid_mode(raw_env, "the SEMLOG_MODE environment variable")
        return raw_env
    pyproject_mode = _pyproject_mode(search_dir)
    if pyproject_mode is not None:
        if pyproject_mode not in MODES:
            raise _invalid_mode(pyproject_mode, "[tool.semlog].mode in pyproject.toml")
        return pyproject_mode
    return "full"


def _initial_mode():
    """Import-time mode, no file I/O: `SEMLOG_MODE` if valid, else `"full"`
    (design D2). Off is honored before `configure()` ever runs; an invalid
    value here is left for `configure()`'s own resolution to raise on."""
    raw = os.environ.get("SEMLOG_MODE")
    return raw if raw in MODES else "full"


state.mode = _initial_mode()


def is_off():
    """Shared off-mode guard (LM-005), reused by `_context.py` and
    `_middleware.py` instead of each comparing `state.mode` inline."""
    return state.mode == "off"


def _log(
    self,
    level,
    msg,
    args,
    exc_info=None,
    extra=None,
    stack_info=False,
    stacklevel=1,
    semlog=False,
    **kwargs,
):
    """`logging.Logger._log` replacement (LM-002): accepts the keyword-only
    `semlog` argument in every mode, before `configure()` ever runs, and
    never raises. Only attaches `MARKER` while `state.marking` is armed
    (hybrid, once its pipeline is installed -- WU4); the caller's `extra`
    is never mutated in place, only ever copied. `stacklevel` is bumped by
    exactly one to compensate for this wrapper's own extra call frame, so
    caller-visible metadata (`filename`, `lineno`, `funcName`, `module`)
    stays identical to the same call site without the keyword."""
    if semlog and state.marking:
        extra = {**extra, MARKER: True} if extra else {MARKER: True}
    _log._semlog_original(
        self, level, msg, args, exc_info, extra, stack_info, stacklevel + 1, **kwargs
    )


def _arm_keyword():
    """Patch `logging.Logger._log` at import time; idempotent via the true
    original stashed on the wrapper itself, so a defensive re-arm never
    wraps twice (which would double the `stacklevel` bump)."""
    current = logging.Logger._log
    _log._semlog_original = getattr(current, "_semlog_original", current)
    logging.Logger._log = _log


_arm_keyword()


# LM-003 (design D4): the thread-local dispatch identity that hides a
# marked record from `StreamHandler` instances and subclasses during the
# ONE dispatch pass that follows semlog's own direct delivery; a nested
# dispatch (a handler that itself logs) restores the outer identity in
# `finally`, so nesting can never leak into the wrong record.
_dispatching = threading.local()

_routing_armed = False


def arm_routing():
    """Arm hybrid's routing wrappers once per process, never disarmed
    (design D4): a marked record reaches semlog's own installed handler
    exactly once, then dispatches through the original `callHandlers` with
    that record hidden only from `logging.StreamHandler` instances and
    subclasses; every other handler (for example a non-`StreamHandler`
    observer) receives the record unchanged, marker already gone."""
    global _routing_armed
    if _routing_armed:
        return
    _routing_armed = True

    original_call_handlers = logging.Logger.callHandlers
    original_stream_handle = logging.StreamHandler.handle

    def _call_handlers(self, record):
        handler = _transport._state.handler
        if handler is None or record.__dict__.pop(MARKER, None) is None:
            return original_call_handlers(self, record)
        handler.handle(record)
        previous = getattr(_dispatching, "record", None)
        _dispatching.record = record
        try:
            return original_call_handlers(self, record)
        finally:
            _dispatching.record = previous

    def _stream_handle(self, record):
        if getattr(_dispatching, "record", None) is record:
            return False
        return original_stream_handle(self, record)

    logging.Logger.callHandlers = _call_handlers
    logging.StreamHandler.handle = _stream_handle
