"""Report-formatting tests (task 5.4/5.5): turning a measured summary into
the exact Markdown row shape the harness prints. Pure formatting logic, no
timing/measurement of its own, over synthetic values: no figure here is a
measured result, and none is published (STANDARDS.md CP-005)."""

from __future__ import annotations

import unittest

from benchmark import report


class FormatNsTests(unittest.TestCase):
    def test_formats_whole_microseconds(self):
        self.assertEqual("10.00 µs", report.format_ns(10_000))

    def test_formats_fractional_microseconds(self):
        self.assertEqual("1.50 µs", report.format_ns(1_500))


class FormatThroughputRowTests(unittest.TestCase):
    def test_row_includes_scenario_subject_median_spread_and_memory(self):
        summary = {"median_ns": 5_000, "p10_ns": 4_000, "p90_ns": 6_000, "n": 100}
        row = report.format_throughput_row(
            "steady-state", "semlog", summary, memory_bytes=512
        )
        self.assertEqual("| steady-state | semlog | 5.00 µs | 2.00 µs | 512 B |", row)

    def test_row_uses_an_n_a_placeholder_without_memory(self):
        summary = {"median_ns": 5_000, "p10_ns": 4_000, "p90_ns": 6_000, "n": 100}
        row = report.format_throughput_row("steady-state", "loguru", summary)
        self.assertTrue(row.endswith("| n/a |"))


class FormatRangeRowTests(unittest.TestCase):
    """Multi-run aggregates publish the median of per-run medians plus the
    min-max range across runs, never a single run's figure."""

    def test_range_row_shows_median_range_and_memory(self):
        aggregated = {"median_ns": 11_000, "min_ns": 10_000, "max_ns": 12_500}
        row = report.format_range_row("steady-state", "semlog", aggregated, 3.0)
        self.assertEqual(
            "| steady-state | semlog | 11.00 µs | 10.00-12.50 µs | 3 B |", row
        )

    def test_stage_rows_use_the_range_for_aggregated_summaries(self):
        breakdown = {
            "dispatch": {"median_ns": 3_000, "min_ns": 2_900, "max_ns": 3_100},
            "format": {"median_ns": 13_000, "min_ns": 12_000, "max_ns": 14_000},
        }
        self.assertEqual(
            [
                "| dispatch | 3.00 µs | 2.90-3.10 µs | 3.00 µs |",
                "| format | 13.00 µs | 12.00-14.00 µs | 10.00 µs |",
            ],
            report.format_stage_rows(breakdown),
        )


if __name__ == "__main__":
    unittest.main()
