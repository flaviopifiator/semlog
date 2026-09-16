"""README badge anti-drift checks, for both README editions.

Every badge states a fact that lives somewhere else in the repository, so
each one is checked against that source instead of being trusted:

- CI: the live GitHub Actions status badge of `.github/workflows/ci.yml`,
  linking to the workflow runs (the honest form of "all tests pass"; no
  static "tests passing" badge).
- Python: the CI test matrix, without the version allowed to fail.
- FastAPI and Django: the floor rows of the CI compatibility matrix.
- License and runtime dependencies: `pyproject.toml`.
- Requirements proven: the number of requirement IDs declared in
  STANDARDS.md and cited by a test, counted with the traceability
  checker's own parser (`tests/test_traceability.py`).
- No coverage badge and no percentage anywhere: coverage is not measured
  (`coverage.py` is not an approved tool, CP-008/CP-013).
- PyPI: the live version badge of the published project, linking to its
  page on PyPI, and never left behind inside an HTML comment.

Not tied to a single STANDARDS.md requirement id (same precedent as
`tests/test_class_budget.py`): these checks keep published claims
consistent with the sources that other tests already prove.
"""

from __future__ import annotations

import re
import sys
import unittest
import urllib.parse
from pathlib import Path

from ._readme_support import READMES, badges, commented_badges, readme_text
from .test_traceability import (
    STANDARDS_PATH,
    TESTS_DIR,
    collect_proves,
    parse_standards,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"

CI_BADGE_IMAGE = (
    "https://github.com/flaviopifiator/semlog/actions/workflows/ci.yml/badge.svg"
)
CI_RUNS_URL = "https://github.com/flaviopifiator/semlog/actions/workflows/ci.yml"
PYPI_BADGE_IMAGE = "https://img.shields.io/pypi/v/semlog"
PYPI_PROJECT_URL = "https://pypi.org/project/semlog/"
PYPI_BADGE_LINE = f"[![PyPI]({PYPI_BADGE_IMAGE})]({PYPI_PROJECT_URL})"


def shields_static(image_url):
    """`(label, message)` of a shields.io static badge URL, undoing its
    escaping (`--` is a dash, `__` an underscore, `_` a space), or `None`
    when the URL is not a static shields.io badge."""
    prefix = "https://img.shields.io/badge/"
    if not image_url.startswith(prefix):
        return None
    path = image_url[len(prefix) :].split("?", 1)[0]
    parts = re.split(r"(?<!-)-(?!-)", path)
    if len(parts) != 3:
        return None

    def unescape(part):
        part = part.replace("--", "\0").replace("__", "\1").replace("_", " ")
        part = part.replace("\0", "-").replace("\1", "_")
        return urllib.parse.unquote(part)

    return unescape(parts[0]), unescape(parts[1])


def static_badges(text):
    """`{label: message}` for every static shields.io badge in `text`."""
    result = {}
    for _alt, image, _link in badges(text):
        parsed = shields_static(image)
        if parsed:
            result[parsed[0]] = parsed[1]
    return result


def _version_key(version):
    return tuple(int(part) for part in version.split("."))


def ci_python_range():
    """`"first-last"` of the CI test matrix, without the version allowed to fail."""
    text = CI_PATH.read_text(encoding="utf-8")
    matrix = re.search(r"(?m)^\s*python:\s*\[([^\]]*)\]", text).group(1)
    versions = [v.strip().strip('"') for v in matrix.split(",")]
    allowed_to_fail = set(re.findall(r"matrix\.python == '([^']+)'", text))
    supported = sorted(
        (v for v in versions if v not in allowed_to_fail), key=_version_key
    )
    return f"{supported[0]}-{supported[-1]}"


def ci_floor(framework):
    """The lowest version of `framework` in the CI compatibility matrix."""
    text = CI_PATH.read_text(encoding="utf-8")
    versions = re.findall(rf'(?m)^\s*{framework}:\s*"([^"]+)"', text)
    return min(versions, key=_version_key)


def pyproject_license_and_dependency_count():
    text = PYPROJECT_PATH.read_text(encoding="utf-8")
    if sys.version_info >= (3, 11):
        import tomllib

        project = tomllib.loads(text)["project"]
        return project["license"], len(project.get("dependencies", []))
    license_expr = re.search(r'(?m)^license = "([^"]+)"', text).group(1)
    dependencies = re.search(r"(?m)^dependencies\s*=\s*\[([^\]]*)\]", text).group(1)
    return license_expr, len(re.findall(r'"[^"]+"', dependencies))


def requirements_counts():
    """`(proven, declared)` requirement IDs, as the traceability checker sees them."""
    standards = parse_standards(STANDARDS_PATH.read_text(encoding="utf-8"))
    declared = set(standards.declarations)
    proven = declared & collect_proves(TESTS_DIR).cited
    return len(proven), len(declared)


class ReadmeBadgeFactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.texts = {language: readme_text(language) for language in READMES}

    def test_live_ci_status_badge_links_to_the_workflow_runs(self):
        self.assertTrue(CI_PATH.is_file())
        for language, text in self.texts.items():
            with self.subTest(language=language):
                self.assertIn(
                    (CI_BADGE_IMAGE, CI_RUNS_URL),
                    [(image, link) for _alt, image, link in badges(text)],
                )

    def test_requirements_badge_matches_the_declared_and_proven_count(self):
        proven, declared = requirements_counts()
        self.assertGreater(declared, 0)
        for language, text in self.texts.items():
            with self.subTest(language=language):
                self.assertEqual(
                    f"{proven}/{declared}",
                    static_badges(text).get("requirements proven"),
                )

    def test_python_badge_matches_the_ci_test_matrix(self):
        for language, text in self.texts.items():
            with self.subTest(language=language):
                self.assertEqual(ci_python_range(), static_badges(text).get("python"))

    def test_framework_badges_match_the_ci_matrix_floors(self):
        for language, text in self.texts.items():
            with self.subTest(language=language):
                found = static_badges(text)
                self.assertEqual(f"≥ {ci_floor('django')}", found.get("Django"))
                fastapi = found.get("FastAPI", "")
                self.assertTrue(fastapi.startswith("≥ "), fastapi)
                floor = ci_floor("fastapi")
                self.assertTrue(
                    floor == fastapi[2:] or floor.startswith(fastapi[2:] + "."), fastapi
                )

    def test_license_and_dependency_badges_match_pyproject(self):
        license_expr, dependency_count = pyproject_license_and_dependency_count()
        for language, text in self.texts.items():
            with self.subTest(language=language):
                found = static_badges(text)
                self.assertEqual(license_expr, found.get("license"))
                self.assertEqual(
                    str(dependency_count), found.get("runtime dependencies")
                )

    def test_no_coverage_badge_and_no_percentage_badge(self):
        for language, text in self.texts.items():
            with self.subTest(language=language):
                every_badge = badges(text) + commented_badges(text)
                offenders = [
                    image
                    for alt, image, _link in every_badge
                    if "coverage" in (alt + image).lower()
                    or "%" in urllib.parse.unquote(image)
                ]
                self.assertEqual([], offenders)

    def test_pypi_badge_is_enabled_and_links_to_the_project_page(self):
        for language, text in self.texts.items():
            with self.subTest(language=language):
                self.assertIn(
                    (PYPI_BADGE_IMAGE, PYPI_PROJECT_URL),
                    [(image, link) for _alt, image, link in badges(text)],
                )
                self.assertNotIn(
                    PYPI_BADGE_IMAGE,
                    [image for _a, image, _l in commented_badges(text)],
                )


class ReadmeBadgePerturbationTests(unittest.TestCase):
    """Each badge check fails against a deliberately broken in-memory copy
    of the real README text, never against the files on disk."""

    @classmethod
    def setUpClass(cls):
        cls.text = readme_text("en")

    def test_a_drifted_requirement_count_is_caught(self):
        proven, declared = requirements_counts()
        mutated = self.text.replace(
            f"requirements%20proven-{proven}%2F{declared}",
            f"requirements%20proven-{proven - 1}%2F{declared}",
        )
        self.assertNotEqual(mutated, self.text)
        self.assertNotEqual(
            f"{proven}/{declared}", static_badges(mutated).get("requirements proven")
        )

    def test_a_static_tests_passing_percentage_badge_is_caught(self):
        tests_badge = (
            "[![tests](https://img.shields.io/badge/tests-100%25-green)](README.md)"
        )
        mutated = self.text.replace(
            PYPI_BADGE_LINE, f"{PYPI_BADGE_LINE}\n{tests_badge}", 1
        )
        self.assertNotEqual(mutated, self.text)
        images = [image for _alt, image, _link in badges(mutated)]
        self.assertTrue(any("%" in urllib.parse.unquote(image) for image in images))

    def test_a_commented_out_pypi_badge_is_caught(self):
        mutated = self.text.replace(PYPI_BADGE_LINE, f"<!-- {PYPI_BADGE_LINE} -->", 1)
        self.assertNotEqual(mutated, self.text)
        self.assertNotIn(PYPI_BADGE_IMAGE, [image for _a, image, _l in badges(mutated)])
        self.assertIn(
            PYPI_BADGE_IMAGE, [image for _a, image, _l in commented_badges(mutated)]
        )

    def test_a_commented_out_coverage_badge_is_still_caught(self):
        coverage = "[![coverage](https://img.shields.io/badge/coverage-90%25-green)](x)"
        mutated = self.text.replace(
            PYPI_BADGE_LINE, f"{PYPI_BADGE_LINE}\n<!--\n{coverage}\n-->", 1
        )
        self.assertNotEqual(mutated, self.text)
        images = [image for _alt, image, _link in commented_badges(mutated)]
        self.assertTrue(any("coverage" in image for image in images), images)

    def test_shields_static_parsing_undoes_the_escaping(self):
        self.assertEqual(
            ("runtime dependencies", "3.10-3.14"),
            shields_static(
                "https://img.shields.io/badge/runtime%20dependencies-3.10--3.14-blue?logo=x"
            ),
        )
        self.assertIsNone(shields_static(CI_BADGE_IMAGE))


if __name__ == "__main__":
    unittest.main()
