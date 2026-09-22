"""Built-wheel content regression guard (precursor to task 4.23's full A7
checklist; STANDARDS.md CP-003, DOC-011).

Design #170 §17.5's own execution note for A7: "locally it runs only when
a built wheel path is supplied and is otherwise skipped with an explicit
reason" -- this module follows that exactly, reading the wheel path from
the `SEMLOG_WHEEL_PATH` environment variable rather than invoking
`uv build` itself, so `python -m unittest discover` never depends on `uv`
or network access to stay green (per the orchestrator's explicit
instruction: a test that builds a wheel must skip gracefully, and say so,
rather than fail the suite for an unrelated reason).

Build a wheel and point this module at it with, for example::

    uv build
    SEMLOG_WHEEL_PATH=dist/semlog-0.1.0-py3-none-any.whl python -m unittest \\
        tests.test_wheel_contents -v

Task 4.23 is the full checklist (zip-import verification, the
`uv.lock`/`.github/` denylist entries that depend on later Phase 4 tasks,
CI wiring via task 4.24); this module proves only the subset already
meaningful with this batch's `pyproject.toml`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

_DENYLIST_SUFFIXES = (
    "AGENTS.md",
    "BENCHMARKS.md",
    "llms.txt",
    "README.es.md",
    "README.md",
    "RELEASING.md",
    "STANDARDS.md",
    "SECURITY.md",
    "uv.lock",
)
_DENYLIST_DIR_PREFIXES = ("schemas/", "tests/", ".github/", "tools/")


def _resolve_wheel_path() -> Path:
    """Shared graceful-skip gate (design #170 §17.5's own execution note
    for A7): read `SEMLOG_WHEEL_PATH`, skip with an explicit reason rather
    than failing the suite when no wheel is available."""
    raw_path = os.environ.get("SEMLOG_WHEEL_PATH")
    if not raw_path:
        raise unittest.SkipTest(
            "SEMLOG_WHEEL_PATH not set; build a wheel with `uv build` and "
            "point this module at it (see module docstring) -- skipped "
            "gracefully, not a suite failure"
        )
    wheel_path = Path(raw_path).resolve()
    if not wheel_path.is_file():
        raise unittest.SkipTest(
            f"SEMLOG_WHEEL_PATH={raw_path!r} does not exist; skipped gracefully"
        )
    return wheel_path


class WheelContentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        wheel_path = _resolve_wheel_path()
        with zipfile.ZipFile(wheel_path) as zf:
            cls.names = zf.namelist()
            metadata_entry = next(
                n for n in cls.names if n.endswith(".dist-info/METADATA")
            )
            cls.metadata_text = zf.read(metadata_entry).decode("utf-8")

    def test_agent_guide_ships_exactly_once(self):
        matches = [n for n in self.names if n.endswith("agent_guide.md")]
        self.assertEqual(["semlog/agent_guide.md"], matches)

    def test_py_typed_marker_ships_inside_the_package(self):
        """PEP 561: a type checker ignores an installed package's inline
        annotations unless this marker travels with it, so building the
        wheel is the only place the claim can actually be checked. It ships
        exactly once, inside `semlog/`, never at the archive root."""
        matches = [n for n in self.names if n.rsplit("/", 1)[-1] == "py.typed"]
        self.assertEqual(["semlog/py.typed"], matches)

    def test_package_python_source_files_are_present(self):
        py_files = [n for n in self.names if n.endswith(".py")]
        self.assertIn("semlog/__init__.py", py_files)
        self.assertIn("semlog/__main__.py", py_files)
        self.assertGreaterEqual(len(py_files), 10)

    def test_denylisted_repository_only_files_are_absent(self):
        offenders = [
            n
            for n in self.names
            if any(n.endswith(suffix) for suffix in _DENYLIST_SUFFIXES)
            or any(n.startswith(prefix) for prefix in _DENYLIST_DIR_PREFIXES)
        ]
        self.assertEqual([], offenders)

    def test_license_ships_only_under_dist_info_licenses(self):
        license_entries = [n for n in self.names if n.rsplit("/", 1)[-1] == "LICENSE"]
        self.assertEqual(1, len(license_entries))
        self.assertIn(".dist-info/licenses/", license_entries[0])

    def test_requires_dist_contains_only_the_optional_aiohttp_extra(self):
        """Checks the METADATA *header* only (up to the first blank line):
        the long description is the README's own prose, which legitimately
        mentions the string "Requires-Dist" while explaining the
        dependency policy -- a naive whole-text substring check would be a
        false positive against that sentence. The base installation has no
        dependencies; aiohttp is permitted only behind its package extra."""
        header = self.metadata_text.split("\n\n", 1)[0]
        requires_dist = [
            line for line in header.splitlines() if line.startswith("Requires-Dist:")
        ]
        self.assertEqual(
            ["Requires-Dist: aiohttp>=3.10,<4 ; extra == 'aiohttp'"],
            requires_dist,
        )


class InstalledWheelEndToEndTests(unittest.TestCase):
    """Proves: DOC-011, CP-003, CP-018, OSF-001

    Full A7 (task 4.23), plus the "installed package" half of A2/A3 (tasks
    4.21/4.22): install the built wheel into a fresh venv OUTSIDE the
    repository and confirm `llm()`/the CLI report the REAL installed
    version (not the "unknown" placeholder this ambient dev checkout would
    otherwise report -- `tests/test_llm.py`'s `LlmFunctionTests` proves the
    same wiring by mocking `importlib.metadata.version` instead, since this
    checkout has no installed distribution); separately, with the wheel
    file itself placed on `sys.path` (zip import, no install), confirm
    `llm()` still works. Skips gracefully -- same `SEMLOG_WHEEL_PATH` gate
    as `WheelContentTests` -- plus a second graceful skip when the `uv`
    CLI (the approved dev-time venv/install frontend, STANDARDS.md CP-008)
    is not on `PATH`, since building a venv with no working `ensurepip` in
    this sandbox is otherwise not possible with the stdlib alone."""

    @classmethod
    def setUpClass(cls):
        cls.wheel_path = _resolve_wheel_path()
        if shutil.which("uv") is None:
            raise unittest.SkipTest(
                "uv CLI not found on PATH; skipped gracefully (see module docstring)"
            )

    def test_fresh_venv_install_reports_the_real_installed_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            venv_dir = Path(tmp) / "venv"
            subprocess.run(
                ["uv", "venv", "--python", sys.executable, str(venv_dir)],
                check=True,
                capture_output=True,
            )
            venv_python = venv_dir / "bin" / "python"
            subprocess.run(
                [
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    str(venv_python),
                    "--no-deps",
                    str(self.wheel_path),
                ],
                check=True,
                capture_output=True,
            )
            outside_repo_cwd = tempfile.gettempdir()
            result = subprocess.run(
                [str(venv_python), "-m", "semlog", "llm"],
                cwd=outside_repo_cwd,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, result.returncode)
            self.assertEqual(b"", result.stderr)
            stdout_text = result.stdout.decode("utf-8")
            version_line = next(
                line
                for line in stdout_text.splitlines()
                if line.startswith("semlog_version:")
            )
            reported_version = version_line.split(":", 1)[1].strip()
            self.assertNotEqual("unknown", reported_version)

            metadata_check = subprocess.run(
                [
                    str(venv_python),
                    "-c",
                    "import importlib.metadata as m; print(m.version('semlog'))",
                ],
                capture_output=True,
                check=True,
                text=True,
            )
            self.assertEqual(metadata_check.stdout.strip(), reported_version)

    def test_zip_import_with_wheel_on_sys_path_still_returns_the_guide(self):
        code = (
            "import sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "import semlog\n"
            "text = semlog.llm()\n"
            "assert text.startswith('---\\nsemlog_version: '), text[:80]\n"
            "print('OK')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code, str(self.wheel_path)],
            capture_output=True,
            check=False,
            text=True,
            cwd=tempfile.gettempdir(),
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("OK\n", result.stdout)


if __name__ == "__main__":
    unittest.main()


class WheelPathResolutionTests(unittest.TestCase):
    """CI passes `SEMLOG_WHEEL_PATH` as a path relative to the checkout, and
    the zip-import test runs a subprocess from another working directory, so
    the gate must hand back an absolute path."""

    def test_relative_wheel_path_is_resolved_to_an_absolute_path(self):
        import tempfile

        previous_cwd = os.getcwd()
        previous_env = os.environ.get("SEMLOG_WHEEL_PATH")
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "fake.whl").write_bytes(b"")
            try:
                os.chdir(tmp)
                os.environ["SEMLOG_WHEEL_PATH"] = "fake.whl"
                resolved = _resolve_wheel_path()
            finally:
                os.chdir(previous_cwd)
                if previous_env is None:
                    os.environ.pop("SEMLOG_WHEEL_PATH", None)
                else:
                    os.environ["SEMLOG_WHEEL_PATH"] = previous_env
        self.assertTrue(resolved.is_absolute(), resolved)
        self.assertEqual("fake.whl", resolved.name)
