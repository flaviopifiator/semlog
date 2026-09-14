"""Stdlib-only timing helpers (STANDARDS.md CP-012): built entirely on
`time.perf_counter_ns` and `statistics`, no third-party benchmarking
package (no `pyperf`, no `pytest-benchmark`).
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Sequence


def now_ns() -> int:
    """One monotonic timestamp, in nanoseconds."""
    return time.perf_counter_ns()


def elapsed_ns(start_ns: int) -> int:
    """Nanoseconds elapsed since `start_ns` (`perf_counter_ns` is
    monotonic, so this is never negative for a `start_ns` it produced)."""
    return time.perf_counter_ns() - start_ns


def summarize(samples_ns: Sequence[int]) -> dict:
    """Median plus a spread (p10-p90 range) over `samples_ns`, never a
    single number alone (design #162 section 10's own protocol: "median
    and spread reported"). A single sample has zero spread. Raises
    `ValueError` on an empty sequence rather than silently returning a
    meaningless summary."""
    if not samples_ns:
        raise ValueError("summarize() needs at least one sample")
    ordered = sorted(samples_ns)
    median = statistics.median(ordered)
    if len(ordered) >= 10:
        deciles = statistics.quantiles(ordered, n=10)
        p10, p90 = deciles[0], deciles[-1]
    else:
        p10, p90 = ordered[0], ordered[-1]
    return {
        "n": len(ordered),
        "median_ns": median,
        "p10_ns": p10,
        "p90_ns": p90,
        "spread_ns": p90 - p10,
    }
