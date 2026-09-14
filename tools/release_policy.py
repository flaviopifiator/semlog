"""Release policy for semlog (STANDARDS.md PRV-005, PRV-006; RELEASING.md).

Decides the version bump a set of Conventional Commits requires, checks a
pull request's version and CHANGELOG.md against its commits, and describes
the annotated tag for the version declared on `main`. The policy refuses
to guess: a commit subject that is not a Conventional Commit, a malformed
version, or a missing CHANGELOG.md section is an error, never a silent
"no release".

Standard library only. The workflows run it as a script, passing inputs as
arguments and environment variables:

    python tools/release_policy.py latest-tag --tags FILE
    python tools/release_policy.py check-pr --tags FILE --commits FILE \\
        --base-pyproject FILE [--head-pyproject FILE] [--changelog FILE]
    python tools/release_policy.py tag-info [--pyproject FILE] [--changelog FILE]
    RELEASE_TAG=v1.2.3 python tools/release_policy.py verify-tag
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import secrets
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

MAJOR = "major"
MINOR = "minor"
PATCH = "patch"
_LEVEL_RANK = {None: 0, PATCH: 1, MINOR: 2, MAJOR: 3}

RELEASE_TYPES = {"feat": MINOR, "fix": PATCH, "perf": PATCH}
NO_RELEASE_TYPES = ("build", "chore", "ci", "docs", "refactor", "style", "test")

_VERSION_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
_HEADER_RE = re.compile(
    r"(?P<type>[A-Za-z]+)(?:\((?P<scope>[^()\r\n]+)\))?(?P<breaking>!)?: \S.*"
)
_BREAKING_FOOTER_RE = re.compile(r"BREAKING[ -]CHANGE: \S")
_LINK_REFERENCE_RE = re.compile(r"\[[^\]]+\]: \S+")


class ReleasePolicyError(ValueError):
    """One or more release policy violations, each a readable sentence."""

    def __init__(self, *problems: str):
        super().__init__("\n".join(problems))
        self.problems = tuple(problems)


def parse_version(version: str) -> tuple[int, int, int]:
    """Parse a plain SemVer `MAJOR.MINOR.PATCH` version (no pre-release or
    build metadata, no leading zeros)."""
    match = _VERSION_RE.fullmatch(version)
    if not match:
        raise ReleasePolicyError(
            f"malformed version {version!r}: expected MAJOR.MINOR.PATCH, such "
            "as 1.4.0, without leading zeros, pre-release, or build metadata"
        )
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def next_version(current_version: str, bump: str) -> str:
    """Return `current_version` bumped by exactly one `bump` level."""
    major, minor, patch = parse_version(current_version)
    if bump == MAJOR:
        return f"{major + 1}.0.0"
    if bump == MINOR:
        return f"{major}.{minor + 1}.0"
    if bump == PATCH:
        return f"{major}.{minor}.{patch + 1}"
    raise ReleasePolicyError(
        f"unknown bump level {bump!r}: expected {MAJOR!r}, {MINOR!r}, or {PATCH!r}"
    )


def _commit_level(message: str) -> str | None:
    """Return the level one commit requires, before the major-zero rule."""
    lines = message.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return None
    subject = lines[0].rstrip()
    if subject.startswith("Merge "):
        return None
    match = _HEADER_RE.fullmatch(subject)
    if not match:
        raise ReleasePolicyError(
            f"not a Conventional Commit: {subject!r} (expected "
            "'type(optional scope)!: description')"
        )
    commit_type = match.group("type").lower()
    if commit_type not in RELEASE_TYPES and commit_type not in NO_RELEASE_TYPES:
        allowed = ", ".join(sorted((*RELEASE_TYPES, *NO_RELEASE_TYPES)))
        raise ReleasePolicyError(
            f"unknown commit type {commit_type!r} in {subject!r}; allowed: {allowed}"
        )
    breaking = match.group("breaking") or any(
        _BREAKING_FOOTER_RE.match(line) for line in lines[1:]
    )
    if breaking:
        return MAJOR
    return RELEASE_TYPES.get(commit_type)


def required_bump(commit_messages: Iterable[str], current_version: str) -> str | None:
    """Return the bump level (`major`, `minor`, `patch`) the commits require,
    or `None` when none requires a release.

    `feat` requires minor; `fix` and `perf` require patch; a `!` after the
    type or scope, or a `BREAKING CHANGE:` footer, requires major, except
    that while the major version is 0 it requires minor (SemVer section 4).
    Merge commits are ignored. Every non-conventional subject is reported
    together in one `ReleasePolicyError`.
    """
    major, _minor, _patch = parse_version(current_version)
    level = None
    problems = []
    for message in commit_messages:
        try:
            commit_level = _commit_level(message)
        except ReleasePolicyError as error:
            problems.extend(error.problems)
            continue
        if _LEVEL_RANK[commit_level] > _LEVEL_RANK[level]:
            level = commit_level
    if problems:
        raise ReleasePolicyError(*problems)
    if level == MAJOR and major == 0:
        return MINOR
    return level


def changelog_section(changelog_text: str, version: str) -> str:
    """Return the body of the Keep a Changelog section for `version`: the
    lines under its `## [X.Y.Z] - YYYY-MM-DD` heading, up to the next
    level-two heading, without surrounding blank lines or trailing link
    reference definitions."""
    expected = f"## [{version}] - YYYY-MM-DD"
    lines = changelog_text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith(f"## [{version}]")]
    if not starts:
        raise ReleasePolicyError(
            f"CHANGELOG.md has no section for {version}; add a '{expected}' "
            "heading (Keep a Changelog) describing the release"
        )
    if len(starts) > 1:
        raise ReleasePolicyError(
            f"CHANGELOG.md has more than one section for {version}"
        )
    start = starts[0]
    heading = lines[start]
    match = re.fullmatch(
        re.escape(f"## [{version}] - ") + r"(\d{4}-\d{2}-\d{2})", heading
    )
    if not match or not _is_iso_date(match.group(1)):
        raise ReleasePolicyError(
            f"CHANGELOG.md heading {heading!r} is not in Keep a Changelog form "
            f"'{expected}' with a real date"
        )
    body = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        body.append(line)
    while body and (not body[-1].strip() or _LINK_REFERENCE_RE.fullmatch(body[-1])):
        body.pop()
    while body and not body[0].strip():
        body.pop(0)
    if not body:
        raise ReleasePolicyError(f"CHANGELOG.md section for {version} is empty")
    return "\n".join(body)


def _is_iso_date(text: str) -> bool:
    try:
        datetime.date.fromisoformat(text)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class PrCheck:
    """Outcome of `check_pr`: the required bump, the version the head must
    declare (`None` when it cannot be determined), and every problem."""

    bump: str | None
    expected_version: str | None
    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.problems


def latest_release(tags: Iterable[str]) -> str | None:
    """Return the highest released version among `vMAJOR.MINOR.PATCH` tags
    (for example `git tag --list 'v*'` output), or `None` before the first
    release. Any other `v*` tag is an error, because the policy cannot tell
    which release it marks."""
    versions = []
    problems = []
    for raw_tag in tags:
        tag = raw_tag.strip()
        if not tag:
            continue
        if tag.startswith("v") and _VERSION_RE.fullmatch(tag[1:]):
            versions.append(parse_version(tag[1:]))
        else:
            problems.append(
                f"tag {tag!r} is not a release tag of the form vMAJOR.MINOR.PATCH"
            )
    if problems:
        raise ReleasePolicyError(*problems)
    if not versions:
        return None
    return ".".join(str(part) for part in max(versions))


def check_pr(
    released_version: str | None,
    base_version: str,
    head_version: str,
    commits: Iterable[str],
    changelog_text: str,
) -> PrCheck:
    """Check a pull request's head version and CHANGELOG.md against the last
    release.

    `released_version` is the version of the latest release tag, or `None`
    before the first release. `commits` are the messages since that tag, up
    to the pull request head; before the first release they are the pull
    request's own commits, which are validated but decide no bump.

    - After a release, the head version must be the released version bumped
      by exactly the highest level the commits require, or the released
      version itself when none requires a release.
    - Before the first release, the head version must stay the version the
      base branch declares.
    - Every version that is not released yet needs its CHANGELOG.md section.
    """
    problems: list[str] = []
    bump = None
    expected = None
    try:
        if released_version is None:
            parse_version(base_version)
            expected = base_version
            required_bump(commits, base_version)  # validates the subjects
        else:
            bump = required_bump(commits, released_version)
            expected = (
                next_version(released_version, bump) if bump else released_version
            )
    except ReleasePolicyError as error:
        problems.extend(error.problems)
    try:
        parse_version(head_version)
    except ReleasePolicyError as error:
        problems.extend(error.problems)
        return PrCheck(bump, expected, tuple(problems))
    if expected is not None and head_version != expected:
        if released_version is None:
            problems.append(
                "no release tag exists yet, so this is the first release: the "
                f"version must stay {expected}, as the base branch declares; "
                f"pyproject.toml declares {head_version}"
            )
        elif bump:
            problems.append(
                f"the commits since v{released_version} require a {bump} release, "
                f"so the version must be {expected}; pyproject.toml declares "
                f"{head_version}"
            )
        else:
            problems.append(
                f"no commit since v{released_version} requires a release, so the "
                f"version must stay {released_version}; pyproject.toml declares "
                f"{head_version}"
            )
    if head_version != released_version:
        try:
            changelog_section(changelog_text, head_version)
        except ReleasePolicyError as error:
            problems.extend(error.problems)
    return PrCheck(bump, expected, tuple(problems))


def split_commit_log(text: str) -> list[str]:
    """Split `git log --format=%B%x00` output into commit messages."""
    return [message for message in text.split("\0") if message.strip()]


def read_project(pyproject_text: str) -> tuple[str, str]:
    """Return `[project]`'s `name` and `version` from `pyproject.toml`."""
    try:
        import tomllib
    except ModuleNotFoundError as error:  # Python 3.10
        raise ReleasePolicyError(
            "reading pyproject.toml needs tomllib (Python 3.11 or newer)"
        ) from error
    try:
        project = tomllib.loads(pyproject_text).get("project", {})
    except tomllib.TOMLDecodeError as error:
        raise ReleasePolicyError(
            f"pyproject.toml is not valid TOML: {error}"
        ) from error
    name, version = project.get("name"), project.get("version")
    if not isinstance(name, str) or not isinstance(version, str):
        raise ReleasePolicyError(
            "pyproject.toml must declare [project] name and version as strings"
        )
    return name, version


@dataclass(frozen=True)
class TagInfo:
    """The annotated tag for a project version."""

    name: str
    version: str
    tag: str
    message: str


def tag_info(pyproject_text: str, changelog_text: str) -> TagInfo:
    """Describe the tag `v<version>` for the version in `pyproject.toml`,
    with that version's CHANGELOG.md section as the annotated message."""
    name, version = read_project(pyproject_text)
    parse_version(version)
    section = changelog_section(changelog_text, version)
    return TagInfo(name, version, f"v{version}", f"{name} {version}\n\n{section}\n")


def verify_tag(tag: str, pyproject_text: str, changelog_text: str) -> TagInfo:
    """Check that `tag` is the tag for the project version and that the
    version has its CHANGELOG.md section."""
    _name, version = read_project(pyproject_text)
    if tag != f"v{version}":
        raise ReleasePolicyError(
            f"tag {tag!r} does not match the project version: expected 'v{version}'"
        )
    return tag_info(pyproject_text, changelog_text)


def github_output(info: TagInfo, delimiter: str | None = None) -> str:
    """Format `info` for `$GITHUB_OUTPUT`, using the multiline syntax with an
    unguessable delimiter for the message."""
    if delimiter is None:
        delimiter = f"EOF_{secrets.token_hex(16)}"
    if delimiter in info.message:
        raise ReleasePolicyError("the output delimiter appears in the tag message")
    message = info.message.rstrip("\n")
    return (
        f"version={info.version}\ntag={info.tag}\n"
        f"message<<{delimiter}\n{message}\n{delimiter}\n"
    )


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as error:
        raise ReleasePolicyError(f"cannot read {path}: {error.strerror}") from error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release_policy.py", description="semlog release policy"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    latest = commands.add_parser(
        "latest-tag", help="print the latest release tag, or nothing before one"
    )
    latest.add_argument("--tags", required=True, help="file listing tags, one per line")

    check = commands.add_parser(
        "check-pr", help="check a pull request's version and changelog"
    )
    check.add_argument("--tags", required=True, help="file listing tags, one per line")
    check.add_argument(
        "--commits",
        required=True,
        help="file with the commit messages since the latest release tag, "
        "NUL-separated (git log --format=%%B%%x00)",
    )
    check.add_argument(
        "--base-pyproject", required=True, help="pyproject.toml at the base commit"
    )
    check.add_argument(
        "--head-pyproject", default="pyproject.toml", help="pyproject.toml at the head"
    )
    check.add_argument(
        "--changelog", default="CHANGELOG.md", help="CHANGELOG.md at the head"
    )

    for command, summary in (
        ("tag-info", "print the tag name and message as GitHub step outputs"),
        ("verify-tag", "check that the tag in RELEASE_TAG matches the version"),
    ):
        sub = commands.add_parser(command, help=summary)
        sub.add_argument("--pyproject", default="pyproject.toml")
        sub.add_argument("--changelog", default="CHANGELOG.md")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "latest-tag":
            released = latest_release(_read(args.tags).splitlines())
            if released is not None:
                print(f"v{released}")
        elif args.command == "check-pr":
            released = latest_release(_read(args.tags).splitlines())
            commits = split_commit_log(_read(args.commits))
            _name, base_version = read_project(_read(args.base_pyproject))
            _name, head_version = read_project(_read(args.head_pyproject))
            result = check_pr(
                released, base_version, head_version, commits, _read(args.changelog)
            )
            if not result.ok:
                raise ReleasePolicyError(*result.problems)
            if released is None:
                summary = f"first release: {head_version} (no release tag yet)"
            elif result.bump:
                summary = (
                    f"{result.bump} release since v{released}: "
                    f"{released} -> {head_version}"
                )
            else:
                summary = (
                    f"no release required since v{released}; version stays {released}"
                )
            print(f"Release policy check passed: {summary}")
        elif args.command == "tag-info":
            pyproject_text = _read(args.pyproject)
            info = tag_info(pyproject_text, _read(args.changelog))
            sys.stdout.write(github_output(info))
        else:
            tag = os.environ.get("RELEASE_TAG", "")
            pyproject_text = _read(args.pyproject)
            info = verify_tag(tag, pyproject_text, _read(args.changelog))
            print(f"Tag {info.tag} matches project version {info.version}.")
    except ReleasePolicyError as error:
        print("Release policy check failed:", file=sys.stderr)
        for problem in error.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
