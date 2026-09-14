"""Runner tests: command-line options, the formatter profile mode, and the
multi-run aggregation that turns several fresh-process runs into the
median and min-max range the harness reports. Stdlib and semlog only."""

from __future__ import annotations

import contextlib
import io
import unittest

from benchmark import profiling, run_benchmark


class ParseArgsTests(unittest.TestCase):
    def test_defaults_run_one_report(self):
        args = run_benchmark.parse_args([])
        self.assertIsNone(args.profile)
        self.assertEqual(1, args.runs)
        self.assertFalse(args.json)

    def test_profile_takes_the_number_of_entries(self):
        self.assertEqual(7, run_benchmark.parse_args(["--profile", "7"]).profile)

    def test_runs_and_json_are_parsed(self):
        args = run_benchmark.parse_args(["--runs", "5", "--json"])
        self.assertEqual(5, args.runs)
        self.assertTrue(args.json)

    def test_cpus_takes_a_comma_separated_affinity_set(self):
        self.assertIsNone(run_benchmark.parse_args([]).cpus)
        self.assertEqual({0, 2}, run_benchmark.parse_args(["--cpus", "0,2"]).cpus)

    def test_cpus_rejects_a_malformed_list(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            run_benchmark.parse_args(["--cpus", "0,x"])

    def test_runs_must_be_positive(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            run_benchmark.parse_args(["--runs", "0"])


class ProfileFormatterTests(unittest.TestCase):
    def test_profile_lists_at_most_top_n_entries_from_the_formatter(self):
        text = profiling.profile_formatter(calls=200, top=5, sort="cumulative")
        self.assertIn("_format.py", text)
        self.assertIn("(format)", text)
        entries = [line for line in text.splitlines() if ".py:" in line]
        self.assertGreaterEqual(len(entries), 1)
        self.assertLessEqual(len(entries), 5)

    def test_profile_rejects_a_non_positive_entry_count(self):
        with self.assertRaises(ValueError):
            profiling.profile_formatter(calls=10, top=0)


class AggregateRunsTests(unittest.TestCase):
    def test_median_and_range_across_runs_per_view_and_subject(self):
        runs = [
            {
                "steady_state": {"semlog": {"median_ns": 10_000}},
                "like_for_like": {"semlog": {"median_ns": 11_000}},
                "stages": {"format": {"median_ns": 9_000}},
                "memory": {"semlog": 4.0},
                "contention": {"concurrent_ns": 30_000_000},
                "field_scaling": {"3": {"semlog": {"median_ns": 1}}},
            },
            {
                "steady_state": {"semlog": {"median_ns": 12_000}},
                "like_for_like": {"semlog": {"median_ns": 13_000}},
                "stages": {"format": {"median_ns": 8_000}},
                "memory": {"semlog": 2.0},
                "contention": {"concurrent_ns": 20_000_000},
                "field_scaling": {"3": {"semlog": {"median_ns": 1}}},
            },
            {
                "steady_state": {"semlog": {"median_ns": 11_000}},
                "like_for_like": {"semlog": {"median_ns": 12_500}},
                "stages": {"format": {"median_ns": 10_000}},
                "memory": {"semlog": 3.0},
                "contention": {"concurrent_ns": 25_000_000},
                "field_scaling": {"3": {"semlog": {"median_ns": 1}}},
            },
        ]
        result = run_benchmark.aggregate_runs(runs)
        self.assertEqual(
            {"median_ns": 11_000, "min_ns": 10_000, "max_ns": 12_000},
            result["steady_state"]["semlog"],
        )
        self.assertEqual(
            {"median_ns": 12_500, "min_ns": 11_000, "max_ns": 13_000},
            result["like_for_like"]["semlog"],
        )
        self.assertEqual(
            {"median_ns": 9_000, "min_ns": 8_000, "max_ns": 10_000},
            result["stages"]["format"],
        )
        self.assertEqual(3.0, result["memory"]["semlog"])
        self.assertEqual(25_000_000, result["contention_ns"])
        self.assertEqual(3, result["runs"])


if __name__ == "__main__":
    unittest.main()
