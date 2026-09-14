"""Tests for the release policy tool (`tools/release_policy.py`;
STANDARDS.md PRV-005, PRV-006, CP-008; RELEASING.md).

The tool decides the required version bump from Conventional Commits,
checks a pull request's version and CHANGELOG.md against its commits, and
describes the annotated tag for the version on `main`. Reading
`pyproject.toml` needs `tomllib` (Python 3.11+); the workflows run a pinned
Python 3.13, so tests that read TOML skip on 3.10 with that reason, and a
3.10-only test proves the tool fails clearly there instead.
"""

from __future__ import annotations

import ast
import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import release_policy as rp

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "release_policy.py"

NEEDS_TOMLLIB = unittest.skipIf(
    sys.version_info < (3, 11),
    "tomllib needs Python 3.11+; the release workflows run Python 3.13",
)

CHANGELOG = """\
# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Added

- Something not released yet.

## [0.2.0] - 2026-09-14

### Added

- A new feature.

### Fixed

- A bug.

## [0.1.0] - 2026-01-02

### Added

- The first release.

[unreleased]: https://example.com/compare/v0.2.0...HEAD
[0.2.0]: https://example.com/compare/v0.1.0...v0.2.0
[0.1.0]: https://example.com/releases/tag/v0.1.0
"""


def pyproject(version: str, name: str = "semlog") -> str:
    return f'[project]\nname = "{name}"\nversion = "{version}"\n'


class ParseVersionTests(unittest.TestCase):
    """Proves: PRV-005"""

    def test_parses_a_plain_major_minor_patch_version(self):
        self.assertEqual((1, 20, 3), rp.parse_version("1.20.3"))
        self.assertEqual((0, 0, 0), rp.parse_version("0.0.0"))

    def test_rejects_malformed_versions_with_a_clear_error(self):
        for bad in (
            "",
            "1",
            "1.2",
            "1.2.3.4",
            "01.2.3",
            "1.02.3",
            "1.2.03",
            "v1.2.3",
            " 1.2.3",
            "1.2.3 ",
            "1.2.x",
            "1.2.3-rc.1",
            "1.2.3+build",
        ):
            with self.subTest(version=bad):
                with self.assertRaises(rp.ReleasePolicyError) as caught:
                    rp.parse_version(bad)
                self.assertIn(repr(bad), str(caught.exception))
                self.assertIn("MAJOR.MINOR.PATCH", str(caught.exception))


class NextVersionTests(unittest.TestCase):
    """Proves: PRV-005"""

    def test_major_resets_minor_and_patch(self):
        self.assertEqual("2.0.0", rp.next_version("1.4.7", "major"))

    def test_minor_resets_patch(self):
        self.assertEqual("1.5.0", rp.next_version("1.4.7", "minor"))

    def test_patch_increments_patch(self):
        self.assertEqual("1.4.8", rp.next_version("1.4.7", "patch"))

    def test_rejects_an_unknown_bump_level(self):
        with self.assertRaises(rp.ReleasePolicyError) as caught:
            rp.next_version("1.4.7", "micro")
        self.assertIn("'micro'", str(caught.exception))

    def test_rejects_a_malformed_current_version(self):
        with self.assertRaises(rp.ReleasePolicyError) as caught:
            rp.next_version("1.4", "patch")
        self.assertIn("'1.4'", str(caught.exception))


class RequiredBumpTests(unittest.TestCase):
    """Proves: PRV-005"""

    def test_feat_requires_a_minor_release(self):
        self.assertEqual("minor", rp.required_bump(["feat: add a thing"], "1.0.0"))

    def test_fix_and_perf_require_a_patch_release(self):
        for message in ("fix: repair a thing", "perf: speed up a thing"):
            with self.subTest(message=message):
                self.assertEqual("patch", rp.required_bump([message], "1.0.0"))

    def test_housekeeping_types_require_no_release(self):
        for commit_type in (
            "docs",
            "test",
            "ci",
            "chore",
            "refactor",
            "build",
            "style",
        ):
            with self.subTest(commit_type=commit_type):
                message = f"{commit_type}: tidy up"
                self.assertIsNone(rp.required_bump([message], "1.0.0"))

    def test_no_commits_require_no_release(self):
        self.assertIsNone(rp.required_bump([], "1.0.0"))

    def test_a_scope_is_accepted(self):
        self.assertEqual("minor", rp.required_bump(["feat(format): add"], "1.0.0"))

    def test_the_type_is_case_insensitive(self):
        self.assertEqual("patch", rp.required_bump(["Fix: repair"], "1.0.0"))

    def test_bang_after_the_type_or_scope_requires_a_major_release(self):
        for message in ("feat!: drop it", "fix(api)!: change it"):
            with self.subTest(message=message):
                self.assertEqual("major", rp.required_bump([message], "1.2.3"))

    def test_bang_on_a_housekeeping_type_is_still_breaking(self):
        self.assertEqual("major", rp.required_bump(["refactor!: rename"], "1.2.3"))

    def test_breaking_change_footer_requires_a_major_release(self):
        for footer in ("BREAKING CHANGE: gone", "BREAKING-CHANGE: gone"):
            with self.subTest(footer=footer):
                message = f"feat: rework\n\nLonger body.\n\n{footer}\n"
                self.assertEqual("major", rp.required_bump([message], "1.2.3"))

    def test_breaking_change_token_must_be_uppercase(self):
        message = "fix: repair\n\nbreaking change: not a footer token\n"
        self.assertEqual("patch", rp.required_bump([message], "1.2.3"))

    def test_breaking_change_bumps_minor_while_the_major_version_is_zero(self):
        self.assertEqual("minor", rp.required_bump(["feat!: drop it"], "0.4.1"))
        footer_message = "fix: repair\n\nBREAKING CHANGE: gone\n"
        self.assertEqual("minor", rp.required_bump([footer_message], "0.4.1"))

    def test_the_highest_level_wins(self):
        messages = ["docs: explain", "fix: repair", "feat: add", "test: cover"]
        self.assertEqual("minor", rp.required_bump(messages, "1.0.0"))

    def test_merge_commits_are_ignored(self):
        messages = [
            "Merge pull request #7 from someone/branch",
            "Merge branch 'main' into feature",
            "docs: explain",
        ]
        self.assertIsNone(rp.required_bump(messages, "1.0.0"))

    def test_non_conventional_subjects_are_errors_listed_together(self):
        messages = ["update stuff", "feat: fine", "WIP", "fixup! feat: fine"]
        with self.assertRaises(rp.ReleasePolicyError) as caught:
            rp.required_bump(messages, "1.0.0")
        problems = caught.exception.problems
        self.assertEqual(3, len(problems))
        for subject in ("update stuff", "WIP", "fixup! feat: fine"):
            with self.subTest(subject=subject):
                self.assertTrue(any(repr(subject) in p for p in problems))

    def test_malformed_headers_are_errors(self):
        for subject in (
            "feat:missing space",
            "feat: ",
            "feat(): empty scope",
            "feat (api): space before scope",
            " feat: leading space",
        ):
            with (
                self.subTest(subject=subject),
                self.assertRaises(rp.ReleasePolicyError),
            ):
                rp.required_bump([subject], "1.0.0")

    def test_unknown_types_are_errors_naming_the_allowed_types(self):
        with self.assertRaises(rp.ReleasePolicyError) as caught:
            rp.required_bump(["revert: undo"], "1.0.0")
        message = str(caught.exception)
        self.assertIn("'revert'", message)
        self.assertIn("feat", message)
        self.assertIn("style", message)

    def test_blank_messages_are_skipped(self):
        self.assertEqual("patch", rp.required_bump(["", "\n", "fix: x"], "1.0.0"))

    def test_rejects_a_malformed_current_version(self):
        with self.assertRaises(rp.ReleasePolicyError):
            rp.required_bump(["feat: add"], "1.0")


class ChangelogSectionTests(unittest.TestCase):
    """Proves: PRV-005"""

    def test_extracts_the_section_body_up_to_the_next_version_heading(self):
        section = rp.changelog_section(CHANGELOG, "0.2.0")
        self.assertEqual(
            "### Added\n\n- A new feature.\n\n### Fixed\n\n- A bug.", section
        )

    def test_excludes_trailing_link_reference_definitions(self):
        section = rp.changelog_section(CHANGELOG, "0.1.0")
        self.assertEqual("### Added\n\n- The first release.", section)

    def test_missing_section_is_an_error_showing_the_expected_heading(self):
        with self.assertRaises(rp.ReleasePolicyError) as caught:
            rp.changelog_section(CHANGELOG, "0.3.0")
        self.assertIn("## [0.3.0] - YYYY-MM-DD", str(caught.exception))

    def test_heading_without_a_valid_date_is_an_error(self):
        for heading in (
            "## [0.3.0]",
            "## [0.3.0] - 2026-13-01",
            "## [0.3.0] - 14/09/2026",
            "## [0.3.0] - 2026-09-14 [YANKED]",
        ):
            with self.subTest(heading=heading):
                text = f"# Changelog\n\n{heading}\n\n- Item.\n"
                with self.assertRaises(rp.ReleasePolicyError) as caught:
                    rp.changelog_section(text, "0.3.0")
                self.assertIn("Keep a Changelog", str(caught.exception))

    def test_duplicate_sections_are_an_error(self):
        text = "## [0.3.0] - 2026-09-14\n\n- A.\n\n## [0.3.0] - 2026-09-15\n\n- B.\n"
        with self.assertRaises(rp.ReleasePolicyError):
            rp.changelog_section(text, "0.3.0")

    def test_empty_section_is_an_error(self):
        text = "## [0.3.0] - 2026-09-14\n\n## [0.2.0] - 2026-09-01\n\n- B.\n"
        with self.assertRaises(rp.ReleasePolicyError) as caught:
            rp.changelog_section(text, "0.3.0")
        self.assertIn("empty", str(caught.exception))

    def test_a_similar_version_is_not_matched(self):
        text = (
            "## [0.3.0-rc.1] - 2026-09-14\n\n- A.\n\n## [10.3.0] - 2026-09-14\n\n- B.\n"
        )
        with self.assertRaises(rp.ReleasePolicyError):
            rp.changelog_section(text, "0.3.0")


class LatestReleaseTests(unittest.TestCase):
    """Proves: PRV-005"""

    def test_no_release_tags_means_no_release_yet(self):
        self.assertIsNone(rp.latest_release([]))
        self.assertIsNone(rp.latest_release(["", "  ", "\n"]))

    def test_picks_the_highest_version_not_the_last_listed(self):
        tags = ["v0.9.0", "v0.10.0\n", " v0.2.1 ", "v0.10.0-never"]
        with self.assertRaises(rp.ReleasePolicyError):
            rp.latest_release(tags)
        self.assertEqual("0.10.0", rp.latest_release(tags[:3]))

    def test_non_release_v_tags_are_errors_listed_together(self):
        with self.assertRaises(rp.ReleasePolicyError) as caught:
            rp.latest_release(["v0.1.0", "v1.0", "vnext"])
        problems = caught.exception.problems
        self.assertEqual(2, len(problems))
        self.assertTrue(any("'v1.0'" in p for p in problems))
        self.assertTrue(any("'vnext'" in p for p in problems))


class CheckPrTests(unittest.TestCase):
    """Proves: PRV-005

    `check_pr(released_version, base_version, head_version, commits,
    changelog_text)`: the bump is measured against the last release tag,
    over every commit since it, never against the pull request's base."""

    FIRST = "## [0.1.0] - 2026-09-14\n\n### Added\n\n- The first release.\n"

    def test_first_release_keeps_the_declared_version_whatever_the_commits(self):
        commits = ["perf: speed up", "feat: add", "fix!: rework"]
        result = rp.check_pr(None, "0.1.0", "0.1.0", commits, self.FIRST)
        self.assertEqual((), result.problems)
        self.assertIsNone(result.bump)
        self.assertEqual("0.1.0", result.expected_version)

    def test_first_release_rejects_a_version_change(self):
        result = rp.check_pr(None, "0.1.0", "0.1.1", ["perf: x"], CHANGELOG)
        self.assertFalse(result.ok)
        self.assertTrue(any("no release tag" in p for p in result.problems))
        self.assertTrue(any("must stay 0.1.0" in p for p in result.problems))

    def test_first_release_still_requires_its_changelog_section(self):
        result = rp.check_pr(None, "0.3.0", "0.3.0", ["docs: explain"], CHANGELOG)
        self.assertFalse(result.ok)
        self.assertTrue(any("## [0.3.0] - YYYY-MM-DD" in p for p in result.problems))

    def test_first_release_still_rejects_non_conventional_commits(self):
        result = rp.check_pr(None, "0.1.0", "0.1.0", ["update stuff"], self.FIRST)
        self.assertFalse(result.ok)
        self.assertTrue(any("'update stuff'" in p for p in result.problems))

    def test_commits_since_the_release_decide_the_bump_not_the_base(self):
        # v0.1.0 is released; an earlier merge already declared 0.2.0 for
        # its `feat`, and this pull request only adds a `fix`.
        commits = ["fix: repair", "Merge pull request #3 from x/y", "feat: add"]
        result = rp.check_pr("0.1.0", "0.2.0", "0.2.0", commits, CHANGELOG)
        self.assertEqual((), result.problems)
        self.assertEqual("minor", result.bump)
        self.assertEqual("0.2.0", result.expected_version)

    def test_nothing_releasable_since_the_release_keeps_the_released_version(self):
        result = rp.check_pr("0.2.0", "0.2.0", "0.2.0", ["docs: a", "ci: b"], "")
        self.assertEqual((), result.problems)
        self.assertIsNone(result.bump)
        self.assertEqual("0.2.0", result.expected_version)

    def test_nothing_releasable_rejects_a_version_change(self):
        result = rp.check_pr("0.2.0", "0.2.0", "0.2.1", ["docs: a"], CHANGELOG)
        self.assertFalse(result.ok)
        self.assertTrue(any("must stay 0.2.0" in p for p in result.problems))

    def test_releasable_commits_require_exactly_one_bump_from_the_release(self):
        for head in ("0.1.5", "1.0.0", "0.3.0", "0.1.4"):
            with self.subTest(head=head):
                result = rp.check_pr("0.1.4", "0.1.4", head, ["feat: add"], CHANGELOG)
                self.assertFalse(result.ok)
                self.assertTrue(any("must be 0.2.0" in p for p in result.problems))

    def test_fix_since_the_release_without_a_bump_fails(self):
        result = rp.check_pr("1.2.3", "1.2.3", "1.2.3", ["fix: repair"], CHANGELOG)
        self.assertFalse(result.ok)
        self.assertTrue(any("patch release" in p for p in result.problems))
        self.assertTrue(any("must be 1.2.4" in p for p in result.problems))

    def test_breaking_change_at_major_zero_requires_minor_not_one_point_zero(self):
        passing = rp.check_pr("0.1.0", "0.1.0", "0.2.0", ["feat!: drop"], CHANGELOG)
        self.assertTrue(passing.ok, passing.problems)
        failing = rp.check_pr("0.1.0", "0.1.0", "1.0.0", ["feat!: drop"], CHANGELOG)
        self.assertFalse(failing.ok)

    def test_breaking_change_after_one_point_zero_requires_major(self):
        changelog = "## [2.0.0] - 2026-09-14\n\n### Removed\n\n- Old API.\n"
        commits = ["fix!: remove old API"]
        result = rp.check_pr("1.9.9", "1.9.9", "2.0.0", commits, changelog)
        self.assertTrue(result.ok, result.problems)

    def test_a_new_version_without_its_changelog_section_fails(self):
        result = rp.check_pr("0.2.0", "0.2.0", "0.3.0", ["feat: add"], CHANGELOG)
        self.assertFalse(result.ok)
        self.assertTrue(any("## [0.3.0] - YYYY-MM-DD" in p for p in result.problems))

    def test_non_conventional_commits_since_the_release_fail_the_check(self):
        result = rp.check_pr("0.1.0", "0.1.0", "0.1.0", ["update stuff"], CHANGELOG)
        self.assertFalse(result.ok)
        self.assertIsNone(result.expected_version)
        self.assertTrue(any("'update stuff'" in p for p in result.problems))

    def test_malformed_versions_fail_the_check(self):
        for released, base, head in (
            ("0.1", "0.1.0", "0.1.0"),
            (None, "0.1", "0.1"),
            ("0.1.0", "0.1.0", "0.2"),
        ):
            with self.subTest(released=released, base=base, head=head):
                result = rp.check_pr(released, base, head, ["docs: x"], CHANGELOG)
                self.assertFalse(result.ok)


class SplitCommitLogTests(unittest.TestCase):
    """Proves: PRV-005"""

    def test_splits_nul_separated_messages_and_drops_blank_entries(self):
        log = "feat: a\n\nbody\n\0\nfix: b\n\0\n"
        self.assertEqual(["feat: a\n\nbody\n", "\nfix: b\n"], rp.split_commit_log(log))


class ReadProjectTests(unittest.TestCase):
    """Proves: PRV-005"""

    @NEEDS_TOMLLIB
    def test_reads_the_project_name_and_version(self):
        self.assertEqual(("semlog", "1.2.3"), rp.read_project(pyproject("1.2.3")))

    @NEEDS_TOMLLIB
    def test_missing_version_or_invalid_toml_is_an_error(self):
        for text in ('[project]\nname = "semlog"\n', "[project\n", 'name = "x"\n'):
            with (
                self.subTest(text=text),
                self.assertRaises(rp.ReleasePolicyError),
            ):
                rp.read_project(text)

    @NEEDS_TOMLLIB
    def test_the_real_pyproject_declares_a_policy_compatible_version(self):
        text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        _name, version = rp.read_project(text)
        rp.parse_version(version)

    @unittest.skipIf(sys.version_info >= (3, 11), "tomllib is available")
    def test_without_tomllib_the_error_names_the_python_requirement(self):
        with self.assertRaises(rp.ReleasePolicyError) as caught:
            rp.read_project(pyproject("1.2.3"))
        self.assertIn("Python 3.11", str(caught.exception))


class TagInfoTests(unittest.TestCase):
    """Proves: PRV-005, PRV-006"""

    @NEEDS_TOMLLIB
    def test_builds_the_tag_name_and_annotated_message(self):
        info = rp.tag_info(pyproject("0.2.0"), CHANGELOG)
        self.assertEqual("0.2.0", info.version)
        self.assertEqual("v0.2.0", info.tag)
        self.assertEqual(
            "semlog 0.2.0\n\n### Added\n\n- A new feature.\n\n### Fixed\n\n- A bug.\n",
            info.message,
        )

    @NEEDS_TOMLLIB
    def test_refuses_a_version_without_a_changelog_section(self):
        with self.assertRaises(rp.ReleasePolicyError):
            rp.tag_info(pyproject("0.3.0"), CHANGELOG)

    @NEEDS_TOMLLIB
    def test_refuses_a_malformed_version(self):
        with self.assertRaises(rp.ReleasePolicyError):
            rp.tag_info(pyproject("0.2"), CHANGELOG)

    @NEEDS_TOMLLIB
    def test_verify_tag_accepts_the_matching_tag(self):
        self.assertEqual(
            "v0.2.0", rp.verify_tag("v0.2.0", pyproject("0.2.0"), CHANGELOG).tag
        )

    @NEEDS_TOMLLIB
    def test_verify_tag_rejects_a_mismatched_or_missing_tag(self):
        for tag in ("v0.1.0", "0.2.0", ""):
            with self.subTest(tag=tag):
                with self.assertRaises(rp.ReleasePolicyError) as caught:
                    rp.verify_tag(tag, pyproject("0.2.0"), CHANGELOG)
                self.assertIn("v0.2.0", str(caught.exception))

    def test_github_output_uses_a_multiline_delimiter_for_the_message(self):
        info = rp.TagInfo(
            name="semlog", version="0.2.0", tag="v0.2.0", message="a\n\nb\n"
        )
        self.assertEqual(
            "version=0.2.0\ntag=v0.2.0\nmessage<<END\na\n\nb\nEND\n",
            rp.github_output(info, delimiter="END"),
        )

    def test_github_output_refuses_a_delimiter_found_in_the_message(self):
        info = rp.TagInfo(name="semlog", version="0.2.0", tag="v0.2.0", message="END\n")
        with self.assertRaises(rp.ReleasePolicyError):
            rp.github_output(info, delimiter="END")

    def test_github_output_generates_an_unguessable_delimiter_by_default(self):
        info = rp.TagInfo(name="semlog", version="0.2.0", tag="v0.2.0", message="a\n")
        first = rp.github_output(info).splitlines()[2]
        second = rp.github_output(info).splitlines()[2]
        self.assertTrue(first.startswith("message<<"))
        self.assertNotEqual(first, second)


class CommandLineTests(unittest.TestCase):
    """Proves: PRV-005, PRV-006"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def write(self, name: str, text: str) -> str:
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    def run_main(self, *argv: str, env: dict | None = None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            mock.patch.dict(os.environ, env or {}, clear=False),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = rp.main(list(argv))
        return code, stdout.getvalue(), stderr.getvalue()

    def check_pr_args(
        self, tags: str, base: str, head: str, commits: list[str]
    ) -> list[str]:
        return [
            "check-pr",
            "--tags",
            self.write("tags.txt", tags),
            "--commits",
            self.write("commits.txt", "\0".join(commits) + "\0"),
            "--base-pyproject",
            self.write("base.toml", pyproject(base)),
            "--head-pyproject",
            self.write("head.toml", pyproject(head)),
            "--changelog",
            self.write("CHANGELOG.md", CHANGELOG),
        ]

    def test_latest_tag_prints_the_highest_release_tag(self):
        tags = self.write("tags.txt", "v0.9.0\nv0.10.0\nv0.2.1\n")
        code, out, _err = self.run_main("latest-tag", "--tags", tags)
        self.assertEqual((0, "v0.10.0\n"), (code, out))

    def test_latest_tag_prints_nothing_before_the_first_release(self):
        code, out, _err = self.run_main("latest-tag", "--tags", self.write("t", ""))
        self.assertEqual((0, ""), (code, out))

    def test_latest_tag_fails_on_a_malformed_release_tag(self):
        tags = self.write("tags.txt", "v0.1.0\nvnext\n")
        code, out, err = self.run_main("latest-tag", "--tags", tags)
        self.assertEqual((1, ""), (code, out))
        self.assertIn("'vnext'", err)

    @NEEDS_TOMLLIB
    def test_check_pr_measures_the_bump_from_the_latest_release_tag(self):
        code, out, _err = self.run_main(
            *self.check_pr_args("v0.1.3\nv0.0.9\n", "0.1.3", "0.2.0", ["feat: add"])
        )
        self.assertEqual(0, code)
        self.assertIn("minor release since v0.1.3: 0.1.3 -> 0.2.0", out)

    @NEEDS_TOMLLIB
    def test_check_pr_accepts_the_declared_version_for_the_first_release(self):
        code, out, _err = self.run_main(
            *self.check_pr_args("", "0.2.0", "0.2.0", ["perf: x", "feat: y"])
        )
        self.assertEqual(0, code)
        self.assertIn("first release: 0.2.0", out)

    @NEEDS_TOMLLIB
    def test_check_pr_fails_with_every_problem_on_stderr(self):
        code, _out, err = self.run_main(
            *self.check_pr_args("v0.2.0\n", "0.2.0", "0.2.1", ["docs: a", "oops"])
        )
        self.assertEqual(1, code)
        self.assertIn("'oops'", err)
        self.assertIn("## [0.2.1] - YYYY-MM-DD", err)

    @NEEDS_TOMLLIB
    def test_tag_info_prints_github_outputs(self):
        code, out, _err = self.run_main(
            "tag-info",
            "--pyproject",
            self.write("pyproject.toml", pyproject("0.2.0")),
            "--changelog",
            self.write("CHANGELOG.md", CHANGELOG),
        )
        self.assertEqual(0, code)
        self.assertTrue(out.startswith("version=0.2.0\ntag=v0.2.0\nmessage<<"))
        self.assertIn("### Fixed", out)

    @NEEDS_TOMLLIB
    def test_tag_info_refuses_a_version_without_a_changelog_section(self):
        code, out, err = self.run_main(
            "tag-info",
            "--pyproject",
            self.write("pyproject.toml", pyproject("0.3.0")),
            "--changelog",
            self.write("CHANGELOG.md", CHANGELOG),
        )
        self.assertEqual(1, code)
        self.assertEqual("", out)
        self.assertIn("## [0.3.0] - YYYY-MM-DD", err)

    @NEEDS_TOMLLIB
    def test_verify_tag_reads_the_tag_from_the_environment(self):
        args = (
            "verify-tag",
            "--pyproject",
            self.write("pyproject.toml", pyproject("0.2.0")),
            "--changelog",
            self.write("CHANGELOG.md", CHANGELOG),
        )
        code, out, _err = self.run_main(*args, env={"RELEASE_TAG": "v0.2.0"})
        self.assertEqual(0, code)
        self.assertIn("v0.2.0", out)
        code, _out, err = self.run_main(*args, env={"RELEASE_TAG": "v9.9.9"})
        self.assertEqual(1, code)
        self.assertIn("v9.9.9", err)

    def test_unreadable_input_fails_cleanly(self):
        code, _out, err = self.run_main(
            "tag-info", "--pyproject", str(self.tmp / "missing.toml")
        )
        self.assertEqual(1, code)
        self.assertIn("missing.toml", err)


class StdlibOnlyToolTests(unittest.TestCase):
    """Proves: CP-008"""

    def test_the_tool_imports_only_the_standard_library(self):
        tree = ast.parse(TOOL_PATH.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
        self.assertTrue(imported, "no imports found; is the tool empty?")
        # `tomllib` joined the standard library in 3.11, so 3.10's list lacks
        # it; the tool imports it lazily and fails clearly without it.
        stdlib = set(sys.stdlib_module_names) | {"tomllib"}
        self.assertEqual(set(), imported - stdlib)


if __name__ == "__main__":
    unittest.main()
