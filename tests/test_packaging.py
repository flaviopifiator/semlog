"""Packaging metadata tests (task 4.3/4.4; STANDARDS.md LP-001, CP-003,
CP-015).

Checks what `pyproject.toml` itself declares, without building a wheel
(the full-fidelity built-artifact check is a later task; see
`tests/test_wheel_contents.py` for a graceful-skip precursor added in this
same batch): the built distribution's `dependencies` list stays empty
(LP-001, CP-003), the declared build backend is the approved `uv_build`
(CP-014, already proven separately once its own anti-drift tasks land),
and the public-surface fact already proven by
`tests/test_public_surface.py` (CP-015, 9 names) still holds at the
packaging layer -- re-asserted here as a supporting, non-``Proves``-tagged
check, since that module already carries the citation that makes it
count toward traceability. `tests/test_class_budget.py` (CP-017) no
longer publishes a class count to re-assert here: only the no-ABC and
no-class-factory rules remain, already proven in that module alone.

On Python 3.10 there is no stdlib TOML reader (the same constraint
`_identity.py::_read_pyproject` documents for SI-001/SI-005), so this
module falls back to a narrow, regex-based check of the same fact rather
than bundling a third-party TOML parser.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

import semlog

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
PACKAGE_DIR = REPO_ROOT / "src" / "semlog"
PY_TYPED_PATH = PACKAGE_DIR / "py.typed"


def _pyproject_text() -> str:
    return PYPROJECT_PATH.read_text(encoding="utf-8")


def _load_pyproject():
    """Parse `pyproject.toml` with stdlib `tomllib` (3.11+ only); returns
    `None` on Python 3.10, where callers fall back to a text-based check."""
    if sys.version_info < (3, 11):
        return None
    import tomllib

    with PYPROJECT_PATH.open("rb") as handle:
        return tomllib.load(handle)


class ZeroRuntimeDependenciesTests(unittest.TestCase):
    """Proves: CP-003, LP-001"""

    def test_project_dependencies_list_is_empty(self):
        data = _load_pyproject()
        if data is not None:
            self.assertEqual([], data["project"].get("dependencies", []))
            return
        match = re.search(r"(?m)^dependencies\s*=\s*(\[[^\]]*\])", _pyproject_text())
        self.assertIsNotNone(match, "no top-level `dependencies` key found")
        self.assertEqual("[]", re.sub(r"\s+", "", match.group(1)))

    def test_dev_tooling_lives_outside_project_dependencies(self):
        """Dev-only tooling belongs in `[dependency-groups]`; aiohttp alone
        is allowed as an optional runtime integration dependency."""
        text = _pyproject_text()
        self.assertIn("[dependency-groups]", text)
        data = _load_pyproject()
        if data is not None:
            self.assertEqual(
                ["aiohttp>=3.10,<4"],
                data["project"]["optional-dependencies"]["aiohttp"],
            )
            return
        self.assertIn("[project.optional-dependencies]", text)
        self.assertIn('aiohttp = ["aiohttp>=3.10,<4"]', text)


class BuildBackendDeclaredTests(unittest.TestCase):
    """Supporting checks for the `[build-system]` table this batch writes;
    no `Proves` tag here (CP-014 is fully proven only once its own
    anti-drift tasks land -- see `pyproject.toml`'s own comment)."""

    def test_build_backend_is_uv_build_pinned_as_standards_prescribes(self):
        data = _load_pyproject()
        if data is not None:
            build_system = data["build-system"]
            self.assertEqual("uv_build", build_system["build-backend"])
            self.assertEqual(["uv_build>=0.12.13,<0.13"], build_system["requires"])
            return
        text = _pyproject_text()
        self.assertIn('build-backend = "uv_build"', text)
        self.assertIn('"uv_build>=0.12.13,<0.13"', text)


def _classifiers():
    data = _load_pyproject()
    if data is not None:
        return data["project"].get("classifiers", [])
    match = re.search(r"(?m)^classifiers\s*=\s*\[([^\]]*)\]", _pyproject_text())
    return re.findall(r'"([^"]+)"', match.group(1)) if match else []


def _keywords():
    data = _load_pyproject()
    if data is not None:
        return data["project"].get("keywords", [])
    match = re.search(r"(?ms)^keywords\s*=\s*\[(.*?)\]", _pyproject_text())
    return re.findall(r'"([^"]+)"', match.group(1)) if match else []


def _project_urls():
    data = _load_pyproject()
    if data is not None:
        return data["project"].get("urls", {})
    match = re.search(
        r"(?ms)^\[project\.urls\]\n(.*?)(?=^\[|\Z)",
        _pyproject_text(),
    )
    if match is None:
        return {}
    return dict(re.findall(r'(?m)^(\w+)\s*=\s*"([^"]+)"', match.group(1)))


def _ci_supported_pythons():
    """The CI test matrix's Python versions, without the one allowed to fail."""
    ci_text = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    matrix = re.search(r"(?m)^\s*python:\s*\[([^\]]*)\]", ci_text).group(1)
    allowed_to_fail = set(re.findall(r"matrix\.python == '([^']+)'", ci_text))
    versions = [v.strip().strip('"') for v in matrix.split(",")]
    return [v for v in versions if v not in allowed_to_fail]


class ClassifiersTests(unittest.TestCase):
    """PyPI classifiers stay consistent with what the repository verifies:
    the Python versions of the CI test matrix (without the one allowed to
    fail), the two frameworks of the compatibility matrix, and the SPDX
    license expression. Supporting checks, no `Proves` tag: CP-001, CP-002
    and CP-004 are proven by their own modules."""

    def test_python_version_classifiers_match_the_ci_test_matrix(self):
        prefix = "Programming Language :: Python :: "
        declared = [c[len(prefix) :] for c in _classifiers() if c.startswith(prefix)]
        self.assertEqual(_ci_supported_pythons(), declared)
        self.assertEqual(["3.10", "3.11", "3.12", "3.13", "3.14"], declared)

    def test_framework_license_platform_topic_and_status_classifiers(self):
        classifiers = _classifiers()
        for expected in (
            "Framework :: FastAPI",
            "Framework :: Django",
            "License :: OSI Approved :: Apache Software License",
            "Operating System :: OS Independent",
            "Topic :: System :: Logging",
            "Topic :: Software Development :: Libraries :: Python Modules",
            "Development Status :: 3 - Alpha",
            "Intended Audience :: Developers",
        ):
            with self.subTest(classifier=expected):
                self.assertIn(expected, classifiers)
        self.assertIn('license = "Apache-2.0"', _pyproject_text())

    def test_classifiers_are_sorted_and_unique(self):
        classifiers = _classifiers()
        self.assertEqual(sorted(set(classifiers)), classifiers)


class DiscoverabilityMetadataTests(unittest.TestCase):
    """The metadata a reader finds the project by: `keywords`, which is
    the only field PyPI's own search reads besides the name, summary and
    description, and the `Documentation` URL, which PyPI renders as a
    sidebar link. Both are absent from a `pyproject.toml` that is
    otherwise complete, so they are asserted rather than assumed.
    Supporting checks, no `Proves` tag: no STANDARDS.md requirement
    governs either field."""

    def test_keywords_are_declared(self):
        self.assertTrue(_keywords(), "no `keywords` declared for PyPI search")

    def test_keywords_name_the_subject_matter(self):
        declared = {keyword.lower() for keyword in _keywords()}
        for expected in (
            "logging",
            "structured logging",
            "json",
            "opentelemetry",
            "otel",
            "observability",
            "trace context",
        ):
            with self.subTest(keyword=expected):
                self.assertIn(expected, declared)

    def test_keywords_are_lowercase_and_unique(self):
        declared = _keywords()
        self.assertEqual([k.lower() for k in declared], declared)
        self.assertEqual(len(set(declared)), len(declared))

    def test_documentation_url_points_at_the_readme_on_the_repository(self):
        urls = _project_urls()
        documentation = urls.get("Documentation")
        self.assertIsNotNone(documentation, "no `Documentation` project URL")
        self.assertEqual(f"{urls['Repository']}#readme", documentation)

    def test_every_project_url_is_an_https_url(self):
        offenders = [
            f"{name}: {url}"
            for name, url in _project_urls().items()
            if not url.startswith("https://")
        ]
        self.assertEqual([], offenders)


class Pep561TypingMarkerTests(unittest.TestCase):
    """The PEP 561 `py.typed` marker and the `Typing :: Typed` classifier
    that advertises it. Every module in `src/semlog` is annotated inline,
    but a type checker ignores those annotations in an installed package
    unless the marker file is present, so the marker is what makes the
    annotations reach a consumer at all. Marker and classifier are checked
    together, in both directions: the classifier without the marker is a
    claim the package does not honor, and the marker without the
    classifier hides a fact PyPI could show. Supporting checks, no
    `Proves` tag: no STANDARDS.md requirement governs the marker, and
    `tests/test_wheel_contents.py` proves the built wheel actually carries
    it. CP-017 no longer publishes a source-line or class count, so an
    added package-data file reaches no numeric budget at all."""

    def test_marker_sits_inside_the_importable_package(self):
        self.assertTrue((PACKAGE_DIR / "__init__.py").is_file())
        self.assertTrue(PY_TYPED_PATH.is_file(), "no PEP 561 py.typed marker")

    def test_marker_is_the_empty_file_pep_561_prescribes(self):
        self.assertEqual(b"", PY_TYPED_PATH.read_bytes())

    def test_typing_typed_classifier_is_declared(self):
        self.assertIn("Typing :: Typed", _classifiers())

    def test_marker_and_classifier_are_declared_together(self):
        self.assertEqual(
            PY_TYPED_PATH.is_file(),
            "Typing :: Typed" in _classifiers(),
            "the py.typed marker and the Typing :: Typed classifier must "
            "ship together; neither one alone is an honest claim",
        )


class PackagingLayerSurfaceFactsTests(unittest.TestCase):
    """Re-affirms, at the packaging layer, the public-surface fact already
    proven under CP-015 (`tests/test_public_surface.py`); no new `Proves`
    tag needed here. CP-017 no longer publishes a class count to
    re-affirm."""

    def test_public_surface_is_exactly_ten_names(self):
        self.assertEqual(10, len(semlog.__all__))

    def test_requires_python_floor_matches_cp_001(self):
        data = _load_pyproject()
        if data is not None:
            self.assertEqual(">=3.10", data["project"]["requires-python"])
            return
        self.assertIn('requires-python = ">=3.10"', _pyproject_text())


if __name__ == "__main__":
    unittest.main()
