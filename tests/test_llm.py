"""`llm()` and `python -m semlog llm` tests (design-decisions #170 §17;
DOC-011, CP-018), written before either exists (strict TDD).
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from unittest import mock

import semlog


class LlmFunctionTests(unittest.TestCase):
    """Proves: DOC-011, CP-018

    A2 (design-decisions #170 §17.5): `llm()`'s output starts with the
    version header; `semlog_version` equals the same
    `importlib.metadata.version("semlog")` lookup the library itself makes
    (proven below by mocking that exact call site, since this ambient dev
    checkout has no installed `semlog` distribution -- `tests/
    test_wheel_contents.py`'s `InstalledWheelEndToEndTests` additionally
    proves this against a REAL installed distribution whenever a wheel can
    be built, per design's own "A2/A3 need the installed package" note);
    `otel_semconv_version` equals the pinned constant AND the version
    STANDARDS.md itself states; the stored guide contains neither version
    literal."""

    def test_returns_a_str_starting_with_the_version_header(self):
        text = semlog.llm()
        self.assertIsInstance(text, str)
        self.assertTrue(text.startswith("---\nsemlog_version: "))
        self.assertIn("otel_semconv_version: ", text)

    def test_no_version_literal_leaks_from_the_stored_guide_body(self):
        text = semlog.llm()
        body = text.split("---\n\n", 1)[1]
        self.assertNotIn("semlog_version:", body)

    def test_semlog_version_header_matches_importlib_metadata_lookup(self):
        """A2: mock the exact call site `llm()` depends on
        (`importlib.metadata.version`, via `semlog._identity._dist_version`)
        and confirm the header echoes exactly that value -- this fails if
        the header were ever hardcoded instead of sourced from metadata."""
        with mock.patch(
            "semlog._identity.metadata.version", return_value="9.9.9-fixture"
        ):
            text = semlog.llm()
        self.assertTrue(text.startswith("---\nsemlog_version: 9.9.9-fixture\n"))

    def test_otel_semconv_version_matches_the_pinned_standards_md_version(self):
        """A2: the `otel_semconv_version` header field, the package's own
        pinned constant, and the version STANDARDS.md states as "pinned"
        must all agree -- a real cross-document consistency check, not
        just an internal self-consistency one."""
        import re
        from pathlib import Path

        text = semlog.llm()
        header_match = re.search(r"otel_semconv_version: (\S+)", text)
        self.assertIsNotNone(header_match)
        standards_path = Path(__file__).resolve().parents[1] / "STANDARDS.md"
        standards_text = standards_path.read_text(encoding="utf-8")
        standards_match = re.search(r"Pinned version: v(\S+?)\.\*\*", standards_text)
        self.assertIsNotNone(
            standards_match, "no pinned semconv version found in STANDARDS.md"
        )
        self.assertEqual(header_match.group(1), standards_match.group(1))

    def test_two_calls_each_re_read_the_resource_no_cache(self):
        import importlib.resources

        real_files = importlib.resources.files
        calls = []

        def counting_files(package):
            calls.append(package)
            return real_files(package)

        with mock.patch("importlib.resources.files", side_effect=counting_files):
            semlog.llm()
            semlog.llm()
        self.assertEqual(2, len(calls))

    def test_broken_install_propagates_the_resource_error_unchanged(self):
        with (
            mock.patch(
                "importlib.resources.files", side_effect=FileNotFoundError("gone")
            ),
            self.assertRaises(FileNotFoundError),
        ):
            semlog.llm()


class CliModuleTests(unittest.TestCase):
    """No `Proves:` tag yet -- see `LlmFunctionTests`."""

    def test_no_argparse_import(self):
        import ast
        from pathlib import Path

        import semlog.__main__ as cli_module

        source = Path(cli_module.__file__).read_text(encoding="utf-8")
        names = {
            alias.name.split(".")[0]
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        self.assertNotIn("argparse", names)

    def test_main_with_llm_returns_zero(self):
        from semlog.__main__ import main

        self.assertEqual(0, main(["llm"]))

    def test_main_with_anything_else_returns_two(self):
        from semlog.__main__ import main

        self.assertEqual(2, main([]))
        self.assertEqual(2, main(["bogus"]))
        self.assertEqual(2, main(["llm", "extra"]))


class CliSubprocessTests(unittest.TestCase):
    """Proves: CP-018

    A3 (design-decisions #170 §17.5): `python -m semlog llm` writes bytes
    identical to `llm()` and exits 0; any other argv writes usage to
    stderr, nothing to stdout, and exits 2. This already runs the real
    subprocess entry point (not a call to `main()` in-process), which is
    exactly A3's acceptance shape; `tests/test_wheel_contents.py`'s
    `InstalledWheelEndToEndTests` additionally proves the same contract
    against a distribution installed from a real built wheel, whenever a
    wheel can be built (this batch's A2/A3 "installed package" half)."""

    def test_python_dash_m_semlog_llm_writes_identical_bytes_and_exits_zero(self):
        result = subprocess.run(
            [sys.executable, "-m", "semlog", "llm"],
            cwd="src",
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, result.returncode)
        self.assertEqual(semlog.llm().encode("utf-8"), result.stdout)
        self.assertEqual(b"", result.stderr)

    def test_unknown_subcommand_prints_usage_and_exits_two(self):
        result = subprocess.run(
            [sys.executable, "-m", "semlog", "bogus"],
            cwd="src",
            capture_output=True,
            check=False,
        )
        self.assertEqual(2, result.returncode)
        self.assertEqual(b"", result.stdout)
        self.assertIn(b"usage: python -m semlog llm", result.stderr)

    def test_importing_semlog_has_zero_side_effects(self):
        result = subprocess.run(
            [sys.executable, "-c", "import semlog"],
            cwd="src",
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, result.returncode)
        self.assertEqual(b"", result.stdout)
        self.assertEqual(b"", result.stderr)


if __name__ == "__main__":
    unittest.main()
