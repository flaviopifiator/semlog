"""Stage-breakdown scenario tests: the per-call cost of one semlog record,
split into three cumulative stages measured on the same call shape --
stdlib dispatch plus `LogRecord` creation with a no-op handler; plus
semlog's `Formatter.format`; plus the real queue handler and enqueue.
Stdlib and semlog only, so none of these tests ever skips.
"""

from __future__ import annotations

import io
import json
import unittest
from unittest import mock

from benchmark import report, stages
from semlog._format import Formatter


def _emit_once(stage):
    buffer = io.BytesIO()
    emit, shutdown = stages.make_stage_emitter(stage, buffer)
    try:
        emit(user_id="u-1", request_id="r-1", duration_ms=1.5)
    finally:
        shutdown()
    return buffer.getvalue()


class StageEmitterTests(unittest.TestCase):
    """Each stage adds exactly one piece of work on top of the previous one."""

    def test_stages_are_cumulative_and_ordered(self):
        self.assertEqual(("dispatch", "format", "enqueue"), stages.STAGES)

    def test_dispatch_stage_never_formats_or_writes(self):
        with mock.patch.object(Formatter, "format", autospec=True) as fmt:
            written = _emit_once("dispatch")
        self.assertEqual(0, fmt.call_count)
        self.assertEqual(b"", written)

    def test_format_stage_calls_the_semlog_formatter_once_and_never_writes(self):
        with mock.patch.object(
            Formatter, "format", autospec=True, return_value="{}"
        ) as fmt:
            written = _emit_once("format")
        self.assertEqual(1, fmt.call_count)
        self.assertEqual(b"", written)

    def test_format_stage_renders_the_same_record_as_the_full_pipeline(self):
        captured = []
        real_format = Formatter.format

        def spy(self, record):
            line = real_format(self, record)
            captured.append(json.loads(line))
            return line

        with mock.patch.object(Formatter, "format", autospec=True, side_effect=spy):
            _emit_once("format")
        written = json.loads(_emit_once("enqueue").decode("utf-8"))
        self.assertEqual(1, len(captured))
        for key in ("timestamp", "otel.scope.name"):
            captured[0].pop(key)
            written.pop(key)
        self.assertEqual(written, captured[0])

    def test_enqueue_stage_writes_one_line_through_the_real_pipeline(self):
        lines = _emit_once("enqueue").decode("utf-8").splitlines()
        self.assertEqual(1, len(lines))
        record = json.loads(lines[0])
        self.assertEqual("bench.request.completed", record["event_name"])
        self.assertEqual("u-1", record["app.user_id"])

    def test_unknown_stage_is_rejected(self):
        with self.assertRaises(ValueError):
            stages.make_stage_emitter("write", io.BytesIO())


class StageBreakdownTests(unittest.TestCase):
    def test_breakdown_returns_one_summary_per_stage(self):
        breakdown = stages.stage_breakdown(n=50, warmup=5)
        self.assertEqual(list(stages.STAGES), list(breakdown))
        for stage, summary in breakdown.items():
            with self.subTest(stage=stage):
                self.assertEqual(50, summary["n"])
                self.assertGreater(summary["median_ns"], 0)


class StageReportTests(unittest.TestCase):
    def test_rows_show_cumulative_median_and_the_delta_each_stage_adds(self):
        breakdown = {
            "dispatch": {"median_ns": 3_000, "p10_ns": 2_500, "p90_ns": 3_500},
            "format": {"median_ns": 14_000, "p10_ns": 13_000, "p90_ns": 15_000},
            "enqueue": {"median_ns": 16_500, "p10_ns": 16_000, "p90_ns": 18_000},
        }
        rows = report.format_stage_rows(breakdown)
        self.assertEqual(
            [
                "| dispatch | 3.00 µs | 1.00 µs | 3.00 µs |",
                "| format | 14.00 µs | 2.00 µs | 11.00 µs |",
                "| enqueue | 16.50 µs | 2.00 µs | 2.50 µs |",
            ],
            rows,
        )


if __name__ == "__main__":
    unittest.main()
