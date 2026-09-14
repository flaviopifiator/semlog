"""Concurrent-contention benchmark scenario (task 5.2; STANDARDS.md
CP-007). This is the one benchmark scenario that must run in the default
suite (semlog and the standard library only -- no `loguru`/`structlog`
needed), because it is what finally proves CP-007: the last id Phase 4
left in `tests/test_traceability.py`'s `NOT_YET_PROVEN` allowlist. That
allowlist and its machinery are now removed from `test_traceability.py`
(task 4.33/5.2): once this file's `Proves: CP-007` citation exists,
coverage is unconditional again, with no allowlist exception left.

Why not an absolute wall-clock threshold: any fixed nanosecond bound is
flaky on a loaded machine (CI runners, a busy laptop). Instead this test
proves the ABSENCE of hot-path serialization with a self-contained,
machine-load-tolerant construction: a deliberately slow sink (a fixed,
known artificial delay per write, standing in for a slow real
destination) is wired ONLY into the writer thread, never into the call
site. If semlog's call-site path ever blocked on a lock held during that
slow write (the violation CP-007 forbids), completing N concurrent calls
would cost at least ``N * slow_write_delay`` -- a quantity we control
and know exactly, not a guess about CPU speed. The real, compliant
design lets the calls complete in a small, bounded fraction of that
figure, because the slow write happens later, off the hot path, on the
writer thread alone. A second, complementary check compares one thread
doing all the calls against many threads doing the same total call
count IN THE SAME RUN (the "scaling behavior against a baseline
measured in the same run" the design calls for): a hot-path lock held
across threads would make the multi-thread run take much longer than
the single-thread run for the same total work; the real design keeps
them within a small, generous factor of each other.
"""

from __future__ import annotations

import unittest

from benchmark import scenarios


class ConcurrentContentionTests(unittest.TestCase):
    def test_concurrent_calls_never_wait_on_the_slow_writer(self):
        """Proves: CP-007

        Deterministic assertion #1 (absence of serialization): the total
        wall-clock time to perform ``total_calls`` concurrent `emit()`
        calls must be a small fraction of ``total_calls *
        slow_write_delay_s`` -- the cost a design that blocks the call
        site on the writer's I/O would incur. The margin (10x) is wide
        enough to absorb ordinary scheduling noise on a busy machine
        while still being meaningless for anything that actually waits
        on the slow sink even once per batch.
        """
        result = scenarios.contention_scenario(
            threads=8, calls_per_thread=100, slow_write_delay_s=0.002
        )
        naive_fully_serialized_ns = (
            result["total_calls"] * result["slow_write_delay_ns"]
        )
        self.assertLess(
            result["concurrent_ns"],
            naive_fully_serialized_ns / 10,
            "concurrent emit() calls took long enough to suggest the call "
            "site is waiting on the slow writer (CP-007 violation)",
        )

    def test_single_thread_also_never_waits_on_the_slow_writer(self):
        """Proves: CP-007

        Triangulation: the same absence-of-serialization property must
        hold with a single thread too (rules out "it only looks fast
        because threads happen to interleave the wait")."""
        result = scenarios.contention_scenario(
            threads=1, calls_per_thread=100, slow_write_delay_s=0.002
        )
        naive_fully_serialized_ns = (
            result["total_calls"] * result["slow_write_delay_ns"]
        )
        self.assertLess(result["concurrent_ns"], naive_fully_serialized_ns / 10)

    def test_eight_threads_scale_within_a_bounded_factor_of_one_thread(self):
        """Proves: CP-007

        Deterministic assertion #2 (scaling behavior against a baseline
        measured in the SAME run): 8 threads doing the SAME total call
        count as 1 thread must not take dramatically longer. A hot-path
        lock held across threads would show the opposite: multi-thread
        wall time approaching or exceeding thread-count times the
        single-thread figure. The bound (3x) is generous on purpose --
        this checks for the absence of a specific pathology, not for
        perfect linear speedup, which Python's GIL rules out anyway for
        CPU-bound formatting work.
        """
        same_total_calls = 800
        single = scenarios.contention_scenario(
            threads=1, calls_per_thread=same_total_calls, slow_write_delay_s=0.0
        )
        multi = scenarios.contention_scenario(
            threads=8, calls_per_thread=same_total_calls // 8, slow_write_delay_s=0.0
        )
        self.assertEqual(single["total_calls"], multi["total_calls"])
        self.assertLess(multi["concurrent_ns"], single["concurrent_ns"] * 3)


if __name__ == "__main__":
    unittest.main()
