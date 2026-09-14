"""Benchmark scenarios (task 5.2): steady-state single-thread throughput,
concurrent contention (STANDARDS.md CP-007), and per-record memory
(`tracemalloc`, CP-006/CP-012). Every scenario measures an already-built
subject; subject setup cost never pollutes a measured window.
"""

from __future__ import annotations

import threading
import time
import tracemalloc

from . import timing
from .subjects import build_semlog_pipeline


def warm_up(emit, n=200):
    """Run `n` throwaway calls before measuring, so import-time and
    first-call caching costs (event-name classification cache, redaction
    decisions, etc.) never leak into a measurement."""
    for i in range(n):
        emit(user_id="warmup", request_id=str(i), duration_ms=0.0)


def steady_state_throughput(emit, n=5000, warmup=200):
    """One thread, `n` sequential calls; returns one nanosecond sample
    per call (never a single aggregate number: `timing.summarize` reports
    median and spread over these samples, per the disclosed methodology).
    """
    warm_up(emit, warmup)
    samples = []
    for i in range(n):
        start = timing.now_ns()
        emit(user_id="steady", request_id=str(i), duration_ms=1.5)
        samples.append(timing.elapsed_ns(start))
    return samples


def memory_per_record(make_subject, sink_factory, n=3000, warmup=200):
    """Bytes allocated per record, measured with `tracemalloc` in its own
    dedicated run (tracing itself slows execution, so it is never mixed
    into a timing run; design #162 section 10)."""
    with sink_factory() as sink:
        emit, shutdown = make_subject(sink)
        try:
            warm_up(emit, warmup)
            tracemalloc.start()
            try:
                before = tracemalloc.get_traced_memory()[0]
                for i in range(n):
                    emit(user_id="mem", request_id=str(i), duration_ms=1.5)
                after = tracemalloc.get_traced_memory()[0]
            finally:
                tracemalloc.stop()
        finally:
            shutdown()
    return (after - before) / n


class _SlowSink:
    """A writable binary sink that sleeps `delay_s` on every write,
    standing in for a slow real destination (a loaded disk, a remote log
    shipper). Used only by `contention_scenario`: if semlog's call site
    ever blocked on this sink, the whole scenario would cost at least
    ``total_calls * delay_s`` -- a figure this scenario computes and
    compares against directly (STANDARDS.md CP-007)."""

    def __init__(self, delay_s):
        self._delay_s = delay_s

    def write(self, data):
        if self._delay_s:
            time.sleep(self._delay_s)
        return len(data)

    def flush(self):
        pass


def contention_scenario(*, threads, calls_per_thread, slow_write_delay_s):
    """Run `threads` concurrent threads, each performing `calls_per_thread`
    `emit()` calls against ONE shared semlog pipeline whose writer is
    wired to a deliberately slow sink. Returns wall-clock timing for the
    whole batch of `emit()` calls (never including the writer's own
    drain-on-shutdown, which happens afterward and is deliberately
    excluded from the timed window).
    """
    total_calls = threads * calls_per_thread
    # A queue large enough that overflow policy never engages: this
    # scenario measures the call-site path, not the bounded-queue policy
    # (that is `tests/test_transport.py`'s job, LP-007/CP-006).
    handler, writer = build_semlog_pipeline(
        _SlowSink(slow_write_delay_s), queue_maxsize=total_calls * 2
    )
    import logging

    logger = logging.getLogger(f"benchmark.contention.{id(handler)}")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    def emit(thread_id, i):
        logger.info(
            "bench.request.completed",
            extra={
                "app.user_id": f"t{thread_id}",
                "app.request_id": str(i),
                "app.duration_ms": 1.5,
            },
        )

    try:
        for i in range(50):  # warm-up: outside the timed window
            emit("warmup", i)

        def _worker(thread_id):
            for i in range(calls_per_thread):
                emit(thread_id, i)

        worker_threads = [
            threading.Thread(target=_worker, args=(t,)) for t in range(threads)
        ]
        start = timing.now_ns()
        for t in worker_threads:
            t.start()
        for t in worker_threads:
            t.join()
        concurrent_ns = timing.elapsed_ns(start)
    finally:
        try:
            writer.stop()
        except AttributeError:
            pass
        logger.handlers = []

    return {
        "threads": threads,
        "calls_per_thread": calls_per_thread,
        "total_calls": total_calls,
        "slow_write_delay_ns": int(slow_write_delay_s * 1_000_000_000),
        "concurrent_ns": concurrent_ns,
    }
