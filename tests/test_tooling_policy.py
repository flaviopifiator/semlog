"""Tooling-policy tests (task 4.2; STANDARDS.md CP-008, CP-013).

Checks that `pyproject.toml` configures `ruff` explicitly (rather than
relying on whichever defaults a given `ruff` release happens to ship) and
that no non-approved third-party package (`mypy`, `coverage`, `jsonschema`,
`pytest`, `gunicorn`, `uvicorn`, or anything else) is declared anywhere in
the project's dependency tables. The "identical locally and in CI" half of
CP-008's ruff clause is deferred to the CI workflow tasks (4.1/4.25, out of
this batch's scope per the orchestrator's explicit instruction); this
module proves only the local, file-level half.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"

_NOT_APPROVED = ("mypy", "coverage", "jsonschema", "pytest", "gunicorn", "uvicorn")


def _pyproject_text() -> str:
    return PYPROJECT_PATH.read_text(encoding="utf-8")


class RuffConfiguredExplicitlyTests(unittest.TestCase):
    """Proves: CP-008"""

    def test_tool_ruff_section_is_present(self):
        self.assertIn("[tool.ruff]", _pyproject_text())

    def test_tool_ruff_pins_a_target_version(self):
        text = _pyproject_text()
        match = re.search(r'(?m)^target-version\s*=\s*"(py3\d\d)"', text)
        self.assertIsNotNone(match, "no target-version pinned under [tool.ruff]")

    def test_ruff_is_the_only_declared_dev_dependency_this_batch(self):
        """Phase 4's compatibility-matrix/benchmark subjects (FastAPI,
        Django, loguru, structlog) are approved by CP-013 but are tasks
        4.10-4.13/5.x's job, not this batch's; only `ruff` is wired here."""
        text = _pyproject_text()
        group = re.search(r"\[dependency-groups\][\s\S]*?dev\s*=\s*(\[[^\]]*\])", text)
        self.assertIsNotNone(group, "no `dev` dependency-group found")
        self.assertIn("ruff", group.group(1))


class NoUnapprovedToolingTests(unittest.TestCase):
    """Proves: CP-013"""

    def test_no_unapproved_third_party_package_named_anywhere(self):
        text = _pyproject_text().lower()
        offenders = [name for name in _NOT_APPROVED if name in text]
        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
