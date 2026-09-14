"""`available_subjects()` tests (task 5.4): the full-run orchestrator must
skip a comparison subject whose third-party package is not installed,
rather than crashing the whole run or silently pretending it ran."""

from __future__ import annotations

import importlib.util
import unittest

from benchmark import run_benchmark


class AvailableSubjectsTests(unittest.TestCase):
    def test_semlog_and_stdlib_baseline_are_always_available(self):
        available = run_benchmark.available_subjects()
        self.assertIn("semlog", available)
        self.assertIn("stdlib_baseline", available)

    def test_loguru_presence_matches_the_interpreter(self):
        available = run_benchmark.available_subjects()
        has_loguru = importlib.util.find_spec("loguru") is not None
        self.assertEqual(has_loguru, "loguru" in available)

    def test_structlog_presence_matches_the_interpreter(self):
        available = run_benchmark.available_subjects()
        has_structlog = importlib.util.find_spec("structlog") is not None
        self.assertEqual(has_structlog, "structlog" in available)


if __name__ == "__main__":
    unittest.main()
