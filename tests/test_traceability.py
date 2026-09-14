"""Traceability checker for STANDARDS.md (design #223 P3-ADR-1..5).

Parses STANDARDS.md and every ``tests/**/test*.py`` file with the standard
library only (Markdown line scanning plus ``ast.parse``, never an import),
and enforces:

- TRC-001: the requirement-ID grammar, uniqueness, and prefix-table
  consistency.
- TRC-002: every ``Proves:`` citation in a test is well-formed and cites a
  requirement ID that is actually declared.
- TRC-003: every declared requirement is proven by a citing test.
  Coverage is unconditional (task 4.33/5.2): the allowlist this checker
  used while Phase 5's benchmark was still pending (``NOT_YET_PROVEN``,
  holding only ``CP-007`` from Phase 4 onward) is gone now that
  `tests/test_benchmark_contention.py` proves CP-007 for real.
- TRC-004 / DOC-003: every requirement declares its backing (an external
  reference or a design decision), consistently with the Annex A row and,
  for design-decision rules, exactly one rationale line inside the rule's
  own span.
- CMA-004: Annex B claims support only for declared requirement IDs.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
STANDARDS_PATH = REPO_ROOT / "STANDARDS.md"
TESTS_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Grammar (design #223 §2, "Regular expressions")
# ---------------------------------------------------------------------------

ID_PATTERN = r"[A-Z]{2,4}-[0-9]{3}"
TAG_PATTERN = r"[A-Z0-9][A-Z0-9.\-]*"

ID_RE = re.compile(ID_PATTERN)
DECL_RE = re.compile(r"^\*\*(" + ID_PATTERN + r")\*\*: ")
DECL_LIKE_RE = re.compile(r"^\*\*[A-Za-z]+-?[0-9]+\*\*")
HEADING_RE = re.compile(r"^(#{1,6}) ")
TAGDEF_RE = re.compile(r"^- \[(" + TAG_PATTERN + r")\] ")
CITE_RE = re.compile(r"\[(" + TAG_PATTERN + r")\](?!\()")
CODE_SPAN_RE = re.compile(r"`[^`]*`")
RATIONALE_RE = re.compile(
    r"^> \*\*Design decision \((" + ID_PATTERN + r")\)\.\*\* "
    r"Rationale: \S.* Rejected alternative: \S.*$"
)
RATIONALE_LIKE_RE = re.compile(r"^> \*\*Design decision")
BACKING_RE = re.compile(
    r"^(Design decision|External: \[" + TAG_PATTERN + r"\]"
    r"(, \[" + TAG_PATTERN + r"\])*)$"
)
PROVES_RE = re.compile(r"^Proves: (" + ID_PATTERN + r")(, " + ID_PATTERN + r")*$")
PROVES_LIKE_RE = re.compile(r"(?i)^proves:")

ANNEX_A_HEADER = "| Requirement | Section | Backing | Planned test |"
ANNEX_B_HEADER = (
    "| Control | What it requires (paraphrase) | SEMLOG contributes "
    "| The adopting organization must |"
)

# ---------------------------------------------------------------------------
# STANDARDS.md parsing
# ---------------------------------------------------------------------------


class Standards:
    """Parsed view of a STANDARDS.md-shaped document."""

    def __init__(self):
        self.prefixes = {}  # prefix -> capability
        self.prefix_duplicates = []
        self.declarations = {}  # id -> line_no
        self.malformed_declarations = []  # line_no
        self.duplicate_declarations = []  # id
        self.spans = {}  # id -> (start_line, end_line) inclusive
        self.section12_tags = {}  # tag -> line_no
        self.section12_tag_duplicates = []  # tag
        self.citations = {}  # tag -> [line_no, ...] (outside section 12)
        self.annex_a = {}  # id -> backing cell text
        self.annex_a_duplicates = []  # id
        self.annex_a_header_ok = False
        self.annex_b_header_ok = False
        self.annex_b_ids = set()
        self.rationale = {}  # id -> line_no
        self.malformed_rationale = []  # line_no


def find_citations(line):
    """Return the reference tags cited on ``line``, ignoring code spans."""
    clean = CODE_SPAN_RE.sub("", line)
    return CITE_RE.findall(clean)


def _iter_body_lines(text):
    """Yield ``(line_no, line)`` for lines outside fenced code blocks."""
    in_fence = False
    for line_no, line in enumerate(text.splitlines(), start=1):
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        yield line_no, line


def _section_bounds(lines, heading_at, prefix, level):
    """Return ``(start, end)`` (end exclusive) for the section whose H<level>
    heading starts with ``prefix``, or ``None`` when not found."""
    start = None
    for line_no, line in lines:
        if heading_at.get(line_no) == level and line.startswith(prefix):
            start = line_no
            break
    if start is None:
        return None
    end = lines[-1][0] + 1 if lines else start + 1
    for line_no in sorted(heading_at):
        if line_no > start and heading_at[line_no] <= level:
            end = line_no
            break
    return start, end


def _parse_table(lines, start, end, header):
    """Return ``(header_found, rows)`` for a pipe table whose header line
    equals ``header`` (stripped), scoped to ``[start, end)``. Each row is
    ``(line_no, cells)``."""
    header_found = False
    in_table = False
    rows = []
    for line_no, line in lines:
        if not (start <= line_no < end):
            continue
        stripped = line.strip()
        if stripped == header:
            header_found = True
            in_table = True
            continue
        if in_table:
            if stripped.startswith("|---"):
                continue
            if not stripped.startswith("|"):
                in_table = False
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            rows.append((line_no, cells))
    return header_found, rows


def parse_standards(text):
    doc = Standards()
    lines = list(_iter_body_lines(text))
    last_line = lines[-1][0] if lines else 0

    heading_at = {}
    for line_no, line in lines:
        m = HEADING_RE.match(line)
        if m:
            heading_at[line_no] = len(m.group(1))

    # --- Prefix table (section 1.1) ---
    prefix_bounds = _section_bounds(lines, heading_at, "### 1.1", 3)
    if prefix_bounds:
        start, end = prefix_bounds
        _found, rows = _parse_table(lines, start, end, "| Prefix | Capability |")
        for _line_no, cells in rows:
            if len(cells) != 2:
                continue
            prefix, capability = cells
            if prefix in doc.prefixes or capability in doc.prefixes.values():
                doc.prefix_duplicates.append(prefix)
            doc.prefixes[prefix] = capability

    # --- Declarations and their spans ---
    decl_lines = []
    for line_no, line in lines:
        decl_match = DECL_RE.match(line)
        if decl_match:
            req_id = decl_match.group(1)
            if req_id in doc.declarations:
                doc.duplicate_declarations.append(req_id)
            else:
                doc.declarations[req_id] = line_no
            decl_lines.append(line_no)
        elif DECL_LIKE_RE.match(line):
            doc.malformed_declarations.append(line_no)

    boundaries = sorted(set(decl_lines) | set(heading_at))
    for req_id, start in doc.declarations.items():
        end = last_line
        for boundary in boundaries:
            if boundary > start:
                end = boundary - 1
                break
        doc.spans[req_id] = (start, end)

    # --- Section 12: reference definitions ---
    section12_bounds = _section_bounds(lines, heading_at, "## 12.", 2)
    section12_range = (
        range(section12_bounds[0], section12_bounds[1])
        if section12_bounds
        else range(0)
    )
    for line_no, line in lines:
        if line_no not in section12_range:
            continue
        tag_match = TAGDEF_RE.match(line)
        if tag_match:
            tag = tag_match.group(1)
            if tag in doc.section12_tags:
                doc.section12_tag_duplicates.append(tag)
            doc.section12_tags[tag] = line_no

    # --- Citations outside section 12 ---
    for line_no, line in lines:
        if line_no in section12_range:
            continue
        for tag in find_citations(line):
            doc.citations.setdefault(tag, []).append(line_no)

    # --- Rationale lines ---
    for line_no, line in lines:
        rationale_match = RATIONALE_RE.match(line)
        if rationale_match:
            doc.rationale[rationale_match.group(1)] = line_no
        elif RATIONALE_LIKE_RE.match(line):
            doc.malformed_rationale.append(line_no)

    # --- Annex A ---
    annex_a_bounds = _section_bounds(lines, heading_at, "## Annex A", 2)
    if annex_a_bounds:
        start, end = annex_a_bounds
        header_found, rows = _parse_table(lines, start, end, ANNEX_A_HEADER)
        doc.annex_a_header_ok = header_found
        for _line_no, cells in rows:
            if len(cells) != 4:
                continue
            req_id, _section, backing, _test = cells
            if req_id in doc.annex_a:
                doc.annex_a_duplicates.append(req_id)
            doc.annex_a[req_id] = backing

    # --- Annex B ---
    annex_b_bounds = _section_bounds(lines, heading_at, "## Annex B", 2)
    if annex_b_bounds:
        start, end = annex_b_bounds
        header_found, rows = _parse_table(lines, start, end, ANNEX_B_HEADER)
        doc.annex_b_header_ok = header_found
        for _line_no, cells in rows:
            if len(cells) != 4:
                continue
            doc.annex_b_ids.update(ID_RE.findall(cells[2]))

    return doc


# ---------------------------------------------------------------------------
# Test-source parsing (never imports a test file, only ``ast.parse``s it)
# ---------------------------------------------------------------------------


class ProvesIndex:
    def __init__(self):
        self.cited = set()
        self.malformed = []  # "path::name: raw-line"
        self.locations = {}  # id -> [path::name, ...]


def _decorator_name(deco):
    target = deco.func if isinstance(deco, ast.Call) else deco
    if isinstance(target, ast.Attribute):
        return target.attr
    if isinstance(target, ast.Name):
        return target.id
    return None


def _has_unconditional_skip(node):
    for deco in getattr(node, "decorator_list", []):
        name = _decorator_name(deco)
        if name in ("skip", "expectedFailure"):
            return True
        if name == "skipIf" and isinstance(deco, ast.Call) and deco.args:
            arg = deco.args[0]
            if isinstance(arg, ast.Constant) and arg.value is True:
                return True
    return False


def _proves_lines(node):
    docstring = ast.get_docstring(node, clean=False) or ""
    for raw_line in docstring.splitlines():
        line = raw_line.strip()
        if PROVES_LIKE_RE.match(line):
            yield line


def collect_proves(root):
    """Scan ``root/**/test*.py`` for ``Proves:`` docstring lines.

    Every source file is parsed with ``ast.parse`` only -- never imported --
    so a test module that raises at import time is still scanned safely.
    """
    index = ProvesIndex()
    for path in sorted(Path(root).rglob("test*.py")):
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        try:
            relative = path.relative_to(REPO_ROOT)
        except ValueError:
            relative = path
        for node in ast.walk(tree):
            is_class = isinstance(node, ast.ClassDef)
            is_test_func = isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef)
            ) and node.name.startswith("test")
            if not (is_class or is_test_func):
                continue
            if _has_unconditional_skip(node):
                continue
            location = f"{relative}::{node.name}"
            for line in _proves_lines(node):
                proves_match = PROVES_RE.match(line)
                if not proves_match:
                    index.malformed.append(f"{location}: {line!r}")
                    continue
                for req_id in ID_RE.findall(line):
                    index.cited.add(req_id)
                    index.locations.setdefault(req_id, []).append(location)
    return index


# ---------------------------------------------------------------------------
# Checks (design #223 §2, "Coverage" and "Reporting")
# ---------------------------------------------------------------------------


def find_unknown_proves(declared_ids, proves):
    """Requirement ids cited by a test but never declared (TRC-002)."""
    return sorted(proves.cited - declared_ids)


def find_uncovered(declared_ids, proves):
    """Declared ids with no citing test at all (TRC-003, unconditional
    since task 5.2: no allowlist remains -- every declared requirement
    must be proven by a real test)."""
    return sorted(declared_ids - proves.cited)


def find_unresolved_citations(standards):
    """Cited tags with no section-12 definition (DOC-003)."""
    return sorted(
        tag for tag in standards.citations if tag not in standards.section12_tags
    )


def find_orphan_references(standards):
    """Section-12 tags defined but never cited outside section 12 (DOC-003)."""
    return sorted(
        tag for tag in standards.section12_tags if tag not in standards.citations
    )


def find_unknown_annex_b_ids(standards):
    """Annex B "SEMLOG contributes" ids that are never declared (CMA-004)."""
    return sorted(standards.annex_b_ids - set(standards.declarations))


def find_backing_issues(standards):
    """Cross-check every declared id's Backing cell against its span
    (design #223 P3-ADR-4: TRC-003, TRC-004, DOC-003)."""
    malformed_backing = []
    missing_rationale = []
    misplaced_rationale = []
    rationale_on_external = []
    undefined_tag = []
    tag_not_in_span = []

    for req_id, backing in standards.annex_a.items():
        if req_id not in standards.declarations:
            continue
        if not BACKING_RE.match(backing):
            malformed_backing.append(req_id)
            continue
        start, end = standards.spans[req_id]
        if backing == "Design decision":
            line_no = standards.rationale.get(req_id)
            if line_no is None:
                missing_rationale.append(req_id)
            elif not (start <= line_no <= end):
                misplaced_rationale.append(req_id)
        else:
            if req_id in standards.rationale:
                rationale_on_external.append(req_id)
            for tag in CITE_RE.findall(backing):
                if tag not in standards.section12_tags:
                    undefined_tag.append(f"{req_id}:{tag}")
                elif not any(
                    start <= ln <= end for ln in standards.citations.get(tag, [])
                ):
                    tag_not_in_span.append(f"{req_id}:{tag}")

    return SimpleNamespace(
        malformed_backing=sorted(malformed_backing),
        missing_rationale=sorted(missing_rationale),
        misplaced_rationale=sorted(misplaced_rationale),
        rationale_on_external=sorted(rationale_on_external),
        undefined_tag=sorted(undefined_tag),
        tag_not_in_span=sorted(tag_not_in_span),
    )


class StandardsTraceabilityTests(unittest.TestCase):
    """Runs the checker against the real STANDARDS.md and tests/ tree."""

    @classmethod
    def setUpClass(cls):
        cls.standards = parse_standards(STANDARDS_PATH.read_text(encoding="utf-8"))
        cls.proves = collect_proves(TESTS_DIR)

    def test_ids_well_formed_unique_and_prefixed(self):
        """Proves: TRC-001"""
        s = self.standards
        self.assertEqual(
            [], sorted(s.malformed_declarations), "malformed declaration line(s)"
        )
        self.assertEqual(
            [], sorted(s.duplicate_declarations), "duplicate declaration for"
        )
        self.assertEqual(
            [],
            sorted(s.prefix_duplicates),
            "duplicate prefix/capability in the prefix table",
        )
        missing_prefix = sorted(
            req_id
            for req_id in s.declarations
            if req_id.split("-")[0] not in s.prefixes
        )
        self.assertEqual([], missing_prefix, "declared id with no prefix-table entry")

    def test_test_citations_well_formed_and_known(self):
        """Proves: TRC-002"""
        self.assertEqual([], sorted(self.proves.malformed), "malformed Proves: line")
        unknown = find_unknown_proves(set(self.standards.declarations), self.proves)
        self.assertEqual([], unknown, "unknown id cited by a Proves: line")

    def test_every_requirement_proven(self):
        """Proves: TRC-003

        Unconditional since task 5.2: every declared requirement must be
        proven by a citing test, with no allowlist exception and
        regardless of `GITHUB_REF` -- a release-tag build is simply one
        more run of this same, always-enforced assertion."""
        declared = set(self.standards.declarations)
        uncovered = find_uncovered(declared, self.proves)
        self.assertEqual([], uncovered, "untested: no citing test for")

    def test_backing_present_typed_and_consistent(self):
        """Proves: TRC-003, TRC-004, DOC-003"""
        s = self.standards
        declared = set(s.declarations)
        self.assertEqual(
            [], sorted(declared - set(s.annex_a)), "declared id with no Annex A row"
        )
        self.assertEqual(
            [], sorted(set(s.annex_a) - declared), "Annex A row for an undeclared id"
        )
        self.assertEqual([], sorted(s.annex_a_duplicates), "duplicate Annex A row for")
        self.assertEqual([], sorted(s.malformed_rationale), "malformed rationale line")

        issues = find_backing_issues(s)
        self.assertEqual([], issues.malformed_backing, "malformed Backing cell for")
        self.assertEqual(
            [], issues.missing_rationale, "design-decision rule with no rationale line"
        )
        self.assertEqual(
            [], issues.misplaced_rationale, "rationale line outside its rule's span"
        )
        self.assertEqual(
            [],
            issues.rationale_on_external,
            "rationale line attached to an external rule",
        )
        self.assertEqual(
            [], issues.undefined_tag, "Backing cites an undefined section-12 tag"
        )
        self.assertEqual(
            [],
            issues.tag_not_in_span,
            "Backing tag never cited in its own rule's span",
        )

    def test_references_defined_and_cited(self):
        """Proves: DOC-003"""
        s = self.standards
        self.assertEqual(
            [], sorted(s.section12_tag_duplicates), "duplicate reference definition for"
        )
        self.assertEqual(
            [], find_unresolved_citations(s), "citation with no section-12 definition"
        )
        self.assertEqual(
            [], find_orphan_references(s), "reference defined but never cited"
        )

    def test_annex_b_claims_only_declared_ids(self):
        """Proves: CMA-004"""
        s = self.standards
        self.assertTrue(
            s.annex_a_header_ok, "Annex A header does not match the required text"
        )
        self.assertTrue(
            s.annex_b_header_ok, "Annex B header does not match the required text"
        )
        unknown = find_unknown_annex_b_ids(s)
        self.assertEqual([], unknown, "Annex B claims an undeclared id")


class TraceabilityCheckerFixtureTests(unittest.TestCase):
    """Fixture self-tests over crafted strings (design #223 §2, task 2.1)."""

    def test_unknown_id_is_reported(self):
        with _fixture_tests_dir('def test_a():\n    """Proves: ZZZ-999"""\n') as root:
            proves = collect_proves(root)
        standards = parse_standards(_minimal_standards())
        unknown = find_unknown_proves(set(standards.declarations), proves)
        self.assertEqual(["ZZZ-999"], unknown)

    def test_untested_id_is_reported(self):
        standards = parse_standards(_minimal_standards())
        with _fixture_tests_dir("") as root:
            proves = collect_proves(root)
        uncovered = find_uncovered(set(standards.declarations), proves)
        self.assertEqual(["FIX-001"], uncovered)

    def test_missing_rationale_is_reported(self):
        text = (
            "### 1.1 Identifiers\n\n"
            "| Prefix | Capability |\n|---|---|\n| FIX | Fixture capability |\n\n"
            "**FIX-002**: A design-decision rule with no rationale line.\n\n"
            "## 12. Normative and informative references\n\n### Normative\n\n"
            "## 13. License\n\n"
            "## Annex A: requirement traceability\n\n"
            "| Requirement | Section | Backing | Planned test |\n|---|---|---|---|\n"
            "| FIX-002 | 1.1 | Design decision | n/a |\n"
        )
        standards = parse_standards(text)
        issues = find_backing_issues(standards)
        self.assertEqual(["FIX-002"], issues.missing_rationale)

    def test_malformed_proves_line_is_reported(self):
        with _fixture_tests_dir(
            'def test_a():\n    """proves: FIX-001"""\n    pass\n'
        ) as root:
            proves = collect_proves(root)
        self.assertEqual(1, len(proves.malformed))

    def test_orphan_reference_is_reported(self):
        text = (
            "**FIX-001**: A rule citing nothing from section 12.\n\n"
            "## 12. Normative and informative references\n\n### Normative\n\n"
            "- [ORPHANTAG] A reference nobody cites. https://example.invalid/orphan\n\n"
            "## 13. License\n\n"
            "## Annex A: requirement traceability\n\n"
            "| Requirement | Section | Backing | Planned test |\n|---|---|---|---|\n"
            "| FIX-001 | 1 | Design decision | n/a |\n"
        )
        standards = parse_standards(text)
        self.assertEqual(["ORPHANTAG"], find_orphan_references(standards))

    def test_declaration_without_the_colon_separator_is_malformed(self):
        text = (
            "**FIX-001** \u2014 The previous em dash separator.\n\n"
            "**FIX-002**:No space after the colon.\n\n"
            "**FIX-003**: A well-formed declaration.\n"
        )
        standards = parse_standards(text)
        self.assertEqual(["FIX-003"], sorted(standards.declarations))
        self.assertEqual([1, 3], standards.malformed_declarations)

    def test_code_span_is_not_a_citation(self):
        line = "The grammar is `[A-Z]{2,4}-[0-9]{3}` and is not a citation."
        self.assertEqual([], find_citations(line))

    def test_import_raising_test_file_survives_the_scan(self):
        with _fixture_tests_dir(
            'raise RuntimeError("boom at import time")\n\n'
            "def test_never_actually_runs():\n"
            '    """Proves: FIX-003"""\n'
            "    pass\n"
        ) as root:
            proves = collect_proves(root)
        self.assertIn("FIX-003", proves.cited)


def _minimal_standards():
    return (
        "### 1.1 Identifiers\n\n"
        "| Prefix | Capability |\n|---|---|\n| FIX | Fixture capability |\n\n"
        "**FIX-001**: An externally backed fixture rule ([FIXTAG]).\n\n"
        "## 12. Normative and informative references\n\n### Normative\n\n"
        "- [FIXTAG] A fixture reference. https://example.invalid/fixtag\n\n"
        "## 13. License\n\n"
        "## Annex A: requirement traceability\n\n"
        "| Requirement | Section | Backing | Planned test |\n|---|---|---|---|\n"
        "| FIX-001 | 1.1 | External: [FIXTAG] | n/a |\n"
    )


def _fixture_tests_dir(source):
    """Context manager: a temp dir with one ``test_fixture.py`` file."""
    return _TempTestFile(source)


class _TempTestFile:
    def __init__(self, source):
        self._source = source
        self._tmpdir = None

    def __enter__(self):
        import tempfile

        self._tmpdir = tempfile.TemporaryDirectory()
        root = Path(self._tmpdir.name)
        (root / "test_fixture.py").write_text(self._source, encoding="utf-8")
        return root

    def __exit__(self, exc_type, exc, tb):
        self._tmpdir.cleanup()
        return False


if __name__ == "__main__":
    unittest.main()
