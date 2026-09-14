"""Stage-breakdown scenario: where one semlog call spends its time.

Three cumulative stages, each measured with the same call shape as the
`semlog` subject (one event name plus three attributes) and the same
per-call sampling as `scenarios.steady_state_throughput`:

- ``dispatch``: stdlib `Logger.info` dispatch plus `LogRecord` creation,
  ending in a no-op handler. semlog pays this by design (it is built on
  `logging`); a logger that bypasses stdlib logging does not.
- ``format``: the same, plus semlog's real `Formatter.format` on the
  record, with the rendered line discarded.
- ``enqueue``: the real pipeline, exactly as `subjects.make_semlog` builds
  it (queue handler, bounded queue, writer thread draining to the sink).

The difference between two consecutive stages is the cost that stage adds.
"""

from __future__ import annotations

import logging

from . import scenarios, sinks, timing
from .subjects import _semlog_identity, _stop_writer, build_semlog_pipeline

STAGES = ("dispatch", "format", "enqueue")


class _FormatOnlyHandler(logging.NullHandler):
    """Formats the record with its formatter and discards the line; no lock,
    no I/O, no queue (the `format` stage)."""

    def handle(self, record):
        self.format(record)
        return True


def make_stage_emitter(stage, sink):
    """Return `(emit, shutdown)` for one stage; `emit` has the subject
    adapters' signature. Only the `enqueue` stage ever writes to `sink`."""
    from semlog._format import Formatter

    writer = None
    if stage == "dispatch":
        handler = logging.NullHandler()
    elif stage == "format":
        handler = _FormatOnlyHandler()
        handler.setFormatter(Formatter(identity=_semlog_identity(), namespace="app"))
    elif stage == "enqueue":
        handler, writer = build_semlog_pipeline(sink)
    else:
        raise ValueError(f"unknown stage {stage!r}; expected one of {STAGES}")

    logger = logging.getLogger(f"benchmark.stages.{stage}.{id(sink)}")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    def emit(*, user_id, request_id, duration_ms):
        logger.info(
            "bench.request.completed",
            extra={
                "app.user_id": user_id,
                "app.request_id": request_id,
                "app.duration_ms": duration_ms,
            },
        )

    def shutdown():
        if writer is not None:
            _stop_writer(writer)
        logger.handlers = []

    return emit, shutdown


def stage_breakdown(n=5000, warmup=200):
    """`{stage: timing.summarize(samples)}` for every stage, in order."""
    results = {}
    for stage in STAGES:
        with sinks.devnull_sink() as sink:
            emit, shutdown = make_stage_emitter(stage, sink)
            try:
                samples = scenarios.steady_state_throughput(emit, n=n, warmup=warmup)
            finally:
                shutdown()
        results[stage] = timing.summarize(samples)
    return results
