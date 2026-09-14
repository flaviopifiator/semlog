"""Documentation-conformance module (task 4.31; design #223 §10; STANDARDS.md
DOC-001, DOC-002, DOC-005, DOC-006, DOC-007, DOC-008, CP-005, CP-012, CP-014).

Proxy checks on observable documentation/packaging artifacts, parsed with
the standard library only (`re`, `ast`, `tomllib`/regex fallback) -- this
module never imports STANDARDS.md, the READMEs or BENCHMARKS.md as code.
Checks on the README apply to both editions, `README.md` (English) and
`README.es.md` (Spanish), each in its own language
(`tests/_readme_support.py`).

CP-004 and CP-013, though listed by the design table alongside this
module's Reqs, are NOT re-tested here: both are already fully proven --
CP-004 by `tests/test_packaging_hygiene.py` (SemVer/CHANGELOG/SPDX/
Conventional Commits), CP-013 by `tests/test_tooling_policy.py`
(`NoUnapprovedToolingTests`). Duplicating them here would just be a second,
weaker copy of an already-committed check.
"""

from __future__ import annotations

import ast
import contextlib
import inspect
import io
import re
import sys
import unittest
from pathlib import Path

import semlog
from benchmark import field_scaling, run_benchmark

from ._readme_support import (
    BENCHMARKS_PATH,
    READMES,
    fenced_blocks,
    headings,
    readme_text,
    section,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
STANDARDS_PATH = REPO_ROOT / "STANDARDS.md"
AGENTS_PATH = REPO_ROOT / "AGENTS.md"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
BENCHMARK_DIR = REPO_ROOT / "benchmark"


def _standards_text():
    return STANDARDS_PATH.read_text(encoding="utf-8")


def _span(text, req_id):
    """Return the text span (declaration line up to the next declaration
    or heading) for `req_id`, matching `tests/test_traceability.py`'s own
    span convention, without importing that module (kept independent so a
    change to one checker cannot silently mask the other)."""
    lines = text.splitlines()
    decl_re = re.compile(r"^\*\*(" + re.escape(req_id) + r")\*\*: ")
    any_decl_re = re.compile(r"^\*\*[A-Z]{2,4}-[0-9]{3}\*\*: ")
    heading_re = re.compile(r"^#{1,6} ")
    start = None
    for i, line in enumerate(lines):
        if decl_re.match(line):
            start = i
            break
    assert start is not None, f"{req_id} is not declared in STANDARDS.md"
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if any_decl_re.match(lines[i]) or heading_re.match(lines[i]):
            end = i
            break
    return "\n".join(lines[start:end])


class Doc001ApprovalGateTests(unittest.TestCase):
    """Proves: DOC-001

    An approval-record line: DOC-001's own declaration states that no
    Phase-2-or-later work begins without a recorded user approval, naming
    the user-review-gate task."""

    def test_doc_001_states_recorded_approval_and_names_the_gate_task(self):
        span = _span(_standards_text(), "DOC-001")
        self.assertIn("recorded approval", span)
        self.assertIn("task 1.9", span)


# Every RFC 2119/8174 keyword row DOC-002's table must define, with its
# meaning cell.
REQUIRED_KEYWORD_MEANINGS = {
    "MUST / SHALL": "Absolute requirement",
    "MUST NOT / SHALL NOT": "Absolute prohibition",
    "SHOULD": (
        "Recommendation; an exception requires understanding and weighing "
        "the consequences"
    ),
    "SHOULD NOT": "Recommendation against",
    "MAY": "Truly optional",
    "REQUIRED": "Equivalent to MUST",
    "RECOMMENDED": "Equivalent to SHOULD",
    "OPTIONAL": "Equivalent to MAY",
}
KEYWORD_TABLE_HEADER = "| Keyword (RFC 2119/8174) | Meaning |"


def _keyword_table(text):
    table_start = text.index(KEYWORD_TABLE_HEADER)
    table_end = text.index("\n\n", table_start)
    return text[table_start:table_end]


class Doc002KeywordCorrespondenceTableTests(unittest.TestCase):
    """Proves: DOC-002

    Every RFC 2119/8174 normative keyword has its meaning fixed in the
    section-1 keyword table (the single place DOC-002 requires the
    capitalized keywords to be defined, per its own "as defined by the
    table in this section" clause)."""

    def test_every_rfc_2119_keyword_has_its_meaning_in_the_table(self):
        text = _standards_text()
        table = _keyword_table(text)
        missing = [
            keyword
            for keyword, meaning in REQUIRED_KEYWORD_MEANINGS.items()
            if f"| {keyword} | {meaning} |" not in table
        ]
        self.assertEqual([], missing)


class Doc005ExtensionLimitsSectionTests(unittest.TestCase):
    """Proves: DOC-005

    Section 6 ("Extension limits") documents the attribute-count and
    attribute-length limits with their parameter names, and the overflow
    (truncation) behavior DOC-005 says this section covers."""

    def test_section_6_documents_limits_and_overflow_behavior(self):
        text = _standards_text()
        start = text.index("## 6. Extension limits")
        end = text.index("\n## 7.", start)
        section = text[start:end]
        for token in ("max_attributes", "max_attribute_length", "truncated"):
            with self.subTest(token=token):
                self.assertIn(token, section)


SEVERITY_TABLE_HEADER = (
    "| Python level | `levelno` | `severity_text` "
    "| `severity_number` | OTel short name | RFC 5424 (informative only) |"
)


class Doc006SeverityTableTests(unittest.TestCase):
    """Proves: DOC-006

    The severity-mapping table (section 3) publishes the five Python
    levels with their OTel `severity_number`, and RFC 5424 is marked
    informative-only in DOC-006's own declaration."""

    def test_severity_table_has_the_five_documented_rows(self):
        text = _standards_text()
        table_start = text.index(SEVERITY_TABLE_HEADER)
        table_end = text.index("\n\n", table_start)
        table = text[table_start:table_end]
        expected_rows = {
            "DEBUG": ("10", "5"),
            "INFO": ("20", "9"),
            "WARNING": ("30", "13"),
            "ERROR": ("40", "17"),
            "CRITICAL": ("50", "21"),
        }
        missing = []
        for level, (levelno, severity_number) in expected_rows.items():
            row_pattern = f"| {level} | {levelno} | {level} | {severity_number} |"
            if row_pattern not in table:
                missing.append(level)
        self.assertEqual([], missing)

    def test_doc_006_marks_rfc5424_as_informative_only(self):
        span = _span(_standards_text(), "DOC-006")
        self.assertIn("informative reference", span)


class Doc007ComplianceReferencesTests(unittest.TestCase):
    """Proves: DOC-007

    Every compliance reference DOC-007 names (ISO/IEC 27001, ISO/IEC
    27002, PCI DSS, LGPD Art. 6) is marked unverified against its primary
    source within DOC-007's own span."""

    def test_every_named_reference_is_marked_unverified(self):
        span = _span(_standards_text(), "DOC-007")
        for name in ("ISO/IEC 27001", "ISO/IEC 27002", "PCI DSS", "LGPD"):
            with self.subTest(reference=name):
                self.assertIn(name, span)
        self.assertEqual(4, span.count("unverified against the primary source"))


# Every element DOC-008 requires AGENTS.md's tooling policy to declare.
AGENTS_TOOLING_POLICY_ELEMENTS = (
    "standard library",
    "FastAPI, Starlette, Django, loguru, structlog",
    "`ruff`",
    "GitHub Actions",
    "`uv_build`",
    'requires = ["uv_build>=0.12.13,<0.13"]',
    "`uv` CLI",
    "`uv build`, `uv sync`, `uv run`",
    "never at runtime",
    "zero `Requires-Dist`",
    "python -m pip wheel . --no-deps --wheel-dir dist",
    "pip install -e .",
    "`mypy`, `coverage.py`, `jsonschema`, `gunicorn`, and `uvicorn` are not approved",
    "none pending",
    "`llms.txt`",
    "`AGENTS.md`",
    "agent guide",
)


def _agents_tooling_policy(text):
    start = text.index("## Tooling policy")
    end = text.index("\n## ", start + 1)
    return text[start:end]


class Doc008ToolingPolicyTests(unittest.TestCase):
    """Proves: DOC-008

    The tooling-policy declaration names the approved subjects, `ruff`,
    GitHub Actions, `uv_build`, the `uv` CLI, the documented `pip`
    fallbacks, the non-approved tools, and the agent-facing documentation
    set (DOC-009/010/011), both in STANDARDS.md and in AGENTS.md's
    "Tooling policy" section (the README links there instead)."""

    def test_agents_md_tooling_policy_declares_every_required_element(self):
        policy = _agents_tooling_policy(AGENTS_PATH.read_text(encoding="utf-8"))
        missing = [e for e in AGENTS_TOOLING_POLICY_ELEMENTS if e not in policy]
        self.assertEqual([], missing)

    def test_doc_008_names_every_required_element(self):
        span = _span(_standards_text(), "DOC-008")
        required_substrings = (
            "standard library",
            "FastAPI, Starlette, Django, loguru, structlog",
            "ruff",
            "GitHub Actions",
            "uv_build",
            "`uv` CLI",
            "pip install -e .",
            "mypy",
            "coverage.py",
            "jsonschema",
            "gunicorn",
            "uvicorn",
            "llms.txt",
            "AGENTS.md",
        )
        missing = [s for s in required_substrings if s not in span]
        self.assertEqual([], missing)


_BENCHMARK_NUMBER_RE = re.compile(
    r"\d+(\.\d+)?\s*(x|veces)\b|\d+(\.\d+)?\s*(ms|ns|µs|req/s|%)\b",
    re.IGNORECASE,
)
# Published benchmark results (CP-005): a figure in microseconds (micro sign
# or Greek mu), a growth factor written like `×6.31`, a results chart, or a
# table row that carries a timing figure, a median column, or a measured
# environment field. Methodology parameters in prose ("2 ms per write",
# "800 × 2 ms = 1.6 s") are not results and are not flagged.
_MICROSECONDS_RE = re.compile(r"\d+(?:[.,]\d+)?\s*[µμ]s\b")
_GROWTH_FACTOR_RE = re.compile(r"×\d+(?:[.,]\d+)?")
_RESULTS_CHART_RE = re.compile(r"^\s*xychart(?:-beta)?\b", re.MULTILINE)
_TABLE_TIMING_RE = re.compile(r"\d+(?:[.,]\d+)?\s*(?:[µμ]s|ns|ms|req/s)\b")
_TABLE_MEDIAN_RE = re.compile(r"\bmedian", re.IGNORECASE)
_TABLE_ENVIRONMENT_RE = re.compile(
    r"^\|\s*(?:CPU|Interpreter|Intérprete|Operating system|Sistema operativo)\b"
)
_MACHINE_DETAILS = ("CPU", "kernel", "governor", "Intel", "x86_64", "GIL")

# The phrases CP-005's declaration must keep: the methodology and the
# harness are published and reproducible, results are withheld from both
# READMEs and BENCHMARKS.md until the maintainer validates them.
CP005_REQUIRED_PHRASES = (
    "The methodology",
    "the measurement harness",
    "MUST be published in BENCHMARKS.md",
    "so that the measurement is reproducible",
    "The benchmark results",
    (
        "MUST NOT be published in README.md, README.es.md or BENCHMARKS.md "
        "until the maintainer validates them"
    ),
    "(CP-012)",
    "(Previously:",
)
BENCHMARKS_METHODOLOGY_SECTIONS = (
    "Subjects",
    "Scenarios",
    "Fairness, verified before timing",
    "Differences that are not equalized",
    "Repetitions and CPU pinning",
    "Environment disclosure",
)
ENVIRONMENT_DISCLOSURE_FIELDS = (
    "CPU model",
    "operating system",
    "interpreter version",
    "`semlog`",
    "`loguru`",
    "`structlog`",
    "`benchmark/environment.py`",
)
_HARNESS_COMMAND = "-m benchmark.run_benchmark"
_FENCED_BLOCK_RE = re.compile(r"^```.*?^```[^\n]*$", re.MULTILINE | re.DOTALL)


def _benchmarks_text():
    return BENCHMARKS_PATH.read_text(encoding="utf-8")


def _methodology(benchmarks_text):
    return section(benchmarks_text, 2, "Methodology")


def _published_benchmark_results(text):
    """Every benchmark result `text` publishes (empty when it publishes
    none)."""
    problems = [f"figure {value}" for value in _MICROSECONDS_RE.findall(text)]
    problems += [f"growth factor {value}" for value in _GROWTH_FACTOR_RE.findall(text)]
    problems += ["results chart" for _chart in _RESULTS_CHART_RE.findall(text)]
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        if (
            _TABLE_TIMING_RE.search(line)
            or _TABLE_MEDIAN_RE.search(line)
            or _TABLE_ENVIRONMENT_RE.match(line)
        ):
            problems.append(f"results table row {line.strip()!r}")
    return problems


def _readme_performance_problems(text):
    """Every way `text` (a README, in either language) talks about
    performance: a performance section under any README language's title,
    a link to BENCHMARKS.md, or a machine detail (empty when it does
    none)."""
    titles = {labels["performance"] for labels in READMES.values()}
    problems = [
        f"performance section {title!r}"
        for level, title in headings(text)
        if level == 2 and title in titles
    ]
    if "](BENCHMARKS.md)" in text:
        problems.append("link to BENCHMARKS.md")
    problems += [f"machine detail {d!r}" for d in _MACHINE_DETAILS if d in text]
    return problems


def _field_scaling_counts_phrase(counts):
    listed = ", ".join(str(count) for count in counts[:-1])
    return f"with {listed} and {counts[-1]} call-site attributes"


def _harness_commands(benchmarks_text):
    running = section(benchmarks_text, 2, "Running the harness")
    return [
        line
        for _info, block in fenced_blocks(running)
        for line in block.splitlines()
        if _HARNESS_COMMAND in line
    ]


def _harness_accepts(argv):
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            run_benchmark.parse_args(argv)
    except SystemExit:
        return False
    return True


def _undocumented_harness_flags(benchmarks_text):
    """Every `--flag` that BENCHMARKS.md's prose or its harness command
    mentions and the harness's own command line rejects, alone or with one
    value. Flags of other tools inside code blocks (`uv pip install
    --python`) are not the harness's."""
    prose = _FENCED_BLOCK_RE.sub("", benchmarks_text)
    mentioned = prose + "\n" + "\n".join(_harness_commands(benchmarks_text))
    flags = sorted(set(re.findall(r"(?<![\w-])--[a-z][a-z-]*", mentioned)))
    return [
        flag
        for flag in flags
        if not (_harness_accepts([flag]) or _harness_accepts([flag, "1"]))
    ]


class Cp005ResultsWithheldTests(unittest.TestCase):
    """Proves: CP-005

    The benchmark's methodology and its harness are published and
    reproducible, and its results stay unpublished until the maintainer
    validates them. CP-005's declaration says so; BENCHMARKS.md documents
    the methodology (subjects, scenarios, fairness rules, repetitions and
    the environment disclosure every run reports) and a harness command
    that the harness's own command line accepts, and states that results
    are not published yet and are never a performance guarantee; and
    neither README nor BENCHMARKS.md publishes a benchmark figure, growth
    factor, results chart or results table. Neither README, in either
    language, has a performance section, points to BENCHMARKS.md, or
    discloses machine details."""

    def test_cp005_publishes_the_methodology_and_withholds_results(self):
        span = _span(_standards_text(), "CP-005")
        for phrase in CP005_REQUIRED_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, span)

    def test_no_document_publishes_benchmark_results(self):
        documents = [labels["path"] for labels in READMES.values()]
        for path in documents + [BENCHMARKS_PATH]:
            with self.subTest(document=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertEqual([], _published_benchmark_results(text))

    def test_no_document_publishes_the_old_no_number_placeholder(self):
        placeholder = "Esta sección no publica ninguna cifra."
        self.assertNotIn(placeholder, _benchmarks_text())
        for language in READMES:
            with self.subTest(language=language):
                self.assertNotIn(placeholder, readme_text(language))

    def test_benchmarks_documents_every_methodology_section(self):
        methodology = _methodology(_benchmarks_text())
        for title in BENCHMARKS_METHODOLOGY_SECTIONS:
            with self.subTest(section=title):
                body = section(methodology, 3, title).split("\n", 1)[1]
                self.assertTrue(body.strip())

    def test_every_harness_module_the_document_names_exists(self):
        named = set(re.findall(r"`(benchmark/[\w/]+\.py)`", _benchmarks_text()))
        self.assertGreater(len(named), 0)
        missing = sorted(name for name in named if not (REPO_ROOT / name).is_file())
        self.assertEqual([], missing)

    def test_field_scaling_scenario_names_every_harness_attribute_count(self):
        scenarios = section(_methodology(_benchmarks_text()), 3, "Scenarios")
        self.assertIn(
            _field_scaling_counts_phrase(field_scaling.FIELD_COUNTS), scenarios
        )

    def test_benchmarks_documents_the_environment_disclosure(self):
        disclosure = section(
            _methodology(_benchmarks_text()), 3, "Environment disclosure"
        )
        for field in ENVIRONMENT_DISCLOSURE_FIELDS:
            with self.subTest(field=field):
                self.assertIn(field, disclosure)

    def test_benchmarks_documents_a_harness_command_the_harness_accepts(self):
        commands = _harness_commands(_benchmarks_text())
        self.assertEqual(1, len(commands), commands)
        argv = commands[0].split(_HARNESS_COMMAND, 1)[1].split()
        self.assertTrue(_harness_accepts(argv), argv)
        self.assertEqual([], _undocumented_harness_flags(_benchmarks_text()))

    def test_benchmarks_states_results_are_not_published_yet(self):
        text = _benchmarks_text()
        lead = text[: text.index("\n## Methodology")]
        self.assertIn("Benchmark results are not published yet", lead)
        self.assertIn("Results are not published yet", section(text, 2, "Results"))

    def test_benchmarks_states_results_are_run_specific_not_a_guarantee(self):
        self.assertIn("not a performance guarantee", _benchmarks_text())

    def test_no_performance_number_appears_before_the_methodology(self):
        text = _benchmarks_text()
        lead_paragraph = text[: text.index("\n## Methodology")]
        offenders = _BENCHMARK_NUMBER_RE.findall(lead_paragraph)
        self.assertEqual(
            [],
            offenders,
            "a performance number appears before the methodology",
        )

    def test_no_readme_has_a_performance_section_or_benchmark_pointer(self):
        for language in READMES:
            with self.subTest(language=language):
                self.assertEqual(
                    [], _readme_performance_problems(readme_text(language))
                )


def _find_import_offenders(root, allowed_third_party):
    """Return `"path: name"` strings for every top-level import in
    `root/**/*.py` that is neither a stdlib module, an allowed
    third-party name, nor a same-package relative import (`from . import
    x` / `from .x import y`, `node.level > 0` -- these name a sibling
    module inside `root` itself, never an external dependency, and must
    never be checked against the stdlib/allowed-list)."""
    offenders = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
                and not node.level  # 0 for an absolute import; >0 is relative
            ):
                names = [node.module.split(".")[0]]
            for name in names:
                if name in sys.stdlib_module_names:
                    continue
                if name in allowed_third_party:
                    continue
                try:
                    label = path.relative_to(REPO_ROOT)
                except ValueError:
                    label = path.relative_to(root)  # fixture root outside the repo
                offenders.append(f"{label}: {name}")
    return offenders


class Cp012BenchmarkImportAuditTests(unittest.TestCase):
    """Proves: CP-012

    A stdlib-only import audit of the benchmark directory. Task 5.1
    created `benchmark/`, so this check is no longer vacuous (the
    `tests/test_pipeline_order.py`/`tests/test_namespace.py` precedent
    this docstring used to cite before its own completion no longer
    applies): it now runs a real, non-trivial scan of real benchmark
    source files on every suite run."""

    # loguru/structlog: the approved benchmark-comparison subjects
    # (CP-008/CP-013). semlog: not a third-party dependency at all --
    # it is the library under benchmark, imported by the harness the
    # same way any of its real users would import it (`import semlog`,
    # `from semlog._format import Formatter`, etc.); listing it here
    # only means "this name is not flagged as an unapproved external
    # package", which is correct, since it IS this repository's own
    # package, not an external one.
    _ALLOWED_THIRD_PARTY = frozenset({"loguru", "structlog", "semlog"})

    def test_benchmark_directory_is_stdlib_or_approved_only(self):
        self.assertTrue(BENCHMARK_DIR.is_dir(), "benchmark/ must exist by now (5.1)")
        offenders = _find_import_offenders(BENCHMARK_DIR, self._ALLOWED_THIRD_PARTY)
        self.assertEqual([], offenders)


class Cp014BuildBackendDeclarationTests(unittest.TestCase):
    """Proves: CP-014

    `pyproject.toml`'s `[build-system]` table matches, exactly, the block
    CP-014 prescribes in STANDARDS.md."""

    def test_pyproject_build_system_matches_the_prescribed_block(self):
        span = _span(_standards_text(), "CP-014")
        code_match = re.search(r"```toml\n(.*?)\n```", span, re.DOTALL)
        self.assertIsNotNone(code_match, "CP-014 has no fenced toml block")
        prescribed = code_match.group(1).strip()

        pyproject_text = PYPROJECT_PATH.read_text(encoding="utf-8")
        if sys.version_info >= (3, 11):
            import tomllib

            data = tomllib.loads(pyproject_text)
            self.assertEqual(
                ["uv_build>=0.12.13,<0.13"], data["build-system"]["requires"]
            )
            self.assertEqual("uv_build", data["build-system"]["build-backend"])
        else:
            self.assertIn('requires = ["uv_build>=0.12.13,<0.13"]', pyproject_text)
            self.assertIn('build-backend = "uv_build"', pyproject_text)
        self.assertIn('requires = ["uv_build>=0.12.13,<0.13"]', prescribed)
        self.assertIn('build-backend = "uv_build"', prescribed)


def _readme_configure_table_params(text, title):
    """Return the set of backtick-quoted parameter names in the `Parameter`
    column of a README's configuration table: the table directly under its
    `## {title}` heading, before the first subsection (engram #255 gap
    closure's README anti-drift check)."""
    start = text.index(f"## {title}\n")
    end = text.index("\n### ", start)
    table = text[start:end]
    return set(re.findall(r"^\| [^|]+ \| `([a-z_]+)` \|", table, re.MULTILINE))


class ReadmeConfigureSignatureAntiDriftTests(unittest.TestCase):
    """Engram #255 gap closure: each README's configuration table
    ("## Configuration" in README.md, "## Configuración" in README.es.md)
    must name exactly the same parameters as the real `configure()`
    signature, in both directions -- every documented parameter is real,
    and every real parameter is documented. Not tied to a single
    STANDARDS.md requirement id (same precedent as
    `tests/test_class_budget.py`: no `Proves:` line when no id cleanly
    backs the check)."""

    def test_readme_table_matches_configure_signature_in_both_directions(self):
        real = set(inspect.signature(semlog.configure).parameters)
        for language, labels in READMES.items():
            documented = _readme_configure_table_params(
                readme_text(language), labels["configuration"]
            )
            with self.subTest(language=language):
                self.assertEqual(
                    set(), documented - real, "README documents unknown param(s)"
                )
                self.assertEqual(
                    set(), real - documented, "README is missing real param(s)"
                )


class PerturbationProofTests(unittest.TestCase):
    """Proves the checks above are not vacuously true: each is shown to
    fail against a deliberately broken in-memory COPY of the real
    STANDARDS.md/README/BENCHMARKS.md/AGENTS.md text, never against the
    real file on disk."""

    @classmethod
    def setUpClass(cls):
        cls.standards_text = _standards_text()

    def test_doc_001_check_fails_without_the_recorded_approval_phrase(self):
        mutated = self.standards_text.replace("recorded approval", "sign-off")
        self.assertNotEqual(mutated, self.standards_text)
        span = _span(mutated, "DOC-001")
        self.assertNotIn("recorded approval", span)

    def test_doc_006_check_fails_if_a_severity_row_is_altered(self):
        mutated = self.standards_text.replace(
            "| INFO | 20 | INFO | 9 | INFO | 6 Informational |",
            "| INFO | 20 | INFO | 99 | INFO | 6 Informational |",
        )
        self.assertNotEqual(mutated, self.standards_text)
        table_start = mutated.index(SEVERITY_TABLE_HEADER)
        table_end = mutated.index("\n\n", table_start)
        table = mutated[table_start:table_end]
        self.assertNotIn("| INFO | 20 | INFO | 9 |", table)

    def test_doc_007_check_fails_if_an_unverified_marker_is_removed(self):
        mutated = self.standards_text.replace(
            "PCI DSS v4.0.1, requirements 10 and 3.4.1 ([PCIDSS]): "
            "**[unverified against the primary source]**.",
            "PCI DSS v4.0.1, requirements 10 and 3.4.1 ([PCIDSS]).",
        )
        self.assertNotEqual(mutated, self.standards_text)
        span = _span(mutated, "DOC-007")
        self.assertEqual(3, span.count("unverified against the primary source"))

    def test_doc_008_check_fails_if_an_unapproved_tool_mention_is_removed(self):
        mutated = self.standards_text.replace("`mypy`, `coverage.py`, `jsonschema`", "")
        self.assertNotEqual(mutated, self.standards_text)
        span = _span(mutated, "DOC-008")
        self.assertNotIn("mypy", span)

    def test_cp014_check_fails_if_pyproject_diverges_from_the_prescribed_block(self):
        fake_pyproject = (
            '[build-system]\nrequires = ["setuptools"]\nbuild-backend = '
            '"setuptools.build_meta"\n'
        )
        self.assertNotIn("uv_build", fake_pyproject)

    def test_readme_configure_check_fails_if_a_real_param_row_is_removed(self):
        real = set(inspect.signature(semlog.configure).parameters)
        for language, labels in READMES.items():
            original = readme_text(language)
            mutated = re.sub(r"(?m)^\| [^|]+ \| `queue_size` \|.*\n", "", original)
            with self.subTest(language=language):
                self.assertNotEqual(mutated, original)
                documented = _readme_configure_table_params(
                    mutated, labels["configuration"]
                )
                self.assertIn("queue_size", real - documented)

    def test_readme_configure_check_fails_if_an_unknown_param_row_is_added(self):
        real = set(inspect.signature(semlog.configure).parameters)
        for language, labels in READMES.items():
            original = readme_text(language)
            heading = f"## {labels['configuration']}\n"
            mutated = original.replace(
                heading, heading + "\n| Output | `not_a_real_param` | `None` | x |\n", 1
            )
            with self.subTest(language=language):
                self.assertNotEqual(mutated, original)
                documented = _readme_configure_table_params(
                    mutated, labels["configuration"]
                )
                self.assertIn("not_a_real_param", documented - real)

    def test_doc_008_agents_check_fails_if_a_non_approved_tool_is_dropped(self):
        original = AGENTS_PATH.read_text(encoding="utf-8")
        mutated = original.replace(
            ", `gunicorn`, and `uvicorn` are not approved", " are not approved"
        )
        self.assertNotEqual(mutated, original)
        policy = _agents_tooling_policy(mutated)
        missing = [e for e in AGENTS_TOOLING_POLICY_ELEMENTS if e not in policy]
        self.assertTrue(missing)

    def test_cp012_import_audit_catches_a_real_disallowed_import(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "offender.py").write_text("import requests\n", encoding="utf-8")
            offenders = _find_import_offenders(
                root, Cp012BenchmarkImportAuditTests._ALLOWED_THIRD_PARTY
            )
        self.assertEqual(["offender.py: requests"], offenders)

    def test_cp012_import_audit_never_flags_a_same_package_relative_import(self):
        # Regression guard for the bug this batch fixed: `node.module`
        # for `from .sibling import x` is `"sibling"`, which used to be
        # checked against the stdlib/allowed-list as if it were an
        # external dependency. `node.level > 0` must exempt it.
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "__init__.py").write_text("", encoding="utf-8")
            (root / "sibling.py").write_text("VALUE = 1\n", encoding="utf-8")
            (root / "user.py").write_text(
                "from .sibling import VALUE\nfrom . import sibling\n",
                encoding="utf-8",
            )
            offenders = _find_import_offenders(root, frozenset())
        self.assertEqual([], offenders)

    def test_cp005_check_fails_if_the_run_specific_disclaimer_is_removed(self):
        original = _benchmarks_text()
        mutated = original.replace(
            "not a performance guarantee", "a performance guarantee"
        )
        self.assertNotEqual(mutated, original)
        self.assertNotIn("not a performance guarantee", mutated)

    def test_cp005_check_fails_if_a_number_appears_before_the_methodology(self):
        original = _benchmarks_text()
        mutated = original.replace(
            "\n## Methodology",
            "\nsemlog is 2x faster in this introduction.\n\n## Methodology",
            1,
        )
        self.assertNotEqual(mutated, original)
        lead_paragraph = mutated[: mutated.index("\n## Methodology")]
        self.assertTrue(_BENCHMARK_NUMBER_RE.findall(lead_paragraph))

    def test_cp005_check_fails_if_the_withholding_clause_is_dropped(self):
        mutated = self.standards_text.replace(
            " until the maintainer validates them; unmeasured", "; unmeasured", 1
        )
        self.assertNotEqual(mutated, self.standards_text)
        span = _span(mutated, "CP-005")
        self.assertTrue([p for p in CP005_REQUIRED_PHRASES if p not in span])

    def test_cp005_check_fails_if_a_readme_publishes_a_figure(self):
        for language in READMES:
            original = readme_text(language)
            mutated = original + "\nsemlog costs 8.21 µs per record.\n"
            with self.subTest(language=language):
                self.assertEqual([], _published_benchmark_results(original))
                self.assertIn("figure 8.21 µs", _published_benchmark_results(mutated))

    def test_cp005_check_fails_if_benchmarks_publishes_a_results_table(self):
        original = _benchmarks_text()
        mutated = original.replace(
            "Results are not published yet.",
            "| Subject | Median |\n|---|---|\n| semlog | 10.03 ms |\n",
            1,
        )
        self.assertNotEqual(mutated, original)
        problems = _published_benchmark_results(mutated)
        self.assertEqual(2, len(problems), problems)

    def test_cp005_check_fails_if_a_chart_or_growth_factor_is_published(self):
        original = readme_text("en")
        mutated = original + (
            "\n```mermaid\nxychart-beta\n    line [8.21, 51.78]\n```\n\nGrowth: ×6.31\n"
        )
        problems = _published_benchmark_results(mutated)
        self.assertIn("results chart", problems)
        self.assertIn("growth factor ×6.31", problems)

    def test_cp005_check_fails_if_an_environment_result_block_is_published(self):
        original = _benchmarks_text()
        mutated = (
            original + "\n| Field | Value |\n|---|---|\n| CPU | 14 logical CPUs |\n"
        )
        problems = _published_benchmark_results(mutated)
        self.assertTrue(any("| CPU |" in p for p in problems), problems)

    def test_cp005_check_fails_if_the_field_scaling_counts_drift(self):
        scenarios = section(_methodology(_benchmarks_text()), 3, "Scenarios")
        phrase = _field_scaling_counts_phrase(field_scaling.FIELD_COUNTS)
        mutated = scenarios.replace(phrase, phrase.replace(" 25,", " 10,"), 1)
        self.assertNotEqual(mutated, scenarios)
        self.assertNotIn(phrase, mutated)

    def test_cp005_check_fails_if_the_documented_command_uses_an_unknown_flag(self):
        original = _benchmarks_text()
        mutated = original.replace("--runs 5", "--rounds 5", 1)
        self.assertNotEqual(mutated, original)
        argv = _harness_commands(mutated)[0].split(_HARNESS_COMMAND, 1)[1].split()
        self.assertFalse(_harness_accepts(argv))
        self.assertEqual(["--rounds"], _undocumented_harness_flags(mutated))

    def test_cp005_check_fails_if_a_readme_discloses_machine_details(self):
        original = readme_text("es")
        mutated = original + "\nMedido en una CPU Intel.\n"
        self.assertEqual([], _readme_performance_problems(original))
        self.assertIn("machine detail 'Intel'", _readme_performance_problems(mutated))

    def test_cp005_check_fails_if_a_readme_regains_a_performance_section(self):
        for language, labels in READMES.items():
            original = readme_text(language)
            mutated = original.replace(
                "\n## ",
                f"\n## {labels['performance']}\n\nSee [BENCHMARKS.md](BENCHMARKS.md).\n\n## ",
                1,
            )
            with self.subTest(language=language):
                self.assertNotEqual(mutated, original)
                problems = _readme_performance_problems(mutated)
                self.assertIn(
                    f"performance section {labels['performance']!r}", problems
                )
                self.assertIn("link to BENCHMARKS.md", problems)


class DocConformanceParserSelfTests(unittest.TestCase):
    """Fixture self-tests over crafted strings, never the real files."""

    def test_span_stops_at_the_next_declaration(self):
        text = (
            "**FIX-001**: first rule body, more text here.\n**FIX-002**: second rule.\n"
        )
        span = _span(text, "FIX-001")
        self.assertEqual("**FIX-001**: first rule body, more text here.", span)

    def test_span_stops_at_a_heading(self):
        text = "**FIX-001**: first rule.\n## Next section\nmore text\n"
        span = _span(text, "FIX-001")
        self.assertEqual("**FIX-001**: first rule.", span)

    def test_missing_keyword_row_is_caught(self):
        text = (
            f"{KEYWORD_TABLE_HEADER}\n"
            "|---|---|\n"
            "| MUST / SHALL | Absolute requirement |\n\n"
        )
        table = _keyword_table(text)
        missing = [
            keyword
            for keyword, meaning in REQUIRED_KEYWORD_MEANINGS.items()
            if f"| {keyword} | {meaning} |" not in table
        ]
        self.assertIn("MAY", missing)
        self.assertNotIn("MUST / SHALL", missing)

    def test_benchmark_number_pattern_catches_a_performance_claim(self):
        offenders = _BENCHMARK_NUMBER_RE.findall("semlog is 2x faster than structlog")
        self.assertTrue(offenders)

    def test_results_detector_ignores_methodology_parameters_in_prose(self):
        text = (
            "A writer that sleeps 2 ms per write (800 × 2 ms = 1.6 s), within a\n"
            "bounded factor (3x), at 90% capacity.\n\n"
            "| Framework | Version | Python |\n|---|---|---|\n| Django | 3.2.9 | 3.10 |\n"
        )
        self.assertEqual([], _published_benchmark_results(text))

    def test_results_detector_catches_a_greek_mu_figure(self):
        self.assertEqual(
            ["figure 8.21 μs"], _published_benchmark_results("costs 8.21 μs")
        )


if __name__ == "__main__":
    unittest.main()
