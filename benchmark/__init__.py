"""Benchmark harness for SEMLOG (STANDARDS.md CP-005/CP-006/CP-007/CP-012).

Lives outside `src/semlog`: this package is never counted toward CP-017's
1,200-line package-source budget (that budget scopes `src/semlog/*.py`
only; see `tests/test_source_budget.py`), and it ships in neither the
sdist tree the library publishes as its contract nor the built wheel.

Stdlib-only measurement tools throughout (`time.perf_counter_ns`,
`tracemalloc`, `statistics`; `cProfile`/`pstats` only for the diagnostic
`--profile` mode, never for a published number); the only third-party
imports anywhere in this package are the two approved benchmark-comparison
subjects,
`loguru` and `structlog` (STANDARDS.md CP-008/CP-013), both imported
lazily inside the functions that need them so the rest of the harness,
and the whole default test suite, stays usable without them installed.
"""

from __future__ import annotations
