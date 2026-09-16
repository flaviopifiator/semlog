"""Packaging hygiene: SemVer, Keep a Changelog, Conventional Commits, and
the SPDX license declaration (STANDARDS.md CP-004, section 11; spec #176
CP-004).

Proves: CP-004 -- the published version is valid SemVer 2.0.0; a
CHANGELOG.md exists at the repository root, names both Keep a Changelog
and Semantic Versioning, and every version-like heading is either
`[Unreleased]` or a dated `[X.Y.Z] - YYYY-MM-DD` entry; the license is
declared as the SPDX `Apache-2.0` expression; and AGENTS.md documents the
Conventional Commits convention for commit messages.
"""

from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# https://semver.org/spec/v2.0.0.html -- the spec's own suggested regex.
_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-(?:(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_HEADING_RE = re.compile(
    r"^## \[Unreleased\]$|^## \[\d+\.\d+\.\d+[^\]]*\] - \d{4}-\d{2}-\d{2}$"
)


def _load_pyproject_project_table():
    """Return `[project]`'s `version` and `license`, matching
    `tests/test_packaging.py`'s tomllib-with-regex-fallback pattern (no
    stdlib TOML reader on Python 3.10, SI-001)."""
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    if sys.version_info >= (3, 11):
        import tomllib

        data = tomllib.loads(text)
        return data["project"]["version"], data["project"]["license"]
    version_match = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    license_match = re.search(r'^license = "([^"]+)"', text, re.MULTILINE)
    return version_match.group(1), license_match.group(1)


class SemVerAndLicenseTests(unittest.TestCase):
    """Proves: CP-004"""

    def test_version_is_valid_semver_2_0_0(self):
        version, _license = _load_pyproject_project_table()
        self.assertRegex(version, _SEMVER_RE)

    def test_license_is_the_spdx_apache_2_0_expression(self):
        _version, license_expr = _load_pyproject_project_table()
        self.assertEqual("Apache-2.0", license_expr)


class ChangelogTests(unittest.TestCase):
    """Proves: CP-004"""

    @classmethod
    def setUpClass(cls):
        cls.path = REPO_ROOT / "CHANGELOG.md"
        cls.text = cls.path.read_text(encoding="utf-8")

    def test_changelog_exists_at_the_repository_root(self):
        self.assertTrue(self.path.is_file())

    def test_changelog_names_keep_a_changelog_and_semantic_versioning(self):
        self.assertIn("Keep a Changelog", self.text)
        self.assertIn("Semantic Versioning", self.text)

    def test_changelog_starts_with_an_h1_named_changelog(self):
        first_line = self.text.splitlines()[0]
        self.assertEqual("# Changelog", first_line)

    def test_every_version_heading_is_unreleased_or_a_dated_semver_entry(self):
        headings = [line for line in self.text.splitlines() if line.startswith("## [")]
        self.assertGreaterEqual(len(headings), 1, "no version heading found")
        malformed = [line for line in headings if not _HEADING_RE.match(line)]
        self.assertEqual([], malformed)


def _changelog_section(text, heading_prefix):
    """The lines of the first `## `-heading section whose text starts with
    `heading_prefix`, up to (not including) the next `## ` heading."""
    lines = text.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.startswith(heading_prefix)), None
    )
    assert start is not None, f"no heading starting with {heading_prefix!r}"
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break
    return "\n".join(lines[start:end])


def _subsection(section_text, title):
    """The body of the `### {title}` subsection inside `section_text`, up
    to the next `##`/`###` heading, or `None` when the subsection is
    absent."""
    lines = section_text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == title), None)
    if start is None:
        return None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("### ") or lines[i].startswith("## "):
            end = i
            break
    return "\n".join(lines[start + 1 : end]).strip()


class Changelog020Tests(unittest.TestCase):
    """Proves: DOC-013

    CHANGELOG.md's `## [0.2.0]` section: a dated heading, a non-empty
    `### Added` subsection naming the modes and the `semlog=True` keyword,
    and a non-empty `### Fixed` subsection."""

    @classmethod
    def setUpClass(cls):
        cls.text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        cls.section = _changelog_section(cls.text, "## [0.2.0]")

    def test_changelog_has_a_dated_0_2_0_heading(self):
        self.assertRegex(self.text, r"(?m)^## \[0\.2\.0\] - \d{4}-\d{2}-\d{2}$")

    def test_0_2_0_has_a_non_empty_added_subsection_naming_modes_and_keyword(self):
        added = _subsection(self.section, "### Added")
        self.assertIsNotNone(added, "no ### Added subsection")
        self.assertTrue(added)
        self.assertIn("mode", added.lower())
        self.assertIn("semlog=True", added)

    def test_0_2_0_has_a_non_empty_fixed_subsection(self):
        fixed = _subsection(self.section, "### Fixed")
        self.assertIsNotNone(fixed, "no ### Fixed subsection")
        self.assertTrue(fixed)


class SemlogImportsWithoutDjangoTests(unittest.TestCase):
    """Proves: CP-008, HTM-008

    Runs in a child interpreter with `django` and `asgiref` blocked by a
    `sys.meta_path` finder installed before `semlog` is ever imported:
    `import semlog` must still succeed, `semlog.DjangoMiddleware` must
    resolve and construct with no Django installed at all, and `django`,
    `asgiref` and `asyncio` must all stay absent from `sys.modules`
    afterward -- proving HTM-008's "MUST NOT import django or asgiref at
    semlog import time" clause end to end, including the lazy
    coroutine-detection shim inside `DjangoMiddleware.__init__`.
    Lives here, not in `tests/test_django_matrix.py`: it
    must run in the **default** suite, where Django is genuinely absent,
    not inside the `django-matrix` CI job where Django is installed."""

    def test_semlog_imports_and_constructs_django_middleware_without_django(self):
        src_dir = str(REPO_ROOT / "src")
        script = f"""
import sys


class _BlockFinder:
    def find_spec(self, name, path, target=None):
        blocked = name == "django" or name.startswith("django.")
        blocked = blocked or name == "asgiref" or name.startswith("asgiref.")
        if blocked:
            raise ImportError(f"blocked for this test: {{name}}")
        return None


sys.meta_path.insert(0, _BlockFinder())
sys.path.insert(0, {src_dir!r})

import semlog

middleware = semlog.DjangoMiddleware(lambda request: request)
assert middleware is not None

for blocked_name in ("django", "asgiref", "asyncio"):
    assert blocked_name not in sys.modules, f"{{blocked_name}} was imported"

print("OK")
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("OK", result.stdout)


class ConventionalCommitsDocumentedTests(unittest.TestCase):
    """Proves: CP-004"""

    def test_agents_md_documents_conventional_commits(self):
        text = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("Conventional Commits", text)


if __name__ == "__main__":
    unittest.main()
