"""RED-first tests for the benchmark harness itself (task 5.1; STANDARDS.md
CP-005/CP-012). Strict TDD applies to the harness too: it is code, and its
timing helper and subject adapters are proven here before any scenario is
built on top of them.

The harness lives in ``benchmark/`` at the repository root, a sibling of
``src/`` and ``tests/``, never inside ``src/semlog`` -- it is not part of
the library's own budgeted source (CP-017 counts only ``src/semlog/*.py``).

``loguru``/``structlog`` subject tests skip gracefully when those packages
are not installed in the running interpreter: they are approved
benchmark-comparison subjects (CP-013), never runtime dependencies, and the
default suite must stay green without them (per the standing instruction).
"""

from __future__ import annotations

import importlib.util
import io
import json
import unittest

from benchmark import sinks, subjects, timing

_HAS_LOGURU = importlib.util.find_spec("loguru") is not None
_HAS_STRUCTLOG = importlib.util.find_spec("structlog") is not None


class TimingHelperPlausibilityTests(unittest.TestCase):
    """The timing helper is a thin, deterministic wrapper over
    `time.perf_counter_ns`/`statistics` (CP-012: stdlib measurement tools
    only); these tests prove it returns plausible values, not just that it
    imports."""

    def test_elapsed_ns_is_positive_after_measurable_work(self):
        start = timing.now_ns()
        total = sum(range(200_000))  # cheap, deterministic busy-work
        self.assertGreater(total, 0)
        elapsed = timing.elapsed_ns(start)
        self.assertGreater(elapsed, 0)
        self.assertLess(elapsed, 5_000_000_000)  # sane upper bound: under 5s

    def test_summarize_computes_median_and_spread_from_known_samples(self):
        samples = list(range(1, 101))  # exact, known distribution: 1..100
        result = timing.summarize(samples)
        self.assertEqual(100, result["n"])
        self.assertAlmostEqual(50.5, result["median_ns"])
        self.assertGreater(result["spread_ns"], 0)
        self.assertLessEqual(result["p10_ns"], result["median_ns"])
        self.assertLessEqual(result["median_ns"], result["p90_ns"])

    def test_summarize_on_a_single_sample_has_zero_spread(self):
        result = timing.summarize([42])
        self.assertEqual(42, result["median_ns"])
        self.assertEqual(0, result["spread_ns"])

    def test_summarize_rejects_empty_samples(self):
        with self.assertRaises(ValueError):
            timing.summarize([])


class SinkDrainageTests(unittest.TestCase):
    """Task 5.1: two shared sinks exist -- a real file on `os.devnull`, and
    a real `os.pipe` drained by a reader thread -- so every subject in a
    given scenario writes to the identical kind of sink (fairness)."""

    def test_devnull_sink_accepts_writes_without_error(self):
        with sinks.devnull_sink() as sink:
            sink.write(b"x" * 1000)
            sink.flush()

    def test_pipe_sink_drains_more_than_the_kernel_pipe_buffer(self):
        # Larger than a typical 64 KiB kernel pipe buffer: if the reader
        # thread were not actually draining, this write would block/hang.
        payload = b"y" * (256 * 1024)
        with sinks.pipe_sink() as sink:
            sink.write(payload)
            sink.flush()


class SubjectAdapterEmitOneLineTests(unittest.TestCase):
    """Every subject adapter emits exactly one parseable JSON line per
    call, carrying the same dynamic call-site content (task 5.1's fairness
    building block; STANDARDS.md CP-005)."""

    def _assert_one_json_line(self, make_subject):
        buffer = io.BytesIO()
        emit, shutdown = make_subject(buffer)
        try:
            emit(user_id="u-1", request_id="r-1", duration_ms=12.5)
        finally:
            shutdown()
        raw = buffer.getvalue().decode("utf-8")
        lines = [line for line in raw.splitlines() if line]
        self.assertEqual(1, len(lines), raw)
        return json.loads(lines[0])

    def test_semlog_subject_emits_one_line(self):
        self._assert_one_json_line(subjects.make_semlog)

    def test_stdlib_baseline_subject_emits_one_line(self):
        self._assert_one_json_line(subjects.make_stdlib_baseline)

    @unittest.skipUnless(_HAS_LOGURU, "loguru not installed in this interpreter")
    def test_loguru_subject_emits_one_line(self):
        self._assert_one_json_line(subjects.make_loguru)

    @unittest.skipUnless(_HAS_STRUCTLOG, "structlog not installed in this interpreter")
    def test_structlog_subject_emits_one_line(self):
        self._assert_one_json_line(subjects.make_structlog)

    def test_two_calls_on_the_same_subject_emit_two_distinct_lines(self):
        # Triangulation: proves the "one line per call" behavior generalizes
        # past a single call, and that call-site content actually reaches
        # the sink (not a hardcoded fixed line).
        buffer = io.BytesIO()
        emit, shutdown = subjects.make_stdlib_baseline(buffer)
        try:
            emit(user_id="u-1", request_id="r-1", duration_ms=1.0)
            emit(user_id="u-2", request_id="r-2", duration_ms=2.0)
        finally:
            shutdown()
        lines = [ln for ln in buffer.getvalue().decode("utf-8").splitlines() if ln]
        self.assertEqual(2, len(lines))
        first, second = (json.loads(ln) for ln in lines)
        self.assertEqual("u-1", first["user_id"])
        self.assertEqual("u-2", second["user_id"])
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
