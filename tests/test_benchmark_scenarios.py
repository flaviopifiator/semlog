"""Steady-state throughput and per-record memory scenario tests (task
5.2; STANDARDS.md CP-005/CP-006/CP-012). `loguru`/`structlog` variants
skip gracefully when those packages are not installed.
"""

from __future__ import annotations

import unittest

from benchmark import scenarios, sinks, subjects, timing


class SteadyStateThroughputTests(unittest.TestCase):
    """Proves: CP-005"""

    def test_returns_one_sample_per_call_after_warmup(self):
        with sinks.devnull_sink() as sink:
            emit, shutdown = subjects.make_stdlib_baseline(sink)
            try:
                samples = scenarios.steady_state_throughput(emit, n=50, warmup=10)
            finally:
                shutdown()
        self.assertEqual(50, len(samples))
        self.assertTrue(all(isinstance(s, int) and s >= 0 for s in samples))

    def test_summary_median_is_plausible_for_a_cheap_call(self):
        with sinks.devnull_sink() as sink:
            emit, shutdown = subjects.make_stdlib_baseline(sink)
            try:
                samples = scenarios.steady_state_throughput(emit, n=200, warmup=20)
            finally:
                shutdown()
        summary = timing.summarize(samples)
        # A single in-memory JSON-format-and-write call is not free, but
        # it is also not seconds long; this is a generous sanity bound,
        # not a tuned performance target.
        self.assertLess(summary["median_ns"], 50_000_000)  # under 50ms/call


class MemoryPerRecordTests(unittest.TestCase):
    """Proves: CP-006

    Complements `tests/test_transport.py::QueueHandlerOverflowTests.
    test_queue_memory_bounded_under_sustained_overflow` (CP-006's
    existing unit-level proof) with a benchmark-level measurement using
    `tracemalloc`, per CP-012."""

    def test_semlog_memory_per_record_is_a_small_positive_number(self):
        bytes_per_record = scenarios.memory_per_record(
            subjects.make_semlog, sinks.devnull_sink, n=500, warmup=50
        )
        self.assertGreater(bytes_per_record, 0)
        self.assertLess(bytes_per_record, 100_000)  # generous upper bound

    def test_stdlib_baseline_memory_per_record_is_a_small_positive_number(self):
        bytes_per_record = scenarios.memory_per_record(
            subjects.make_stdlib_baseline, sinks.devnull_sink, n=500, warmup=50
        )
        self.assertGreater(bytes_per_record, 0)
        self.assertLess(bytes_per_record, 100_000)


if __name__ == "__main__":
    unittest.main()
