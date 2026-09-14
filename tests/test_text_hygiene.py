"""Dash hygiene for every tracked text file.

No tracked text file may contain an em dash (U+2014) or an en dash
(U+2013): documentation, comments, docstrings, test descriptions, workflow
comments and generated report text use a colon, a comma, parentheses, or
a new sentence instead, and a hyphen for numeric ranges. The en dash is
flagged everywhere, not only in prose, because the repository has no
legitimate use for it: ranges such as `3.10-3.14` are written with a
hyphen.

Files come from `git ls-files`; outside a Git checkout, a walk of the
repository skips `.git` and the ignored build, cache and virtual
environment directories. Binary files (a NUL byte, or not UTF-8) are
skipped. Not tied to a single STANDARDS.md requirement id (same precedent
as `tests/test_class_budget.py`), so this module carries no `Proves:`
line.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_DASHES = {"\u2014": "em dash (U+2014)", "\u2013": "en dash (U+2013)"}
_SKIPPED_DIRS = frozenset(
    {".git", ".venv", "__pycache__", ".ruff_cache", ".codegraph", "build", "dist"}
)


def tracked_files(root):
    """Repository-relative paths of the files Git tracks under `root`, or
    of every file outside the skipped directories when Git is unavailable."""
    try:
        listing = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file()
            and not _SKIPPED_DIRS.intersection(path.relative_to(root).parts)
            and not any(part.endswith(".egg-info") for part in path.parts)
        )
    return sorted(name for name in listing.decode("utf-8").split("\0") if name)


def text_of(path):
    """The UTF-8 text of `path`, or `None` for a binary file."""
    data = path.read_bytes()
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def dash_offenders(name, text):
    """`"file:line: <dash name>"` for every line of `text` with a forbidden
    dash."""
    return [
        f"{name}:{line_no}: {label}"
        for line_no, line in enumerate(text.splitlines(), start=1)
        for dash, label in FORBIDDEN_DASHES.items()
        if dash in line
    ]


class TrackedTextDashTests(unittest.TestCase):
    def test_no_tracked_text_file_contains_an_em_or_en_dash(self):
        names = tracked_files(REPO_ROOT)
        self.assertGreater(len(names), 0)
        offenders = []
        for name in names:
            path = REPO_ROOT / name
            if not path.is_file():
                continue
            text = text_of(path)
            if text is not None:
                offenders.extend(dash_offenders(name, text))
        self.assertEqual(
            [],
            offenders,
            "replace each dash with a colon, comma, parentheses or a new "
            "sentence:\n" + "\n".join(offenders),
        )


class TrackedTextDashSelfTests(unittest.TestCase):
    """Each check fails on a crafted input, never on the real files."""

    def test_an_em_dash_is_reported_with_its_file_and_line(self):
        text = "First line.\nA rule \u2014 with an aside.\n"
        self.assertEqual(["doc.md:2: em dash (U+2014)"], dash_offenders("doc.md", text))

    def test_an_en_dash_is_reported(self):
        self.assertEqual(
            ["doc.md:1: en dash (U+2013)"],
            dash_offenders("doc.md", "Python 3.10\u20133.14\n"),
        )

    def test_a_hyphen_and_a_minus_sign_are_not_reported(self):
        self.assertEqual([], dash_offenders("doc.md", "3.10-3.14, x \u2212 y\n"))

    def test_a_binary_file_is_skipped(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "image.bin"
            binary.write_bytes(b"\x89PNG\0\xff\xfe")
            latin = Path(tmp) / "latin.txt"
            latin.write_bytes("café".encode("latin-1"))
            self.assertIsNone(text_of(binary))
            self.assertIsNone(text_of(latin))

    def test_the_fallback_walk_skips_git_and_cache_directories(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in (".git/HEAD", ".venv/lib/x.py", "__pycache__/a.pyc", "a.md"):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text("x", encoding="utf-8")
            listed = tracked_files(root)
        self.assertIn("a.md", listed)
        self.assertFalse([n for n in listed if n.startswith((".git/", ".venv/"))])
        self.assertFalse([n for n in listed if "__pycache__" in n])


if __name__ == "__main__":
    unittest.main()
