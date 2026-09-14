"""Source line-budget guard (STANDARDS.md CP-017; design #162/#170 §8).

Counts non-blank, non-comment physical source lines in every
``src/semlog/**/*.py`` file with stdlib ``tokenize`` only: a line counts
when it is covered by any token other than ``COMMENT``, ``NL``,
``NEWLINE``, ``INDENT``, ``DEDENT``, ``ENCODING`` or ``ENDMARKER``, and its
source text is not whitespace-only (this is what drops a blank line that
merely sits inside a multi-line docstring's span; a docstring line that
has real text, including one that happens to start with ``#``, counts).
Tests are outside ``src/`` and never counted. The bundled UUIDv7 fallback
(``_uuid7_fallback.py``) is excluded by filename, matching CP-017's
"excluding ... the UUIDv7 fallback implementation" clause; package
data (the agent guide) is excluded by construction, since only ``*.py``
files are globbed. This module never imports production code.

Now cites ``Proves: CP-017`` (task 4.6, Phase 4 batch 4): CP-017 also
requires the budget to be "published, and enforced in continuous
integration," and `.github/workflows/ci.yml`'s ``test`` job now runs
``python -m unittest discover`` on every push/PR to `main` -- which
includes this exact module -- so the enforcement clause is genuinely
satisfied. Before this batch, citing CP-017 here would have tripped the
traceability checker's ratchet (`tests/test_traceability.py`'s
`NOT_YET_PROVEN` stale-allowlist check) on a requirement that was only
partly proven; that is no longer the case now that CI exists.
"""

from __future__ import annotations

import re
import tempfile
import tokenize
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "src" / "semlog"
STANDARDS_PATH = REPO_ROOT / "STANDARDS.md"

BUDGET = 1200
EXCLUDED_FILENAMES = frozenset({"_uuid7_fallback.py"})

_IGNORED_TOKEN_TYPES = frozenset(
    {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENCODING,
        tokenize.ENDMARKER,
    }
)


def count_source_lines(path: Path) -> int:
    """Count physical lines of `path` covered by any counted token, minus
    whitespace-only lines."""
    source_lines = path.read_text(encoding="utf-8").splitlines()
    covered: set[int] = set()
    with tokenize.open(path) as handle:
        for tok in tokenize.generate_tokens(handle.readline):
            if tok.type in _IGNORED_TOKEN_TYPES:
                continue
            covered.update(range(tok.start[0], tok.end[0] + 1))
    return sum(1 for line_no in covered if source_lines[line_no - 1].strip())


def _iter_package_files() -> list[Path]:
    return sorted(
        p for p in PACKAGE_DIR.rglob("*.py") if p.name not in EXCLUDED_FILENAMES
    )


def _standards_budget() -> int:
    match = re.search(
        r"maximum budget of \*\*(\d+) non-blank, non-comment lines",
        STANDARDS_PATH.read_text(encoding="utf-8"),
    )
    if match is None:
        raise AssertionError("CP-017's line-budget number not found in STANDARDS.md")
    return int(match.group(1))


class SourceBudgetTests(unittest.TestCase):
    """Proves: CP-017"""

    def test_total_source_lines_within_budget(self):
        counts = {p.name: count_source_lines(p) for p in _iter_package_files()}
        total = sum(counts.values())
        breakdown = ", ".join(f"{name}={n}" for name, n in sorted(counts.items()))
        self.assertLessEqual(total, BUDGET, f"{total}/{BUDGET} lines: {breakdown}")

    def test_standards_md_budget_matches_this_test(self):
        self.assertEqual(BUDGET, _standards_budget())

    def test_uuid7_fallback_module_is_excluded_by_filename(self):
        names = {p.name for p in _iter_package_files()}
        self.assertNotIn("_uuid7_fallback.py", names)
        self.assertTrue((PACKAGE_DIR / "_uuid7_fallback.py").is_file())


def _count_source(text: str) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "fixture.py"
        path.write_text(text, encoding="utf-8")
        return count_source_lines(path)


class SourceBudgetSelfTests(unittest.TestCase):
    """Fixture self-tests over crafted strings (never imports production
    code): a comment line, a blank line, a blank line inside a docstring, a
    docstring line starting with ``#``, and code followed by a trailing
    comment."""

    def test_comment_only_line_is_not_counted(self):
        self.assertEqual(0, _count_source("# just a comment\n"))

    def test_blank_line_is_not_counted(self):
        self.assertEqual(0, _count_source("\n"))

    def test_blank_line_inside_docstring_is_not_counted(self):
        text = '"""Line one.\n\nLine three."""\n'
        self.assertEqual(2, _count_source(text))

    def test_docstring_line_starting_with_hash_counts(self):
        text = '"""\n# looks like a comment but is inside a string\n"""\n'
        self.assertEqual(3, _count_source(text))

    def test_code_followed_by_trailing_comment_counts_once(self):
        self.assertEqual(1, _count_source("x = 1  # trailing comment\n"))


if __name__ == "__main__":
    unittest.main()
