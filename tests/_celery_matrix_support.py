"""Shared task/burst/publish wiring for the Celery CLI subprocess matrix
fixtures (`tests/_celery_plain_app.py`, `tests/_celery_django/celery_app.py`;
`tests/test_celery_matrix.py`, phase 2's subprocess matrix, STANDARDS.md
CEL-002/003/007, CP-020). The two fixture layouts differ only in how
`app`/`configure()`/`semlog.celery(app)` are wired (plain module vs.
Django `AppConfig.ready()`); everything the fixture actually DOES once
wired lives here, once, so both layouts stay in lockstep. Not itself a
`test*.py` file, so the traceability checker skips it.

Recognized environment variables (set by `tests/test_celery_matrix.py`
before launching the `celery` CLI subprocess):

- `SEMLOG_MODE`: read by semlog itself (`full`/`hybrid`/`off`).
- `SEMLOG_CELERY_INTEGRATE`: `"1"` (default) calls `semlog.celery(app)`;
  `"0"` leaves Celery entirely unwired, for `HybridByteIdentityTests`'s
  own baseline-without-`celery(app)` run.
- `SEMLOG_CELERY_BURST`: `"1"` connects an import-time `worker_process_
  shutdown` receiver (prefork child only) that emits `SEMLOG_CELERY_
  BURST_COUNT` (default 20000) plain records before semlog's own flush
  receiver runs -- design M3's discriminating fixture for CEL-007's
  prefork clause.
- `SEMLOG_CELERY_BURST_SOLO`: the solo-pool counterpart of the flag
  above, connected to `worker_shutdown` instead (the only lifecycle
  signal a solo pool ever sends -- it never sends `worker_process_
  shutdown`, since it never forks), for CEL-007's solo clause.
- `SEMLOG_CELERY_NO_SLEEP`: `"1"` replaces `billiard.pool.time.sleep`
  with a no-op, removing the ~1s grace period that would otherwise mask
  a missing flush (design M3).
- `SEMLOG_CELERY_DONE_FILE`: path this module creates once the marked
  task has finished running, so the driving test knows it is safe to
  send SIGTERM.
"""

from __future__ import annotations

import logging
import os
import threading

from celery import signals
from celery.utils.log import get_task_logger

# A fixed, known traceparent (CEL-005/006): the publisher below sets this
# directly on the outbound headers (no active `operation()` span at
# publish time inside a bare `worker_ready` receiver), so the resulting
# task record's `trace_id` is deterministic and assertable.
TRACE_ID = "f" * 32
TRACEPARENT = f"00-{TRACE_ID}-{'1' * 16}-01"

BURST_COUNT = int(os.environ.get("SEMLOG_CELERY_BURST_COUNT", "20000"))

task_logger = get_task_logger("semlog_matrix.task")
_burst_logger = logging.getLogger("semlog.matrix.burst")


def _touch_done_file():
    done_file = os.environ.get("SEMLOG_CELERY_DONE_FILE")
    if done_file:
        with open(done_file, "w", encoding="utf-8") as handle:
            handle.write("done\n")


def maybe_patch_no_sleep():
    """design M3: removes billiard's ~1s post-fork grace period, which
    would otherwise be long enough to mask a missing CEL-007 flush by
    accident."""
    if os.environ.get("SEMLOG_CELERY_NO_SLEEP") == "1":
        import billiard.pool

        billiard.pool.time.sleep = lambda *_args, **_kwargs: None


def register(app):
    """Register the marked task, the worker-ready publisher, and (when
    `SEMLOG_CELERY_BURST=1`) the CEL-007 discriminating burst receivers,
    onto `app`. Returns the registered task."""

    @app.task(name="semlog_matrix.marked_task")
    def marked_task():
        task_logger.warning("matrix.task_logger.warning")
        task_logger.info("matrix.task_logger.info")
        task_logger.debug("matrix.task_logger.debug")
        print("matrix-print-token")  # proves the print/redirect guard
        _touch_done_file()
        return "ok"

    def _publish():
        marked_task.apply_async(headers={"traceparent": TRACEPARENT})

    @signals.worker_ready.connect(weak=False)
    def _on_worker_ready(**kwargs):
        # A background thread, not a direct call: `worker_ready` fires on
        # the same thread that is about to enter the worker's own consume
        # loop (solo runs that loop on this very thread), so publishing
        # here and returning immediately keeps that thread free to start
        # consuming -- and consequently to pick up and run this task.
        threading.Thread(target=_publish, daemon=True).start()

    def _emit_burst(**kwargs):
        for i in range(BURST_COUNT):
            _burst_logger.info("matrix.burst.record", extra={"app.i": i})

    if os.environ.get("SEMLOG_CELERY_BURST") == "1":
        # Prefork clause: fires only in the forked child, before semlog's
        # own shutdown receiver connects (design D5) -- the discriminating
        # fixture for `PreforkShutdownFlushTests`.
        signals.worker_process_shutdown.connect(_emit_burst, weak=False)

    if os.environ.get("SEMLOG_CELERY_BURST_SOLO") == "1":
        # Solo clause: a solo pool never forks, so it never sends
        # `worker_process_shutdown` at all -- `worker_shutdown` is the
        # only lifecycle signal it does send, for `SoloExitFlushTests`.
        signals.worker_shutdown.connect(_emit_burst, weak=False)

    return marked_task
