"""Environment-disclosure tests (task 5.4; STANDARDS.md CP-005: hardware,
OS, Python version and library versions must accompany any measured
number). Proves the disclosure dict carries real, live values -- not a
hardcoded placeholder -- by cross-checking each field against the
interpreter that is actually running the test.
"""

from __future__ import annotations

import importlib.util
import os
import platform
import sys
import unittest

from benchmark import environment


def _has(name):
    return importlib.util.find_spec(name) is not None


class EnvironmentDisclosureTests(unittest.TestCase):
    def test_disclosure_reports_the_running_interpreter(self):
        info = environment.disclose()
        self.assertEqual(sys.version.split()[0], info["python_version"])
        self.assertEqual(
            platform.python_implementation(), info["python_implementation"]
        )
        self.assertIsInstance(info["cpu_count"], int)
        self.assertGreater(info["cpu_count"], 0)
        self.assertIn(platform.system(), info["os"])

    @unittest.skipUnless(hasattr(os, "sched_getaffinity"), "no CPU affinity API here")
    def test_disclosure_reports_the_cpu_affinity_the_run_is_pinned_to(self):
        info = environment.disclose()
        self.assertEqual(sorted(os.sched_getaffinity(0)), info["cpu_affinity"])

    def test_disclosure_reports_a_positive_timer_resolution(self):
        info = environment.disclose()
        self.assertGreater(info["perf_counter_resolution_s"], 0.0)

    @unittest.skipIf(_has("loguru"), "loguru is installed in this interpreter")
    def test_disclosure_reports_not_installed_for_an_absent_library(self):
        # loguru is not installed in the default dev environment (an
        # approved benchmark subject, never a runtime dependency); this
        # is the real, honest signal, not a guess.
        info = environment.disclose()
        self.assertEqual("not installed", info["loguru_version"])

    @unittest.skipUnless(_has("loguru"), "loguru not installed in this interpreter")
    def test_disclosure_reports_a_real_version_for_an_installed_library(self):
        # Triangulation: when the library IS installed, the disclosed
        # version is a real PEP 440 version string, not "not installed".
        info = environment.disclose()
        self.assertNotEqual("not installed", info["loguru_version"])
        self.assertRegex(info["loguru_version"], r"^\d+(\.\d+)*")


if __name__ == "__main__":
    unittest.main()
