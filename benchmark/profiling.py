"""Profile mode: where semlog's `Formatter.format` spends its time.

A diagnostic aid, never a source of published numbers: `cProfile` adds
per-call overhead of its own, so the published figures always come from
the `time.perf_counter_ns` scenarios (CP-012). Stdlib only (`cProfile`,
`pstats`). The profiled record has the same shape as the `semlog`
subject's: one event name plus three attributes, no bound context.
"""

from __future__ import annotations

import cProfile
import io
import logging
import pstats

from .subjects import _semlog_identity


def _bench_record():
    logger = logging.getLogger("benchmark.profiling")
    return logger.makeRecord(
        logger.name,
        logging.INFO,
        __file__,
        1,
        "bench.request.completed",
        (),
        None,
        extra={
            "app.user_id": "profile",
            "app.request_id": "0199358a-7c2e-7b1d-8f3a-2c9e4b6d1a0f",
            "app.duration_ms": 1.5,
        },
    )


def profile_formatter(calls=20_000, top=20, sort="tottime"):
    """Format `calls` records under `cProfile` and return the `top` entries
    of the `pstats` report, sorted by `sort` (`tottime` or `cumulative`)."""
    if top < 1:
        raise ValueError(f"top must be a positive number of entries, got {top!r}")
    from semlog._format import Formatter

    formatter = Formatter(identity=_semlog_identity(), namespace="app")
    record = _bench_record()
    for _ in range(200):  # warm-up outside the profiled window
        formatter.format(record)
    profiler = cProfile.Profile()
    profiler.enable()
    for _ in range(calls):
        formatter.format(record)
    profiler.disable()
    out = io.StringIO()
    stats = pstats.Stats(profiler, stream=out)
    stats.strip_dirs().sort_stats(sort).print_stats(top)
    return f"# Formatter.format profile ({calls} calls, top {top} by {sort})\n" + (
        out.getvalue()
    )
