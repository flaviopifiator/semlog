"""Execution modes (LM-001, LP-010 scoped): `full` (default, today's
behavior), `hybrid` (root untouched, `semlog=True` routes to JSON once),
`off` (as if semlog were not installed, except the keyword never raises).
State lives in one module-level `types.SimpleNamespace`, not a class
(CP-017's class budget)."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace

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
