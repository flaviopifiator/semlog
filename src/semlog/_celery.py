"""Celery integration (`semlog.celery(app)`; STANDARDS.md CEL-001, CEL-002,
CEL-003, CEL-004, CEL-005, CEL-006, CEL-007): per-task span continuation,
publish-side inject, worker/beat/task logging ownership, and prefork
flush. Reuses `operation()`, `inject()`, `capture_loggers()` and `flush()`
(CP-016: no new pipeline stage, no transport change, no new class).
`celery.signals` is imported lazily, only inside `celery()` and
`_on_worker_process_init` themselves, so `import semlog` never needs
Celery installed (CEL-001).

Every receiver body is wrapped in `try/except Exception: pass` (the same
pattern as `_middleware.py`'s HTM-007 request event, SC-3): Celery's own
`import_modules` signal re-raises whatever a connected receiver raises,
aborting worker/beat startup, while its other signals (`task_prerun`,
`task_postrun`, `before_task_publish`, `after_setup_task_logger`,
`worker_process_init`, `worker_process_shutdown`) only log a raising
receiver's exception and continue -- but a receiver connected here must
never depend on which behavior applies, so every one of them stays
defensive regardless."""

from __future__ import annotations

import logging

from . import _modes, _transport
from ._context import inject, operation
from ._transport import capture_loggers, flush

# One scope per in-flight task, keyed by `id(task.request)` so postrun
# always finds and closes the exact scope prerun opened for that same
# task execution (design D3), even with several tasks running
# concurrently in the same process (threads pool).
_scopes: dict[int, object] = {}


def celery(app, /) -> None:
    """Wire per-task span continuation (CEL-005) and publish-side inject
    (CEL-006) for `app`. Idempotent (CEL-001): each receiver connects
    under a fixed `dispatch_uid`, so a repeated call -- with the same app
    instance or a different one -- connects nothing new. `app` must be a
    real Celery app: `None` is rejected up front, since it would later
    broaden a per-app receiver to every sender instead of scoping it to
    one app."""
    if app is None:
        raise TypeError("semlog.celery() requires a Celery app, got None")
    from celery import signals

    signals.task_prerun.connect(
        _on_task_prerun, weak=False, dispatch_uid="semlog.celery.task_prerun"
    )
    signals.task_postrun.connect(
        _on_task_postrun, weak=False, dispatch_uid="semlog.celery.task_postrun"
    )
    signals.before_task_publish.connect(
        _on_before_task_publish,
        weak=False,
        dispatch_uid="semlog.celery.before_task_publish",
    )
    signals.import_modules.connect(
        _on_import_modules,
        sender=app,
        weak=False,
        dispatch_uid="semlog.celery.import_modules",
    )
    signals.after_setup_task_logger.connect(
        _on_after_setup_task_logger,
        weak=False,
        dispatch_uid="semlog.celery.after_setup_task_logger",
    )
    signals.worker_process_init.connect(
        _on_worker_process_init,
        weak=False,
        dispatch_uid="semlog.celery.worker_process_init",
    )


def _carrier(request):
    """Build an `inject()`-shaped header mapping from a task's `Context`
    (design D3/B1). Celery 5.6.x's worker path, and eager
    `apply(headers=...)` on every supported version, populate
    `request.headers`; Celery 5.2.7's worker path never does
    (`Context.__init__` only calls `update()`), so the trace fields land
    as top-level `Context` attributes instead, read through
    `request.get(...)`. Returns `None` when neither shape carries a
    `traceparent`."""
    headers = request.headers or {}
    if headers.get("traceparent"):
        return headers
    carrier = {
        key: value
        for key in ("traceparent", "tracestate", "baggage")
        if (value := request.get(key))
    }
    return carrier if "traceparent" in carrier else None


def _on_task_prerun(task=None, **kwargs):
    try:
        if _modes.is_off():
            return
        request = task.request
        scope = operation(_carrier(request))
        scope.__enter__()
        _scopes[id(request)] = scope
    except Exception:  # noqa: BLE001, S110 -- SC-3: no receiver may raise into Celery
        pass


def _on_task_postrun(task=None, **kwargs):
    # No `is_off()` guard here (validation-1 m1): a task can start in
    # `full` mode and have the process mode flip to `off` before it
    # finishes, and this must still close whatever `_on_task_prerun`
    # opened -- otherwise both the `_scopes` entry and the still-pushed
    # context leak onto whatever runs next on this thread. `_scopes.pop`
    # is already a harmless no-op when prerun itself skipped (off from
    # the start, or it never ran at all).
    try:
        scope = _scopes.pop(id(task.request), None)
        if scope is not None:
            scope.__exit__(None, None, None)
    except Exception:  # noqa: BLE001, S110 -- SC-3
        pass


def _on_before_task_publish(headers=None, **kwargs):
    try:
        if _modes.is_off() or headers is None:
            return
        inject(headers)
    except Exception:  # noqa: BLE001, S110 -- SC-3
        pass


def _owns_root():
    """True only when semlog's own root handler (installed by a
    `full`-mode `configure()` call) is both the current transport handler
    AND still attached to the root logger (design D1/NIT 3): a later
    `dictConfig` call, or an explicit hijack that detached it, must leave
    Celery vanilla instead of silently capturing loggers onto a handler
    nothing delivers records through anymore. hybrid and off never own
    root: CEL-002's logging-ownership receivers below are inert in both,
    matching CEL-003's "hybrid adds only span, inject, flush" and
    CEL-004's "off produces no side effect"."""
    if _modes.state.mode != "full":
        return False
    handler = _transport._state.handler
    return handler is not None and handler in logging.getLogger().handlers


def _on_import_modules(sender=None, **kwargs):
    # design D1: a lazy write, scoped to this one app -- `celery()` connects
    # this receiver with `sender=app`, so two apps calling `celery(app)`
    # each get their own independent flag instead of sharing one receiver
    # (design D6). Never writes `worker_redirect_stdouts` (addendum A7,
    # decision #376 amended): only the hijack flag.
    try:
        if _owns_root():
            sender.conf.worker_hijack_root_logger = False
    except Exception:  # noqa: BLE001, S110 -- SC-3: Celery's loader
        # genuinely re-raises whatever this receiver raises (aborting
        # worker/beat startup), so this guard is load-bearing here, not
        # merely defensive like the other receivers in this module.
        pass


def _on_after_setup_task_logger(sender=None, logger=None, **kwargs):
    # design D2: fires after Celery has already attached its own
    # `TaskFormatter` handler (propagate=0) and its multiprocessing
    # handler; capturing both here routes `celery.task`/`get_task_logger()`
    # children and `multiprocessing` through the same pipeline handler
    # instead of a separate one, while leaving Celery's own level in place
    # so `--loglevel` still governs it.
    try:
        if _owns_root():
            capture_loggers(("celery.task", "multiprocessing"))
    except Exception:  # noqa: BLE001, S110 -- SC-3
        pass


def _on_worker_process_init(sender=None, **kwargs):
    # design D5: connects the shutdown receiver from INSIDE this receiver
    # instead of at `celery(app)` call time. `worker_process_init` fires
    # only after `import_default_modules()` has already run in this same
    # forked child (`concurrency/prefork.py`'s `process_initializer`), so
    # any import-time `worker_process_shutdown` receiver a task module
    # connects during that import is guaranteed to already be registered
    # -- and therefore to run BEFORE this one connects semlog's own,
    # which is what lets semlog's flush observe every record a
    # discriminating import-time receiver enqueued before shutdown.
    try:
        from celery import signals

        signals.worker_process_shutdown.connect(
            _on_worker_process_shutdown,
            weak=False,
            dispatch_uid="semlog.celery.worker_process_shutdown",
        )
    except Exception:  # noqa: BLE001, S110 -- SC-3
        pass


def _on_worker_process_shutdown(sender=None, pid=None, exitcode=None, **kwargs):
    # design D5, CEL-007: drains every record enqueued before this child
    # exits. `off` is checked explicitly (CEL-004): `flush()` is already a
    # no-op when no pipeline was ever installed, but a process
    # reconfigured from `full`/`hybrid` to `off` mid-life still has one
    # attached (off is a process-start switch, not a live toggle, per the
    # agent guide), so this guard still matters here.
    try:
        if _modes.is_off():
            return
        flush(timeout=5)
    except Exception:  # noqa: BLE001, S110 -- SC-3
        pass
