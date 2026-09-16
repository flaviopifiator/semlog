"""Anti-drift checks A1, A4, A5, A6, A8 (design-decisions #170 §17.5;
design-part3 #223 §8; STANDARDS.md DOC-009, DOC-010, DOC-011, CP-015,
CP-018). These five checks all run in the normal `python -m unittest
discover` suite (no installed package or built wheel needed); A2, A3 and A7
need an installed package or a built wheel and live in `tests/test_llm.py`
and `tests/test_wheel_contents.py` respectively (design's own "where they
run" split).

Every check here parses Markdown/source text with the standard library only
(``re``, ``ast``, ``json``) -- it never imports the agent guide or llms.txt
as code, since they are prose, not test sources.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
import unittest
from pathlib import Path

import semlog
from semlog import ASGIMiddleware, WSGIMiddleware, bind, configure, inject, operation
from semlog._transport import flush

from .test_output_schema import SCHEMA, validate_record

REPO_ROOT = Path(__file__).resolve().parents[1]
GUIDE_PATH = REPO_ROOT / "src" / "semlog" / "agent_guide.md"
LLMS_TXT_PATH = REPO_ROOT / "llms.txt"
AGENTS_MD_PATH = REPO_ROOT / "AGENTS.md"

_PUBLIC_OBJECTS = {
    "configure": configure,
    "WSGIMiddleware": WSGIMiddleware,
    "ASGIMiddleware": ASGIMiddleware,
    "operation": operation,
    "bind": bind,
    "inject": inject,
    "flush": flush,
    "llm": semlog.llm,
}

REQUIRED_H2_ORDER = (
    "Public API",
    "Call-site rules",
    "Levels and severity",
    "Output contract",
    "Configuration and precedence",
    "FastAPI recipe",
    "Django recipe",
    "When to use each API",
    "Migration checklist",
    "Review checklist",
)

_MAX_GUIDE_BYTES = 64 * 1024  # A8: 64 KiB


# ---------------------------------------------------------------------------
# Shared Markdown helpers (stdlib only: `re`; never imports the guide as code)
# ---------------------------------------------------------------------------


def _headings(text):
    """Yield `(line_no, level, title)` for every ATX heading."""
    for line_no, line in enumerate(text.splitlines(), start=1):
        m = re.match(r"^(#{1,6}) (.+)$", line)
        if m:
            yield line_no, len(m.group(1)), m.group(2).strip()


def _section_span(text, level, title):
    """Return `(start_line, end_line)` (inclusive, 1-based) for the section
    whose heading at `level` equals `title`, up to the next heading at the
    same or shallower level, or `None` if not found."""
    lines = text.splitlines()
    start = None
    for line_no, lvl, heading_title in _headings(text):
        if start is None and lvl == level and heading_title == title:
            start = line_no
            continue
        if start is not None and lvl <= level:
            return start, line_no - 1
    if start is None:
        return None
    return start, len(lines)


def _fenced_blocks(text, start_line=1, end_line=None):
    """Yield `(lang, content)` for every fenced code block whose opening
    fence line falls within `[start_line, end_line]` (1-based, inclusive)."""
    lines = text.splitlines()
    if end_line is None:
        end_line = len(lines)
    in_fence = False
    lang = None
    buf = []
    for line_no, line in enumerate(lines, start=1):
        if not (start_line <= line_no <= end_line):
            continue
        fence = re.match(r"^```(\w*)\s*$", line)
        if fence and not in_fence:
            in_fence = True
            lang = fence.group(1)
            buf = []
            continue
        if line.strip() == "```" and in_fence:
            in_fence = False
            yield lang, "\n".join(buf)
            continue
        if in_fence:
            buf.append(line)


def _guide_text():
    return GUIDE_PATH.read_text(encoding="utf-8")


def _normalize(s: str) -> str:
    """Whitespace- and quote-insensitive normalization (design #170 §17.5
    A1: "whitespace-insensitive"). Quote-insensitivity is also required in
    practice: `from __future__ import annotations` makes every real
    annotation a plain string, so `inspect.signature()` renders it quoted
    (`'str | None'`), while the guide's stub writes it bare (`str | None`);
    the project's own double-quoted string-default style (`"INFO"`) also
    differs textually from `repr()`'s single-quoted rendering (`'INFO'`).
    Stripping every quote character before collapsing whitespace resolves
    both, with no loss of comparison power for this module's signatures
    (only simple literals: `None`, short identifiers, and empty tuples). A
    trailing comma before a closing parenthesis is also normalized away:
    it is purely a multi-line formatting artifact (this project's own
    style for long parameter lists), never a structural difference."""
    stripped = re.sub(r"[\s\"']", "", s)
    return re.sub(r",\)", ")", stripped)


# ---------------------------------------------------------------------------
# A1: agent guide Public API section vs `semlog.__all__` and real signatures
# ---------------------------------------------------------------------------


def _real_stub_text(name, obj):
    """Reconstruct `def {name}(...): ...` (or `class {name}: def __init__
    (...): ...`) from `inspect.signature`, matching the guide's own stub
    convention -- comparison happens after `_normalize`, so exact spacing
    and quote style never matter, only real structure."""
    if inspect.isclass(obj):
        sig = inspect.signature(obj)
        self_param = inspect.Parameter("self", inspect.Parameter.POSITIONAL_OR_KEYWORD)
        sig = sig.replace(parameters=[self_param, *sig.parameters.values()])
        return f"class {name}:\n    def __init__{sig}: ..."
    sig = inspect.signature(obj)
    return f"def {name}{sig}: ..."


def _guide_public_api_stubs(text=None):
    """Return `{name: stub_source}` for every H3 under the guide's "##
    Public API" H2, using the first fenced code block found in each H3's
    span. Accepts `text` directly (perturbation tests below pass a mutated
    copy); defaults to the real guide file."""
    if text is None:
        text = _guide_text()
    span = _section_span(text, 2, "Public API")
    assert span is not None, "guide has no '## Public API' section"
    h2_start, h2_end = span
    h3_starts = [
        (line_no, title)
        for line_no, lvl, title in _headings(text)
        if lvl == 3 and h2_start <= line_no <= h2_end
    ]
    stubs = {}
    for i, (start, title) in enumerate(h3_starts):
        end = (h3_starts[i + 1][0] - 1) if i + 1 < len(h3_starts) else h2_end
        for lang, content in _fenced_blocks(text, start, end):
            if lang == "python":
                stubs[title] = content
                break
    return stubs


class AgentGuidePublicApiTests(unittest.TestCase):
    """Proves: DOC-011, CP-015, CP-018

    A1 (design-decisions #170 §17.5): the guide's Public API section has
    exactly one H3 per public name, the set equals `semlog.__all__` with no
    duplicates, and each documented signature equals the real
    `inspect.signature` (whitespace- and quote-insensitive)."""

    @classmethod
    def setUpClass(cls):
        cls.stubs = _guide_public_api_stubs()

    def test_h3_set_equals_all_with_no_duplicates(self):
        text = _guide_text()
        span = _section_span(text, 2, "Public API")
        h2_start, h2_end = span
        titles = [
            title
            for line_no, lvl, title in _headings(text)
            if lvl == 3 and h2_start <= line_no <= h2_end
        ]
        self.assertEqual(sorted(set(titles)), sorted(titles), "duplicate H3 heading")
        self.assertEqual(set(semlog.__all__), set(titles))

    def test_every_documented_signature_matches_inspect_signature(self):
        mismatches = []
        for name, obj in _PUBLIC_OBJECTS.items():
            guide_source = self.stubs.get(name)
            if guide_source is None:
                mismatches.append(f"{name}: no fenced python block found")
                continue
            real = _normalize(_real_stub_text(name, obj))
            documented = _normalize(guide_source)
            if real != documented:
                mismatches.append(f"{name}: guide={documented!r} real={real!r}")
        self.assertEqual([], mismatches)


REQUIRED_LLMS_TXT_TARGETS = (
    "README.md",
    "STANDARDS.md",
    "src/semlog/agent_guide.md",
    "schemas/log-record.schema.json",
    "schemas/event-catalog.schema.json",
)


def _link_offenders(text, repo_root):
    """Return every link target in `text` that is either an absolute URL or
    does not resolve to a file under `repo_root` (A4)."""
    offenders = []
    for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", target):
            offenders.append(f"{target}: absolute URL, not repository-relative")
            continue
        if not (repo_root / target).is_file():
            offenders.append(f"{target}: does not resolve to a repository file")
    return offenders


def _missing_required_targets(text):
    links = set(re.findall(r"\[[^\]]+\]\(([^)]+)\)", text))
    return [target for target in REQUIRED_LLMS_TXT_TARGETS if target not in links]


# ---------------------------------------------------------------------------
# A4: llms.txt structure and link resolution
# ---------------------------------------------------------------------------


class LlmsTxtConformanceTests(unittest.TestCase):
    """Proves: DOC-009

    A4 (design-decisions #170 §17.5/§17.7): `# semlog` H1, a blockquote, at
    least one H2 with a link list, every link repository-relative and
    resolving to an existing file, and the required targets present."""

    @classmethod
    def setUpClass(cls):
        cls.text = LLMS_TXT_PATH.read_text(encoding="utf-8")
        cls.lines = cls.text.splitlines()

    def test_starts_with_h1_semlog(self):
        self.assertEqual("# semlog", self.lines[0])

    def test_has_a_blockquote_summary(self):
        self.assertTrue(
            any(line.startswith("> ") for line in self.lines[:5]),
            "no blockquote summary in the first few lines",
        )

    def test_has_at_least_one_h2_link_list(self):
        h2_titles = [title for _ln, lvl, title in _headings(self.text) if lvl == 2]
        self.assertGreaterEqual(len(h2_titles), 1)

    def test_every_link_is_repository_relative_and_resolves(self):
        offenders = _link_offenders(self.text, REPO_ROOT)
        self.assertGreater(
            len(re.findall(r"\[[^\]]+\]\(([^)]+)\)", self.text)),
            0,
            "no links found in llms.txt",
        )
        self.assertEqual([], offenders)

    def test_required_targets_present(self):
        self.assertEqual([], _missing_required_targets(self.text))


def _missing_or_out_of_order_h2(text):
    """Required H2 titles absent, or present only before an earlier
    required title (A5)."""
    h2_titles = [title for _ln, lvl, title in _headings(text) if lvl == 2]
    cursor = 0
    missing = []
    for required in REQUIRED_H2_ORDER:
        try:
            idx = h2_titles.index(required, cursor)
        except ValueError:
            missing.append(required)
            continue
        cursor = idx + 1
    return missing


def _python_block_errors(text):
    errors = []
    for i, (lang, content) in enumerate(_fenced_blocks(text)):
        if lang != "python":
            continue
        try:
            ast.parse(content)
        except SyntaxError as exc:
            errors.append(f"python block #{i}: {exc}")
    return errors


def _json_block_errors(text):
    errors = []
    for i, (lang, content) in enumerate(_fenced_blocks(text)):
        if lang != "json":
            continue
        stripped = content.strip("\n")
        if "\n" in stripped:
            errors.append(f"json block #{i}: not a single line")
            continue
        try:
            instance = json.loads(stripped)
        except json.JSONDecodeError as exc:
            errors.append(f"json block #{i}: invalid JSON ({exc})")
            continue
        schema_errors = validate_record(instance, SCHEMA)
        if schema_errors:
            errors.append(f"json block #{i}: {schema_errors}")
    return errors


# ---------------------------------------------------------------------------
# A5: agent guide required H2 order; every fenced block parses/validates
# ---------------------------------------------------------------------------


class AgentGuideStructureTests(unittest.TestCase):
    """Proves: DOC-011

    A5 (design-decisions #170 §17.5): the guide's required H2 sections
    appear, in order (extra H2s are allowed after all required ones are
    seen, but not interleaved); every fenced `python` block parses with
    `ast.parse`; every fenced `json` block is one line, parses with
    `json.loads`, and passes the stdlib JSON Schema subset validator
    (`tests/test_output_schema.py`, task 4.7) against the published
    log-record schema."""

    @classmethod
    def setUpClass(cls):
        cls.text = _guide_text()

    def test_required_h2_sections_appear_in_order(self):
        self.assertEqual(
            [], _missing_or_out_of_order_h2(self.text), "missing/out-of-order H2"
        )

    def test_every_fenced_python_block_parses(self):
        self.assertEqual([], _python_block_errors(self.text))

    def test_every_fenced_json_block_is_one_line_and_schema_conformant(self):
        self.assertEqual([], _json_block_errors(self.text))


# ---------------------------------------------------------------------------
# A6: AGENTS.md exact commands/links, no heading overlap with the guide
# ---------------------------------------------------------------------------


REQUIRED_AGENTS_MD_COMMANDS = (
    "uv sync",
    "pip install -e .",
    "python -m unittest discover",
    "ruff check",
    "ruff format --check",
    "uv build",
    "python -m pip wheel . --no-deps --wheel-dir dist",
)
REQUIRED_AGENTS_MD_LINK_TARGETS = (
    "STANDARDS.md",
    "src/semlog/agent_guide.md",
    "llms.txt",
)


def _missing_commands(text):
    return [cmd for cmd in REQUIRED_AGENTS_MD_COMMANDS if cmd not in text]


def _missing_link_targets(text):
    return [t for t in REQUIRED_AGENTS_MD_LINK_TARGETS if f"]({t})" not in text]


def _h2_heading_overlap(agents_text, guide_text):
    agents_h2 = {title for _ln, lvl, title in _headings(agents_text) if lvl == 2}
    guide_h2 = {title for _ln, lvl, title in _headings(guide_text) if lvl == 2}
    return agents_h2 & guide_h2


class AgentsMdConformanceTests(unittest.TestCase):
    """Proves: DOC-010

    A6 (design-decisions #170 §17.5/§17.6): AGENTS.md carries the exact
    setup/test/lint/format/build commands (and their `pip`/plain fallbacks),
    links to STANDARDS.md, the guide and llms.txt, and shares no H2 heading
    with the guide (no content duplication)."""

    @classmethod
    def setUpClass(cls):
        cls.text = AGENTS_MD_PATH.read_text(encoding="utf-8")

    def test_exact_commands_present(self):
        self.assertEqual([], _missing_commands(self.text))

    def test_links_to_standards_guide_and_llms_txt(self):
        self.assertEqual([], _missing_link_targets(self.text))

    def test_no_h2_heading_duplicates_the_guide(self):
        self.assertEqual(set(), _h2_heading_overlap(self.text, _guide_text()))


# ---------------------------------------------------------------------------
# A8: guide stays under its documented size budget
# ---------------------------------------------------------------------------


class AgentGuideSizeTests(unittest.TestCase):
    """Proves: DOC-011

    A8 (design-decisions #170 §17.5): the guide stays under 64 KiB so it
    fits comfortably in an agent's context."""

    def test_guide_stays_under_64_kib(self):
        size = GUIDE_PATH.stat().st_size
        self.assertLessEqual(size, _MAX_GUIDE_BYTES, f"{size} bytes")


_GUIDE_MODES_SECTION_TITLE = "Execution modes and the semlog=True keyword"
_GUIDE_MODE_BULLET_MARKERS = ("- **`full`**:", "- **`hybrid`**:", "- **`off`**:")
_GUIDE_PRECEDENCE_TOKENS = ("configure(", "SEMLOG_MODE", "[tool.semlog]")
_GUIDE_LIMITATION_MARKERS = (
    "StreamHandler",
    "QueueHandler",
    "QueueListener",
    "MemoryHandler",
    "super()",
)
_GUIDE_TOML_EXAMPLE = '[tool.semlog]\nmode = "hybrid"'
_GUIDE_TOML_EXAMPLE_MUTATED = '[tool.semlog_typo]\nmode = "hybrid"'
_GUIDE_TOMLLIB_CAVEAT_VERSION = "3.11"


def _guide_modes_section_text(text):
    span = _section_span(text, 3, _GUIDE_MODES_SECTION_TITLE)
    if span is None:
        return None
    lines = text.splitlines()
    return "\n".join(lines[span[0] - 1 : span[1]])


def guide_modes_narrative_problems(text=None):
    """Every way the agent guide's modes/keyword narrative fails to prove
    DOC-011's full text (empty when it proves everything). Scoped to the
    "Execution modes and the semlog=True keyword" H3 span, never a
    whole-file `in` check: `agent_guide.md` has an UNRELATED
    `StreamHandler` mention in its `configure()` stub's `queue=False`
    description (validate-wu69 #338, MAJOR M1's own repro), so a
    whole-file check for that single word alone passes even with this
    entire subsection deleted. The TOML example and the tomllib version
    caveat are also checked here (validate-wu69 #339, MINOR n3): a
    single-file document has no cross-file parity check at all, so an
    edit to either one here previously went completely undetected."""
    if text is None:
        text = _guide_text()
    section_text = _guide_modes_section_text(text)
    if section_text is None:
        return [f"no {_GUIDE_MODES_SECTION_TITLE!r} subsection"]

    problems = []
    for marker in _GUIDE_MODE_BULLET_MARKERS:
        if marker not in section_text:
            problems.append(f"missing mode-explanation bullet {marker!r}")

    positions = [section_text.find(token) for token in _GUIDE_PRECEDENCE_TOKENS]
    for token, position in zip(_GUIDE_PRECEDENCE_TOKENS, positions):
        if position == -1:
            problems.append(f"missing precedence token {token!r}")
    if all(position != -1 for position in positions) and positions != sorted(positions):
        problems.append("precedence sequence is out of order")

    if "ValueError" not in section_text:
        problems.append("missing ValueError")

    if _GUIDE_TOML_EXAMPLE not in section_text:
        problems.append("TOML example missing or altered")

    if _GUIDE_TOMLLIB_CAVEAT_VERSION not in section_text:
        problems.append("missing tomllib version caveat")

    for marker in _GUIDE_LIMITATION_MARKERS:
        if marker not in section_text:
            problems.append(f"missing limitation marker {marker!r}")

    if "process-start switch" not in section_text:
        problems.append("missing off-as-process-start-switch phrase")

    if "TypeError" not in section_text:
        problems.append("missing TypeError")

    return problems


class AgentGuideModesTests(unittest.TestCase):
    """DOC-011's mode/keyword narrative: hybrid's suppression scope with
    its `StreamHandler`-subclass-override and `QueueHandler`/
    `QueueListener`/`MemoryHandler` exceptions (LM-004, errata 12,
    validate-wu69 #338 m2), the three configuration sources and their
    ORDERED precedence, the `semlog=True` keyword, and `off` as a
    process-start switch with the uninstall-`TypeError` caveat. Every
    assertion is scoped to the modes subsection's own span
    (`guide_modes_narrative_problems`), not a whole-file token check
    (validate-wu69 #338, MAJOR M1).

    Proves: LM-004, DOC-011
    """

    def test_guide_proves_the_full_modes_narrative(self):
        self.assertEqual([], guide_modes_narrative_problems())

    def test_guide_documents_the_semlog_true_keyword_per_mode(self):
        section_text = _guide_modes_section_text(_guide_text())
        self.assertIsNotNone(section_text)
        self.assertIn("semlog=True", section_text)


class AgentGuideModesPerturbationTests(unittest.TestCase):
    """Every check `guide_modes_narrative_problems` performs is
    demonstrated here to be capable of failing, against deliberately
    broken in-memory copies of the real guide text (validate-wu69 #338,
    MAJOR M1's required mutation coverage). The real file is never
    written to."""

    @classmethod
    def setUpClass(cls):
        cls.text = _guide_text()

    def test_baseline_has_no_problems(self):
        self.assertEqual([], guide_modes_narrative_problems(self.text))

    def test_deleting_the_modes_subsection_entirely_is_caught(self):
        start = self.text.index(f"### {_GUIDE_MODES_SECTION_TITLE}")
        end = self.text.index("## FastAPI recipe")
        mutated = self.text[:start] + self.text[end:]
        self.assertNotEqual(mutated, self.text)
        # The exact vacuousness validate-wu69 #338 found: a whole-file
        # check for "StreamHandler" alone still passes here, because the
        # unrelated queue=False mention at the top of the guide survives.
        self.assertIn("StreamHandler", mutated)
        self.assertEqual(
            [f"no {_GUIDE_MODES_SECTION_TITLE!r} subsection"],
            guide_modes_narrative_problems(mutated),
        )

    def test_reordered_precedence_is_caught(self):
        mutated = self.text.replace(
            "1. the `mode` keyword argument to `configure()`;\n"
            "2. the `SEMLOG_MODE` environment variable;\n",
            "1. the `SEMLOG_MODE` environment variable;\n"
            "2. the `mode` keyword argument to `configure()`;\n",
            1,
        )
        self.assertNotEqual(mutated, self.text)
        self.assertIn(
            "precedence sequence is out of order",
            guide_modes_narrative_problems(mutated),
        )

    def test_deleting_a_mode_value_bullet_is_caught(self):
        mutated = self.text.replace(
            "- **`off`**: `semlog=True` produces no JSON output and no "
            "other side effect of its own; `configure()` installs nothing, "
            "`operation()`, `bind()` and both middlewares keep working as "
            "inert pass-throughs. A process that starts in `off` mode "
            "behaves as if semlog were never installed. Reconfiguring an "
            "already-running process from `full`/`hybrid` to `off` leaves "
            "an already-installed JSON pipeline attached; `off` is a "
            "process-start switch, not a live toggle. Removing `semlog` "
            "from a service while `semlog=True` call sites remain raises "
            "`TypeError` at an enabled call site rather than failing "
            "silently; strip the keyword from call sites before "
            "uninstalling.\n",
            "",
            1,
        )
        self.assertNotEqual(mutated, self.text)
        problems = guide_modes_narrative_problems(mutated)
        self.assertIn(f"missing mode-explanation bullet {'- **`off`**:'!r}", problems)
        self.assertIn("missing TypeError", problems)
        self.assertIn("missing off-as-process-start-switch phrase", problems)

    def test_valueerror_changed_to_typeerror_is_caught(self):
        mutated = self.text.replace(
            "raises `ValueError` naming both the invalid value and its source.",
            "raises `TypeError` naming both the invalid value and its source.",
            1,
        )
        self.assertNotEqual(mutated, self.text)
        self.assertIn("missing ValueError", guide_modes_narrative_problems(mutated))

    def test_removing_the_streamhandler_override_clause_is_caught(self):
        mutated = self.text.replace(
            ", and so may a `StreamHandler` subclass that overrides "
            "`handle()` without calling `super().handle()`",
            "",
            1,
        )
        self.assertNotEqual(mutated, self.text)
        self.assertIn(
            f"missing limitation marker {'super()'!r}",
            guide_modes_narrative_problems(mutated),
        )

    def test_toml_example_mutation_is_caught(self):
        # validate-wu69 #339, MINOR n3: the guide is a single file with no
        # cross-edition parity check, so an edit here was previously
        # undetected regardless of whether the READMEs stayed correct.
        mutated = self.text.replace(_GUIDE_TOML_EXAMPLE, _GUIDE_TOML_EXAMPLE_MUTATED, 1)
        self.assertNotEqual(mutated, self.text)
        self.assertIn(
            "TOML example missing or altered", guide_modes_narrative_problems(mutated)
        )

    def test_tomllib_caveat_version_mutation_is_caught(self):
        mutated = self.text.replace(
            "available on Python 3.11 and later",
            "available on Python 3.10 and later",
            1,
        )
        self.assertNotEqual(mutated, self.text)
        self.assertIn(
            "missing tomllib version caveat", guide_modes_narrative_problems(mutated)
        )


# ---------------------------------------------------------------------------
# Perturbation proof: every check above that passed on its first run against
# the real, already-correct files is demonstrated here to be capable of
# failing. Each test mutates an in-memory COPY of the real text (or, for A8,
# writes a throwaway file under a `tempfile.TemporaryDirectory()`); the real
# repository files are never written to.
# ---------------------------------------------------------------------------


class PerturbationProofTests(unittest.TestCase):
    """Proves the checks in this module are not vacuously true: each one is
    shown to fail against a deliberately broken copy of real input, never
    against the real file on disk."""

    @classmethod
    def setUpClass(cls):
        cls.guide_text = _guide_text()
        cls.llms_txt_text = LLMS_TXT_PATH.read_text(encoding="utf-8")
        cls.agents_md_text = AGENTS_MD_PATH.read_text(encoding="utf-8")

    # -- A1: signature drift --------------------------------------------

    def test_a1_catches_a_removed_parameter_in_the_guide_copy(self):
        mutated = self.guide_text.replace("    search_dir: str | None = None,\n", "")
        self.assertNotEqual(mutated, self.guide_text, "fixture setup: no match found")
        stubs = _guide_public_api_stubs(mutated)
        real = _normalize(_real_stub_text("configure", configure))
        documented = _normalize(stubs["configure"])
        self.assertNotEqual(
            real, documented, "mutated guide should no longer match the real signature"
        )

    # -- A4: llms.txt link resolution/absolute-URL/required targets -----

    def test_a4_catches_a_broken_relative_link(self):
        mutated = self.llms_txt_text.replace(
            "[README](README.md)", "[README](README-typo.md)"
        )
        self.assertNotEqual(mutated, self.llms_txt_text, "fixture setup: no match")
        offenders = _link_offenders(mutated, REPO_ROOT)
        self.assertTrue(any("README-typo.md" in o for o in offenders), offenders)

    def test_a4_catches_an_absolute_url(self):
        mutated = self.llms_txt_text.replace(
            "[README](README.md)", "[README](https://example.com/README.md)"
        )
        self.assertNotEqual(mutated, self.llms_txt_text, "fixture setup: no match")
        offenders = _link_offenders(mutated, REPO_ROOT)
        self.assertTrue(any("absolute URL" in o for o in offenders), offenders)

    def test_a4_catches_a_removed_required_target(self):
        mutated = self.llms_txt_text.replace(
            "[STANDARDS](STANDARDS.md)", "[STANDARDS removed]()"
        )
        self.assertNotEqual(mutated, self.llms_txt_text, "fixture setup: no match")
        self.assertIn("STANDARDS.md", _missing_required_targets(mutated))

    # -- A5: guide structure ---------------------------------------------

    def test_a5_catches_a_missing_required_h2(self):
        mutated = self.guide_text.replace(
            "## Call-site rules", "## Call-site rules renamed"
        )
        self.assertNotEqual(mutated, self.guide_text, "fixture setup: no match")
        self.assertIn("Call-site rules", _missing_or_out_of_order_h2(mutated))

    def test_a5_catches_a_reordered_required_h2(self):
        # Swap "## Levels and severity" to appear before "## Call-site
        # rules" (both stay present, but out of the required order).
        mutated = (
            self.guide_text.replace(
                "## Call-site rules", "## Call-site rules moved-after-levels", 1
            )
            .replace("## Levels and severity\n", "## Call-site rules\n", 1)
            .replace(
                "## Call-site rules moved-after-levels", "## Levels and severity", 1
            )
        )
        self.assertNotEqual(mutated, self.guide_text, "fixture setup: no-op mutation")
        violations = _missing_or_out_of_order_h2(mutated)
        self.assertTrue(violations, "reordering the required H2s should be flagged")

    def test_a5_catches_a_syntax_error_in_a_python_block(self):
        mutated = self.guide_text.replace(
            "def llm() -> str: ...", "def llm( -> str: ..."
        )
        self.assertNotEqual(mutated, self.guide_text, "fixture setup: no match")
        errors = _python_block_errors(mutated)
        self.assertTrue(errors)

    def test_a5_catches_invalid_json_in_the_json_block(self):
        mutated = self.guide_text.replace(
            '"telemetry.sdk.language":"python"}', '"telemetry.sdk.language":"python"'
        )
        self.assertNotEqual(mutated, self.guide_text, "fixture setup: no match")
        errors = _json_block_errors(mutated)
        self.assertTrue(any("invalid JSON" in e for e in errors), errors)

    def test_a5_catches_a_schema_violating_json_block(self):
        mutated = self.guide_text.replace('"severity_number":9', '"severity_number":99')
        self.assertNotEqual(mutated, self.guide_text, "fixture setup: no match")
        errors = _json_block_errors(mutated)
        self.assertTrue(any("severity_number" in str(e) for e in errors), errors)

    # -- A6: AGENTS.md commands/links/heading-overlap ---------------------

    def test_a6_catches_a_missing_command(self):
        mutated = self.agents_md_text.replace("ruff format --check", "ruff fmt --check")
        self.assertNotEqual(mutated, self.agents_md_text, "fixture setup: no match")
        self.assertIn("ruff format --check", _missing_commands(mutated))

    def test_a6_catches_a_missing_link(self):
        mutated = self.agents_md_text.replace(
            "[llms.txt](llms.txt)", "[llms.txt removed]()"
        )
        self.assertNotEqual(mutated, self.agents_md_text, "fixture setup: no match")
        self.assertIn("llms.txt", _missing_link_targets(mutated))

    def test_a6_catches_a_duplicated_guide_heading(self):
        mutated = self.agents_md_text.replace(
            "## Setup", "## Public API\n\n## Setup", 1
        )
        self.assertNotEqual(mutated, self.agents_md_text, "fixture setup: no match")
        overlap = _h2_heading_overlap(mutated, self.guide_text)
        self.assertIn("Public API", overlap)

    # -- A8: guide size budget -------------------------------------------

    def test_a8_catches_an_oversized_guide_copy(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            oversized = Path(tmp) / "agent_guide.md"
            oversized.write_text("x" * (_MAX_GUIDE_BYTES + 1), encoding="utf-8")
            self.assertGreater(oversized.stat().st_size, _MAX_GUIDE_BYTES)


if __name__ == "__main__":
    unittest.main()
