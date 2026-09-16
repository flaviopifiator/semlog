"""README translation parity (STANDARDS.md DOC-002): `README.md` (English)
and `README.es.md` (Spanish) are the same document in two languages.

Both editions must open with the language switch linking to the other,
share the same heading structure (count and levels), carry byte-identical
fenced code blocks (code is never translated), document exactly the
parameters of `inspect.signature(semlog.configure)` in their configuration
table, and show the same badges. Stdlib-only Markdown parsing through
`tests/_readme_support.py`; the READMEs are never imported as code.
"""

from __future__ import annotations

import inspect
import re
import unittest

import semlog

from ._readme_support import (
    READMES,
    REPO_ROOT,
    badges,
    fenced_blocks,
    headings,
    readme_text,
    section,
)
from .test_traceability import STANDARDS_PATH, parse_standards


def configure_table_params(text, title):
    """Backtick-quoted parameter names in the `Parameter` column of the
    table under the `## {title}` heading (before its first subsection)."""
    table = section(text, 2, title).split("\n### ", 1)[0]
    return set(re.findall(r"^\| [^|]+ \| `([a-z_]+)` \|", table, re.MULTILINE))


_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def documentation_items(text, title):
    """The link targets of each list item under the `## {title}` heading,
    one tuple per item, in order."""
    return [
        tuple(_LINK_RE.findall(line))
        for line in section(text, 2, title).splitlines()
        if line.startswith("- ")
    ]


def _switch_problems(text, language):
    first_line = next(line for line in text.splitlines() if line.strip())
    expected = READMES[language]["switch"]
    return [] if first_line == expected else [f"{language}: {first_line!r}"]


def _badge_set(text):
    return {(image, link) for _alt, image, link in badges(text)}


class ReadmeTranslationParityTests(unittest.TestCase):
    """Proves: DOC-002"""

    @classmethod
    def setUpClass(cls):
        cls.texts = {language: readme_text(language) for language in READMES}

    def test_both_editions_open_with_the_language_switch(self):
        for language, text in self.texts.items():
            with self.subTest(language=language):
                self.assertEqual([], _switch_problems(text, language))
        for target in ("README.md", "README.es.md"):
            self.assertTrue((REPO_ROOT / target).is_file(), target)

    def test_same_heading_count_and_levels(self):
        levels = {
            lang: [lvl for lvl, _t in headings(t)] for lang, t in self.texts.items()
        }
        self.assertGreater(len(levels["en"]), 1)
        self.assertEqual(levels["en"], levels["es"])

    def test_code_blocks_are_byte_identical(self):
        blocks = {lang: fenced_blocks(text) for lang, text in self.texts.items()}
        self.assertGreater(len(blocks["en"]), 0)
        self.assertEqual(len(blocks["en"]), len(blocks["es"]))
        for index, (english, spanish) in enumerate(zip(blocks["en"], blocks["es"])):
            with self.subTest(block=index):
                self.assertEqual(english, spanish)

    def test_configuration_tables_name_exactly_the_configure_parameters(self):
        real = set(inspect.signature(semlog.configure).parameters)
        for language, text in self.texts.items():
            with self.subTest(language=language):
                title = READMES[language]["configuration"]
                self.assertEqual(real, configure_table_params(text, title))

    def test_same_set_of_badges(self):
        english = _badge_set(self.texts["en"])
        self.assertGreater(len(english), 0)
        self.assertEqual(english, _badge_set(self.texts["es"]))

    def test_documentation_lists_link_the_same_targets_item_by_item(self):
        items = {
            language: documentation_items(text, READMES[language]["documentation"])
            for language, text in self.texts.items()
        }
        self.assertGreater(len(items["en"]), 0)
        self.assertTrue(all(items["en"]), "a documentation item has no link")
        self.assertEqual(items["en"], items["es"])


class ReadmeParityPerturbationTests(unittest.TestCase):
    """Each parity check fails against a deliberately broken in-memory copy
    of the real README text, never against the files on disk."""

    @classmethod
    def setUpClass(cls):
        cls.english = readme_text("en")
        cls.spanish = readme_text("es")

    def test_a_missing_switch_is_caught(self):
        mutated = self.spanish.replace("[English](README.md) | **Español**", "", 1)
        self.assertNotEqual(mutated, self.spanish)
        self.assertTrue(_switch_problems(mutated, "es"))

    def test_an_extra_heading_is_caught(self):
        mutated = self.spanish.replace("## Licencia", "## Extra\n\n## Licencia", 1)
        self.assertNotEqual(mutated, self.spanish)
        self.assertNotEqual(
            [lvl for lvl, _t in headings(self.english)],
            [lvl for lvl, _t in headings(mutated)],
        )

    def test_a_hash_comment_inside_a_code_block_is_not_a_heading(self):
        text = "# Title\n\n```python\n# wsgi.py\nimport semlog\n```\n"
        self.assertEqual([(1, "Title")], headings(text))

    def test_a_translated_code_block_is_caught(self):
        mutated = self.spanish.replace(
            'semlog.configure(service_name="checkout")',
            'semlog.configure(service_name="caja")',
            1,
        )
        self.assertNotEqual(mutated, self.spanish)
        self.assertNotEqual(fenced_blocks(self.english), fenced_blocks(mutated))

    def test_a_removed_configuration_row_is_caught(self):
        mutated = re.sub(r"(?m)^\| Transporte \| `queue_size` \|.*\n", "", self.spanish)
        self.assertNotEqual(mutated, self.spanish)
        self.assertNotIn("queue_size", configure_table_params(mutated, "Configuración"))

    def test_an_invented_configuration_row_is_caught(self):
        mutated = self.english.replace(
            "| Output | `level` |",
            "| Output | `not_a_real_param` | x | x |\n| Output | `level` |",
            1,
        )
        self.assertNotEqual(mutated, self.english)
        params = configure_table_params(mutated, "Configuration")
        self.assertIn(
            "not_a_real_param",
            params - set(inspect.signature(semlog.configure).parameters),
        )

    def test_a_documentation_link_missing_from_one_edition_is_caught(self):
        line = next(
            ln for ln in self.spanish.splitlines() if ln.startswith("- [SECURITY.md]")
        )
        mutated = self.spanish.replace(line + "\n", "", 1)
        self.assertNotEqual(mutated, self.spanish)
        self.assertNotEqual(
            documentation_items(self.english, "Documentation"),
            documentation_items(mutated, "Documentación"),
        )

    def test_a_badge_missing_from_one_edition_is_caught(self):
        line = next(
            ln for ln in self.spanish.splitlines() if ln.startswith("[![License")
        )
        mutated = self.spanish.replace(line + "\n", "", 1)
        self.assertNotEqual(mutated, self.spanish)
        self.assertNotEqual(_badge_set(self.english), _badge_set(mutated))


_OPENING_H2_KEYS = ("what", "why", "philosophy", "installation", "quick_start")
_SUMMARY_LINE_RE = re.compile(r"^\*\*\S.*\.\*\*$")
# The two differentiators, as the ordered technical tokens that carry them:
# the adoption story first (`hybrid`, the `semlog=True` keyword it needs, and
# the `SEMLOG_MODE=off` rollback), then the zero-dependency story, whose
# claim is exactly the `Requires-Dist` count of the built wheel that
# `tests/test_wheel_contents.py` asserts. Every token is language-neutral,
# so the same ordered check applies to both editions verbatim.
_DIFFERENTIATOR_TOKENS = (
    "`hybrid`",
    "`semlog=True`",
    "`SEMLOG_MODE=off`",
    "`Requires-Dist`",
)
_INSTALL_COMMAND = "pip install semlog"
# Checked as a whole fenced block, not as a substring: `uv pip install
# semlog` contains `pip install semlog`, so a substring check stays green
# even when the plain `pip` instruction is gone.
_INSTALL_BLOCK = ("bash", _INSTALL_COMMAND)
# The opening section's own job: say what semlog does, in the same terms
# that explain the name. The two word parts and the specification the name
# refers to all stay untranslated in the Spanish edition, being code spans
# and a proper noun.
_NAME_TOKENS = ("`semantic`", "`log`", "OpenTelemetry", "Semantic Conventions")
# The two prose facts of the opening section that ARE translated, so they
# are looked up per language (same precedent as `_SEARCH_DIRECTION_PHRASES`
# below): a mutation applied IDENTICALLY to both editions defeats cross-file
# parity alone, since the parity checks only compare one edition against the
# other. The first is why a named field beats message text; the second is
# what the library actually does, the fact that used to sit three sections
# further down.
_NAME_RATIONALE_PHRASES = {
    "en": "stable name and a stable meaning",
    "es": "nombre y un significado estables",
}
_WHAT_IT_DOES_PHRASES = {
    "en": "decides what every resulting record looks like",
    "es": "define la forma de cada registro resultante",
}
# Philosophy: one language-neutral technical token per stated position, so
# a section that drops a position cannot stay green. Presence only, never
# order: unlike the two differentiators, the positions are not ranked.
_PHILOSOPHY_TOKENS = (
    "`schemas/log-record.schema.json`",
    "`Requires-Dist`",
    "`logging.getLogger(__name__)`",
    "`hybrid`",
    "`SEMLOG_MODE=off`",
    "Semantic Conventions",
    "W3C Trace Context",
    "`python -m semlog llm`",
)
# The two philosophy claims carried by prose alone, per language.
_PHILOSOPHY_PHRASES = {
    "en": ("major version change", "at least one test that cites it"),
    "es": ("cambio de versión mayor", "al menos una prueba que lo cita"),
}
# "STANDARDS.md declares N requirements" / "STANDARDS.md declara N
# requisitos": N is checked against the real declaration count, never
# trusted, the same way `tests/test_readme_badges.py` checks the badge.
_REQUIREMENT_COUNT_RES = {
    "en": re.compile(r"declares (\d+) requirements"),
    "es": re.compile(r"declara (\d+) requisitos"),
}

_MODE_BULLET_MARKERS = ("- **`full`**:", "- **`hybrid`**:", "- **`off`**:")
_PRECEDENCE_TOKENS = ("configure(", "SEMLOG_MODE", "[tool.semlog]")
_LIMITATION_MARKERS = (
    "StreamHandler",
    "QueueHandler",
    "QueueListener",
    "MemoryHandler",
    "super()",
)
_TOML_EXAMPLE = '[tool.semlog]\nmode = "hybrid"'
_TOML_EXAMPLE_MUTATED = '[tool.semlog_typo]\nmode = "hybrid"'
_TOMLLIB_CAVEAT_VERSION = "3.11"
_SEARCH_DIRECTION_PHRASES = {
    "en": "upward through its parent directories",
    "es": "hacia arriba por sus directorios padres",
}
_PRECEDENCE_DEFAULT_RE = re.compile(r'4\.[^\n]*`"full"`')
_ADOPTION_ORDER_RE = re.compile(r"`hybrid`.{0,30}?`full`", re.DOTALL)
_ENV_VAR_ROW = "| `SEMLOG_MODE` |"


def opening_problems(text, language):
    """Every way a README's first screen fails to answer, in this order,
    what semlog is, what makes it different, and how to install it (empty
    when it answers all three).

    The order is the check: a reader who has to scroll past a
    configuration table to learn what the project does has already left,
    and so has one who has to scroll past install instructions. So the H1
    is followed immediately by a one-line summary, the first five
    level-two sections are what semlog is, the "why", the philosophy, the
    install instructions and the quick start, and the "why" section states
    the two differentiators in a fixed order. Scoped to the relevant
    section span via `section()`, never a whole-document `in` check, so an
    unrelated mention of the same token elsewhere in a 20+ KB document
    cannot satisfy it.
    """
    labels = READMES[language]
    problems = []

    lines = [line for line in text.splitlines() if line.strip()]
    if "# semlog" not in lines:
        return ["no `# semlog` H1"]
    title_index = lines.index("# semlog")
    summary = lines[title_index + 1] if title_index + 1 < len(lines) else ""
    if not _SUMMARY_LINE_RE.match(summary):
        problems.append(f"no one-line summary under the H1: {summary!r}")

    titles = [title for level, title in headings(text) if level == 2]
    expected = [labels[key] for key in _OPENING_H2_KEYS]
    if titles[: len(expected)] != expected:
        problems.append(f"opening sections are {titles[: len(expected)]!r}")

    try:
        why_text = section(text, 2, labels["why"])
    except ValueError:
        problems.append("no Why section")
        why_text = ""
    positions = [why_text.find(token) for token in _DIFFERENTIATOR_TOKENS]
    for token, position in zip(_DIFFERENTIATOR_TOKENS, positions):
        if position == -1:
            problems.append(f"missing differentiator token {token!r}")
    if all(position != -1 for position in positions) and positions != sorted(positions):
        problems.append("differentiators are out of order")

    try:
        install_text = section(text, 2, labels["installation"])
    except ValueError:
        problems.append("no Installation section")
        install_text = ""
    if _INSTALL_BLOCK not in fenced_blocks(install_text):
        problems.append(f"no `bash` block holding exactly {_INSTALL_COMMAND!r}")

    return problems


def what_section_problems(text, language):
    """Every way a README's opening section fails to say what semlog does,
    where its name comes from and why that matters (empty when it says all
    three)."""
    labels = READMES[language]
    try:
        what_text = section(text, 2, labels["what"])
    except ValueError:
        return ["no what-semlog-is section"]
    problems = [
        f"missing name-explanation token {token!r}"
        for token in _NAME_TOKENS
        if token not in what_text
    ]
    if _NAME_RATIONALE_PHRASES[language] not in what_text:
        problems.append("missing stable-meaning rationale")
    if _WHAT_IT_DOES_PHRASES[language] not in what_text:
        problems.append("missing what-semlog-does statement")
    return problems


def declared_requirement_count():
    """How many requirement IDs STANDARDS.md declares, counted with the
    traceability checker's own parser (`tests/test_traceability.py`), the
    same source `tests/test_readme_badges.py` checks the badge against."""
    standards = parse_standards(STANDARDS_PATH.read_text(encoding="utf-8"))
    return len(standards.declarations)


def philosophy_problems(text, language):
    """Every way a README's philosophy section fails to state the project's
    positions and to state the requirement count correctly (empty when it
    states all of them). Scoped to the section's own span, never a
    whole-document check: every token below also appears somewhere else in
    a 20+ KB document, so an unscoped check would be vacuous."""
    labels = READMES[language]
    try:
        philosophy_text = section(text, 2, labels["philosophy"])
    except ValueError:
        return ["no Philosophy section"]
    problems = [
        f"missing philosophy token {token!r}"
        for token in _PHILOSOPHY_TOKENS
        if token not in philosophy_text
    ]
    problems += [
        f"missing philosophy phrase {phrase!r}"
        for phrase in _PHILOSOPHY_PHRASES[language]
        if phrase not in philosophy_text
    ]
    match = _REQUIREMENT_COUNT_RES[language].search(philosophy_text)
    if match is None:
        problems.append("no stated requirement count")
    elif int(match.group(1)) != declared_requirement_count():
        problems.append(f"stated requirement count {match.group(1)} is stale")
    return problems


def modes_narrative_problems(text, language):
    """Every way `text`'s "## Modes"/"## Modos" section fails to prove
    DOC-012/LM-004's full narrative (empty when it proves everything).
    Every check is scoped to the relevant section or subsection span (via
    `section()`), never a whole-document `in` check, so an unrelated
    mention of the same word elsewhere in a 20+ KB document (validate-wu69
    #338, MAJOR M1) cannot satisfy it. `configure(`, `SEMLOG_MODE`,
    `[tool.semlog]`, the mode value literals, `StreamHandler`-family
    names, `super()`, `SEMLOG_MODE=off`, `TypeError`, the TOML example,
    the `3.11` tomllib caveat, and the `full`/`hybrid` backtick literals
    are all language-neutral technical tokens, unchanged in the Spanish
    edition, so the same checks apply to both editions verbatim. The
    upward-search phrase is the one prose fact that IS translated, so it
    is looked up per `language` (validate-wu69 #339, MINOR n3: a mutation
    applied IDENTICALLY to both editions defeats cross-file parity alone,
    since `test_code_blocks_are_byte_identical` only compares one edition
    against the other, never against a known-good reference)."""
    labels = READMES[language]
    problems = []
    try:
        modes_text = section(text, 2, labels["modes"])
    except ValueError:
        return ["no Modes section"]

    for marker in _MODE_BULLET_MARKERS:
        if marker not in modes_text:
            problems.append(f"missing mode-explanation bullet {marker!r}")

    if not _ADOPTION_ORDER_RE.search(modes_text):
        problems.append("hybrid-then-full adoption order missing or reversed")

    try:
        sources_text = section(modes_text, 3, labels["configuration_sources"])
    except ValueError:
        problems.append("no Configuration sources subsection")
        sources_text = ""
    positions = [sources_text.find(token) for token in _PRECEDENCE_TOKENS]
    for token, position in zip(_PRECEDENCE_TOKENS, positions):
        if position == -1:
            problems.append(f"missing precedence token {token!r}")
    if all(position != -1 for position in positions) and positions != sorted(positions):
        problems.append("precedence sequence is out of order")
    if "ValueError" not in sources_text:
        problems.append("missing ValueError")
    if _TOML_EXAMPLE not in sources_text:
        problems.append("TOML example missing or altered")
    if _TOMLLIB_CAVEAT_VERSION not in sources_text:
        problems.append("missing tomllib version caveat")
    if _SEARCH_DIRECTION_PHRASES[language] not in sources_text:
        problems.append("missing upward-search-through-parents phrase")
    if not _PRECEDENCE_DEFAULT_RE.search(sources_text):
        problems.append('missing precedence item 4 default `"full"`')

    try:
        limitations_text = section(modes_text, 3, labels["limitations"])
    except ValueError:
        problems.append("no Limitations subsection")
        limitations_text = ""
    for marker in _LIMITATION_MARKERS:
        if marker not in limitations_text:
            problems.append(f"missing limitation marker {marker!r}")
    if "SEMLOG_MODE=off" not in limitations_text:
        problems.append("missing off-as-process-start-switch bullet")
    if "TypeError" not in limitations_text:
        problems.append("missing TypeError bullet")

    return problems


def env_var_table_problems(text, language):
    """Every way `text`'s "### Precedence and environment variables"/
    "### Precedencia y variables de entorno" subsection fails to keep the
    `SEMLOG_MODE` row in its environment-variable table (empty when it
    proves everything). Scoped via `section()`, never a whole-document
    check (validate-wu69 #339, MINOR n3)."""
    labels = READMES[language]
    try:
        configuration_text = section(text, 2, labels["configuration"])
    except ValueError:
        return ["no Configuration section"]
    try:
        precedence_text = section(configuration_text, 3, labels["precedence"])
    except ValueError:
        return ["no Precedence and environment variables subsection"]
    if _ENV_VAR_ROW not in precedence_text:
        return ["missing SEMLOG_MODE row in the environment-variable table"]
    return []


class ReadmeOpeningParityTests(unittest.TestCase):
    """The first screen of both editions: a one-line summary under the H1,
    a section that says what semlog is before anything else, the two
    differentiators in order, the project's philosophy, and the install
    command. Not tied to a single STANDARDS.md requirement id (same
    precedent as `tests/test_class_budget.py`), so no `Proves:` line:
    DOC-002's parity is proven above, and no requirement governs the order
    in which a README introduces the project."""

    @classmethod
    def setUpClass(cls):
        cls.texts = {language: readme_text(language) for language in READMES}

    def test_both_editions_open_with_summary_differentiators_and_install(self):
        for language, text in self.texts.items():
            with self.subTest(language=language):
                self.assertEqual([], opening_problems(text, language))

    def test_both_editions_say_what_semlog_is_and_explain_the_name(self):
        for language, text in self.texts.items():
            with self.subTest(language=language):
                self.assertEqual([], what_section_problems(text, language))

    def test_both_editions_state_the_philosophy(self):
        for language, text in self.texts.items():
            with self.subTest(language=language):
                self.assertEqual([], philosophy_problems(text, language))


class ReadmeOpeningPerturbationTests(unittest.TestCase):
    """Every check `opening_problems`, `what_section_problems` and
    `philosophy_problems` performs is demonstrated here to be capable of
    failing, against deliberately broken in-memory copies of the real
    README text. The real files are never written to."""

    @classmethod
    def setUpClass(cls):
        cls.english = readme_text("en")
        cls.spanish = readme_text("es")

    def test_baseline_has_no_problems(self):
        self.assertEqual([], opening_problems(self.english, "en"))
        self.assertEqual([], what_section_problems(self.english, "en"))
        self.assertEqual([], philosophy_problems(self.english, "en"))

    def test_a_missing_one_line_summary_is_caught(self):
        summary = next(
            line
            for line in self.english.splitlines()
            if _SUMMARY_LINE_RE.match(line.strip())
        )
        mutated = self.english.replace(summary + "\n", "", 1)
        self.assertNotEqual(mutated, self.english)
        problems = opening_problems(mutated, "en")
        self.assertTrue(any("one-line summary" in p for p in problems), problems)

    def test_installation_before_the_why_section_is_caught(self):
        # The exact regression this check exists for: install instructions
        # are useless to a reader who does not yet know what the project is.
        mutated = self.english.replace("## Installation", "## Aardvark", 1).replace(
            "## Why semlog", "## Installation", 1
        )
        self.assertNotEqual(mutated, self.english)
        problems = opening_problems(mutated, "en")
        self.assertTrue(any("opening sections are" in p for p in problems), problems)

    def test_the_what_section_pushed_below_the_quick_start_is_caught(self):
        # The regression the reordering exists for: the explanation of what
        # semlog does used to sit three sections down, after the reader had
        # already been asked to install it.
        mutated = self.english.replace("## What semlog is", "## Aardvark", 1).replace(
            "## Quick start", "## What semlog is", 1
        )
        self.assertNotEqual(mutated, self.english)
        problems = opening_problems(mutated, "en")
        self.assertTrue(any("opening sections are" in p for p in problems), problems)

    def test_a_deleted_differentiator_token_is_caught(self):
        mutated = self.english.replace("`SEMLOG_MODE=off`", "the mode setting", 1)
        self.assertNotEqual(mutated, self.english)
        self.assertIn(
            "missing differentiator token '`SEMLOG_MODE=off`'",
            opening_problems(mutated, "en"),
        )

    def test_differentiators_in_the_wrong_order_are_caught(self):
        why_text = section(self.english, 2, "Why semlog")
        adoption = next(
            line for line in why_text.splitlines() if "`SEMLOG_MODE=off`" in line
        )
        zero_dependency = next(
            line for line in why_text.splitlines() if "`Requires-Dist`" in line
        )
        swapped = why_text.replace(adoption, "\0", 1).replace(
            zero_dependency, adoption, 1
        )
        swapped = swapped.replace("\0", zero_dependency, 1)
        mutated = self.english.replace(why_text, swapped, 1)
        self.assertNotEqual(mutated, self.english)
        self.assertIn(
            "differentiators are out of order", opening_problems(mutated, "en")
        )

    def test_a_missing_install_command_is_caught(self):
        # `uv pip install semlog` survives this mutation on purpose: it is
        # what makes a plain substring check vacuous here.
        mutated = self.english.replace(
            f"```bash\n{_INSTALL_COMMAND}\n```", "```bash\npip install .\n```", 1
        )
        self.assertNotEqual(mutated, self.english)
        self.assertIn(_INSTALL_COMMAND, mutated)
        self.assertIn(
            f"no `bash` block holding exactly {_INSTALL_COMMAND!r}",
            opening_problems(mutated, "en"),
        )

    def test_deleting_the_what_section_entirely_is_caught(self):
        start = self.english.index("## What semlog is")
        end = self.english.index("## Why semlog")
        mutated = self.english[:start] + self.english[end:]
        self.assertNotEqual(mutated, self.english)
        self.assertEqual(
            ["no what-semlog-is section"], what_section_problems(mutated, "en")
        )

    def test_a_what_section_that_drops_the_specification_is_caught(self):
        mutated = self.english.replace("Semantic Conventions", "conventions")
        self.assertNotEqual(mutated, self.english)
        self.assertIn(
            "missing name-explanation token 'Semantic Conventions'",
            what_section_problems(mutated, "en"),
        )

    def test_name_rationale_mutated_identically_in_both_editions_is_caught(self):
        mutated_en = self.english.replace(_NAME_RATIONALE_PHRASES["en"], "nice name", 1)
        mutated_es = self.spanish.replace(
            _NAME_RATIONALE_PHRASES["es"], "un nombre lindo", 1
        )
        self.assertNotEqual(mutated_en, self.english)
        self.assertNotEqual(mutated_es, self.spanish)
        self.assertIn(
            "missing stable-meaning rationale", what_section_problems(mutated_en, "en")
        )
        self.assertIn(
            "missing stable-meaning rationale", what_section_problems(mutated_es, "es")
        )

    def test_what_it_does_mutated_identically_in_both_editions_is_caught(self):
        mutated_en = self.english.replace(_WHAT_IT_DOES_PHRASES["en"], "is nice", 1)
        mutated_es = self.spanish.replace(_WHAT_IT_DOES_PHRASES["es"], "es lindo", 1)
        self.assertNotEqual(mutated_en, self.english)
        self.assertNotEqual(mutated_es, self.spanish)
        self.assertIn(
            "missing what-semlog-does statement",
            what_section_problems(mutated_en, "en"),
        )
        self.assertIn(
            "missing what-semlog-does statement",
            what_section_problems(mutated_es, "es"),
        )

    def test_deleting_the_philosophy_section_entirely_is_caught(self):
        start = self.english.index("## Philosophy")
        end = self.english.index("## Installation")
        mutated = self.english[:start] + self.english[end:]
        self.assertNotEqual(mutated, self.english)
        self.assertEqual(["no Philosophy section"], philosophy_problems(mutated, "en"))

    def test_a_dropped_philosophy_position_is_caught(self):
        philosophy = section(self.english, 2, "Philosophy")
        bullet = next(
            line
            for line in philosophy.splitlines()
            if line.startswith("- ") and "`Requires-Dist`" in line
        )
        mutated = self.english.replace(bullet + "\n", "", 1)
        self.assertNotEqual(mutated, self.english)
        self.assertIn(
            "missing philosophy token '`Requires-Dist`'",
            philosophy_problems(mutated, "en"),
        )

    def test_a_philosophy_phrase_mutated_identically_in_both_editions_is_caught(self):
        mutated_en = self.english.replace(
            "major version change", "patch version change", 1
        )
        mutated_es = self.spanish.replace(
            "cambio de versión mayor", "cambio de versión de parche", 1
        )
        self.assertNotEqual(mutated_en, self.english)
        self.assertNotEqual(mutated_es, self.spanish)
        self.assertIn(
            "missing philosophy phrase 'major version change'",
            philosophy_problems(mutated_en, "en"),
        )
        self.assertIn(
            "missing philosophy phrase 'cambio de versión mayor'",
            philosophy_problems(mutated_es, "es"),
        )

    def test_a_stale_requirement_count_is_caught(self):
        real = declared_requirement_count()
        for language, replacement in (
            (
                "en",
                (f"declares {real} requirements", f"declares {real - 1} requirements"),
            ),
            ("es", (f"declara {real} requisitos", f"declara {real - 1} requisitos")),
        ):
            original = readme_text(language)
            mutated = original.replace(*replacement, 1)
            with self.subTest(language=language):
                self.assertNotEqual(mutated, original)
                self.assertIn(
                    f"stated requirement count {real - 1} is stale",
                    philosophy_problems(mutated, language),
                )


class ReadmeModesParityTests(unittest.TestCase):
    """DOC-012's four required README topics, in both editions: the three
    modes and their ORDERED resolution precedence (LM-001), the
    `semlog=True` keyword (LM-002), hybrid's suppression scope with its
    `StreamHandler`-subclass-override and `QueueHandler`/`QueueListener`/
    `MemoryHandler` exceptions (LM-004, errata 12, validate-wu69 #338 m2),
    and `off` as the rollback switch, including the uninstall-`TypeError`
    caveat. Every assertion is section-scoped (`modes_narrative_problems`),
    not a whole-file token check (validate-wu69 #338, MAJOR M1).

    Proves: LM-004, DOC-012
    """

    @classmethod
    def setUpClass(cls):
        cls.texts = {language: readme_text(language) for language in READMES}

    def test_both_editions_prove_the_full_modes_narrative(self):
        for language, text in self.texts.items():
            with self.subTest(language=language):
                self.assertEqual([], modes_narrative_problems(text, language))

    def test_both_editions_document_the_semlog_true_keyword(self):
        for language, text in self.texts.items():
            with self.subTest(language=language):
                modes_text = section(text, 2, READMES[language]["modes"])
                self.assertIn("semlog=True", modes_text)

    def test_both_editions_keep_the_semlog_mode_env_var_row(self):
        for language, text in self.texts.items():
            with self.subTest(language=language):
                self.assertEqual([], env_var_table_problems(text, language))


class ReadmeModesPerturbationTests(unittest.TestCase):
    """Every check `modes_narrative_problems` and `env_var_table_problems`
    performs is demonstrated here to be capable of failing, against
    deliberately broken in-memory copies of the real README text
    (validate-wu69 #338, MAJOR M1's required mutation coverage). The real
    files are never written to."""

    @classmethod
    def setUpClass(cls):
        cls.english = readme_text("en")
        cls.spanish = readme_text("es")

    def test_baseline_has_no_problems(self):
        self.assertEqual([], modes_narrative_problems(self.english, "en"))

    def test_deleting_the_modes_section_entirely_is_caught(self):
        start = self.english.index("## Modes")
        end = self.english.index("## Output")
        mutated = self.english[:start] + self.english[end:]
        self.assertNotEqual(mutated, self.english)
        self.assertEqual(["no Modes section"], modes_narrative_problems(mutated, "en"))

    def test_reordered_precedence_is_caught(self):
        mutated = self.english.replace(
            "1. the `mode` keyword argument to `configure()`;\n"
            "2. the `SEMLOG_MODE` environment variable;\n",
            "1. the `SEMLOG_MODE` environment variable;\n"
            "2. the `mode` keyword argument to `configure()`;\n",
            1,
        )
        self.assertNotEqual(mutated, self.english)
        self.assertIn(
            "precedence sequence is out of order",
            modes_narrative_problems(mutated, "en"),
        )

    def test_deleting_a_mode_value_bullet_is_caught(self):
        mutated = self.english.replace(
            "- **`off`**: `configure()` installs nothing and does not touch "
            "the root logger. `semlog=True` still never raises, but produces "
            "no JSON output and no other side effect of its own; "
            "`operation()`, `bind()` and both middlewares keep working as "
            "inert pass-throughs.\n",
            "",
            1,
        )
        self.assertNotEqual(mutated, self.english)
        self.assertIn(
            f"missing mode-explanation bullet {'- **`off`**:'!r}",
            modes_narrative_problems(mutated, "en"),
        )

    def test_valueerror_changed_to_typeerror_is_caught(self):
        # Every `ValueError` occurrence is mutated (not just the first),
        # so this stays robust even if a second, legitimate `ValueError`
        # mention is ever added inside the Modes section span
        # (validate-wu69 #339, NIT 1: asserting on the checker's own
        # output, per a `str.replace()` of only the first occurrence, is
        # brittle against a second real mention elsewhere in the span).
        mutated = self.english.replace("ValueError", "TypeError")
        self.assertNotEqual(mutated, self.english)
        self.assertIn("missing ValueError", modes_narrative_problems(mutated, "en"))

    def test_removing_the_streamhandler_limitation_bullet_is_caught(self):
        mutated = re.sub(
            r"(?m)^- \*\*Hybrid's suppression is scoped.*\n", "", self.english
        )
        self.assertNotEqual(mutated, self.english)
        problems = modes_narrative_problems(mutated, "en")
        self.assertTrue(any("StreamHandler" in p for p in problems))
        self.assertTrue(any("super()" in p for p in problems))

    def test_removing_the_off_limitation_bullet_is_caught(self):
        mutated = re.sub(r"(?m)^- \*\*`off` is a process-start.*\n", "", self.english)
        self.assertNotEqual(mutated, self.english)
        self.assertIn(
            "missing off-as-process-start-switch bullet",
            modes_narrative_problems(mutated, "en"),
        )

    def test_removing_the_typeerror_limitation_bullet_is_caught(self):
        mutated = re.sub(r"(?m)^- \*\*Uninstalling semlog.*\n", "", self.english)
        self.assertNotEqual(mutated, self.english)
        self.assertIn(
            "missing TypeError bullet", modes_narrative_problems(mutated, "en")
        )

    # -- validate-wu69 #339, MINOR n3: each of the following mutations is
    # applied IDENTICALLY to both editions, since parity alone
    # (`test_code_blocks_are_byte_identical` and the heading/badge/table
    # checks) only compares one edition against the other and stays green
    # when the same content-breaking edit lands in both. --

    def test_toml_example_mutated_identically_in_both_editions_is_caught(self):
        mutated_en = self.english.replace(_TOML_EXAMPLE, _TOML_EXAMPLE_MUTATED, 1)
        mutated_es = self.spanish.replace(_TOML_EXAMPLE, _TOML_EXAMPLE_MUTATED, 1)
        self.assertNotEqual(mutated_en, self.english)
        self.assertNotEqual(mutated_es, self.spanish)
        self.assertIn(
            "TOML example missing or altered",
            modes_narrative_problems(mutated_en, "en"),
        )
        self.assertIn(
            "TOML example missing or altered",
            modes_narrative_problems(mutated_es, "es"),
        )

    def test_tomllib_caveat_version_mutated_identically_in_both_editions_is_caught(
        self,
    ):
        mutated_en = self.english.replace(
            "available on Python 3.11 and later",
            "available on Python 3.10 and later",
            1,
        )
        mutated_es = self.spanish.replace(
            "disponible desde Python 3.11 en adelante",
            "disponible desde Python 3.10 en adelante",
            1,
        )
        self.assertNotEqual(mutated_en, self.english)
        self.assertNotEqual(mutated_es, self.spanish)
        self.assertIn(
            "missing tomllib version caveat",
            modes_narrative_problems(mutated_en, "en"),
        )
        self.assertIn(
            "missing tomllib version caveat",
            modes_narrative_problems(mutated_es, "es"),
        )

    def test_upward_search_phrase_mutated_identically_in_both_editions_is_caught(self):
        mutated_en = self.english.replace(
            "upward through its parent directories",
            "downward through its subdirectories",
            1,
        )
        mutated_es = self.spanish.replace(
            "hacia arriba por sus directorios padres",
            "hacia abajo por sus subdirectorios",
            1,
        )
        self.assertNotEqual(mutated_en, self.english)
        self.assertNotEqual(mutated_es, self.spanish)
        self.assertIn(
            "missing upward-search-through-parents phrase",
            modes_narrative_problems(mutated_en, "en"),
        )
        self.assertIn(
            "missing upward-search-through-parents phrase",
            modes_narrative_problems(mutated_es, "es"),
        )

    def test_precedence_default_mutated_identically_in_both_editions_is_caught(self):
        mutated_en = self.english.replace(
            '4. the default, `"full"`.', '4. the default, `"hybrid"`.', 1
        )
        mutated_es = self.spanish.replace(
            '4. el valor por defecto, `"full"`.',
            '4. el valor por defecto, `"hybrid"`.',
            1,
        )
        self.assertNotEqual(mutated_en, self.english)
        self.assertNotEqual(mutated_es, self.spanish)
        self.assertIn(
            'missing precedence item 4 default `"full"`',
            modes_narrative_problems(mutated_en, "en"),
        )
        self.assertIn(
            'missing precedence item 4 default `"full"`',
            modes_narrative_problems(mutated_es, "es"),
        )

    def test_hybrid_then_full_order_mutated_identically_in_both_editions_is_caught(
        self,
    ):
        mutated_en = self.english.replace(
            "`hybrid`, then `full`", "`full`, then `hybrid`", 1
        )
        mutated_es = self.spanish.replace(
            "`hybrid`, y luego `full`", "`full`, y luego `hybrid`", 1
        )
        self.assertNotEqual(mutated_en, self.english)
        self.assertNotEqual(mutated_es, self.spanish)
        self.assertIn(
            "hybrid-then-full adoption order missing or reversed",
            modes_narrative_problems(mutated_en, "en"),
        )
        self.assertIn(
            "hybrid-then-full adoption order missing or reversed",
            modes_narrative_problems(mutated_es, "es"),
        )

    def test_semlog_mode_env_var_row_deleted_in_both_editions_is_caught(self):
        mutated_en = self.english.replace(
            "| `SEMLOG_MODE` | `mode` (see [Modes](#modes)) |\n", "", 1
        )
        mutated_es = self.spanish.replace(
            "| `SEMLOG_MODE` | `mode` (ver [Modos](#modos)) |\n", "", 1
        )
        self.assertNotEqual(mutated_en, self.english)
        self.assertNotEqual(mutated_es, self.spanish)
        self.assertEqual(
            ["missing SEMLOG_MODE row in the environment-variable table"],
            env_var_table_problems(mutated_en, "en"),
        )
        self.assertEqual(
            ["missing SEMLOG_MODE row in the environment-variable table"],
            env_var_table_problems(mutated_es, "es"),
        )


_FRAMEWORK_RECIPES_TITLES = {
    "en": "Framework recipes",
    "es": "Recetas para frameworks",
}
_DJANGO_PLACEMENT_MARKERS = {
    "en": (
        "outermost",
        "closer to the view",
        "the default recommendation",
        "covers every other middleware's own logging",
        "maximizes exception-capture priority",
    ),
    "es": (
        "más externa",
        "más cerca de la vista",
        "la recomendación por defecto",
        "cubre el logging propio de cualquier otro middleware",
        "maximiza la prioridad de captura de excepciones",
    ),
}
_DJANGO_LIMITATION_MARKER_ES_1 = (
    "no puede observar una excepción lanzada por el propio código de otro middleware"
)
_DJANGO_LIMITATION_MARKER_ES_2 = "anticipándose por completo al propio hook de semlog"
_DJANGO_LIMITATION_MARKERS = {
    "en": (
        "cannot observe an exception raised by another middleware",
        "pre-empting semlog's own hook",
        "neither a defect",
    ),
    "es": (
        _DJANGO_LIMITATION_MARKER_ES_1,
        _DJANGO_LIMITATION_MARKER_ES_2,
        "ninguna es un defecto",
    ),
}
_DJANGO_COEXISTENCE_MARKERS = {
    "en": ("is unsupported",),
    "es": ("no está soportado",),
}


def framework_recipes_narrative_problems(text, language):
    """Every way `text`'s "## Framework recipes"/"## Recetas para
    frameworks" section fails to prove HTM-009's placement/tradeoff
    narrative, HTM-010's unsupported-coexistence note, HTM-011/HTM-012's
    process_exception behavior and permanent limitations, and the
    FastAPI `add_middleware` recipe's exception-handler caveat (empty
    when it proves everything). Scoped to the section's own span, never
    a whole-document check (matching `modes_narrative_problems`'s own
    established pattern)."""
    try:
        recipes_text = section(text, 2, _FRAMEWORK_RECIPES_TITLES[language])
    except ValueError:
        return ["no Framework recipes section"]

    problems = []
    try:
        fastapi_text = section(recipes_text, 3, "FastAPI")
    except ValueError:
        problems.append("no FastAPI subsection")
        fastapi_text = ""
    if "add_middleware" not in fastapi_text:
        problems.append("missing add_middleware recipe")
    if "ServerErrorMiddleware" not in fastapi_text:
        problems.append(
            "missing exception-handler caveat's ServerErrorMiddleware mention"
        )
    if "trace_id" not in fastapi_text:
        problems.append("missing the no-trace_id exception-handler caveat")

    try:
        django_text = section(recipes_text, 3, "Django")
    except ValueError:
        problems.append("no Django subsection")
        return problems

    if '"semlog.DjangoMiddleware"' not in django_text:
        problems.append("missing the settings.MIDDLEWARE recipe")
    if "AppConfig" not in django_text or "ready(self)" not in django_text:
        problems.append("missing the AppConfig.ready() configure() placement")
    for marker in _DJANGO_PLACEMENT_MARKERS[language]:
        if marker not in django_text:
            problems.append(f"missing placement marker {marker!r}")
    if "process_exception" not in django_text:
        problems.append("missing process_exception mention")
    for marker in _DJANGO_LIMITATION_MARKERS[language]:
        if marker not in django_text:
            problems.append(f"missing permanent-limitation marker {marker!r}")
    for marker in _DJANGO_COEXISTENCE_MARKERS[language]:
        if marker not in django_text:
            problems.append(f"missing coexistence marker {marker!r}")
    if "SEMLOG_LOG_REQUESTS" not in django_text:
        problems.append("missing SEMLOG_LOG_REQUESTS reference")

    return problems


def semlog_log_requests_section_problems(text, language):
    """Every way `text` fails to carry the new "### SEMLOG_LOG_REQUESTS"
    subsection under Configuration (empty when it proves everything)."""
    labels = READMES[language]
    try:
        configuration_text = section(text, 2, labels["configuration"])
    except ValueError:
        return ["no Configuration section"]
    try:
        setting_text = section(configuration_text, 3, "SEMLOG_LOG_REQUESTS")
    except ValueError:
        return ["no SEMLOG_LOG_REQUESTS subsection"]
    if "SEMLOG_LOG_REQUESTS = True" not in setting_text:
        return ["missing the setting's own code fence"]
    return []


class ReadmeFrameworkRecipesParityTests(unittest.TestCase):
    """The Django recipe's outermost-placement recommendation and tradeoff
    (HTM-009), the unsupported WSGI/ASGI coexistence note (HTM-010), the
    process_exception behavior and its permanent limitations (HTM-011,
    HTM-012), and the FastAPI `add_middleware` recipe with its exception-
    handler caveat, in both editions, plus the `SEMLOG_LOG_REQUESTS`
    subsection under Configuration. Every assertion is section-scoped
    (`framework_recipes_narrative_problems`), never a whole-file check.

    Proves: DOC-012, HTM-009
    """

    @classmethod
    def setUpClass(cls):
        cls.texts = {language: readme_text(language) for language in READMES}

    def test_both_editions_prove_the_framework_recipes_narrative(self):
        for language, text in self.texts.items():
            with self.subTest(language=language):
                self.assertEqual(
                    [], framework_recipes_narrative_problems(text, language)
                )

    def test_both_editions_document_semlog_log_requests(self):
        for language, text in self.texts.items():
            with self.subTest(language=language):
                self.assertEqual(
                    [], semlog_log_requests_section_problems(text, language)
                )


class ReadmeFrameworkRecipesPerturbationTests(unittest.TestCase):
    """Every check `framework_recipes_narrative_problems` and
    `semlog_log_requests_section_problems` performs is demonstrated here
    to be capable of failing, against deliberately broken in-memory
    copies of the real README text. The real files are never written to."""

    @classmethod
    def setUpClass(cls):
        cls.english = readme_text("en")
        cls.spanish = readme_text("es")

    def test_baseline_has_no_problems(self):
        self.assertEqual([], framework_recipes_narrative_problems(self.english, "en"))
        self.assertEqual([], semlog_log_requests_section_problems(self.english, "en"))

    def test_removing_add_middleware_recipe_is_caught(self):
        mutated = self.english.replace(
            "app.add_middleware(semlog.ASGIMiddleware, log_requests=True)", "", 1
        )
        self.assertNotEqual(mutated, self.english)
        self.assertIn(
            "missing add_middleware recipe",
            framework_recipes_narrative_problems(mutated, "en"),
        )

    def test_removing_the_exception_handler_caveat_is_caught(self):
        mutated = self.english.replace(
            "# A global @app.exception_handler(Exception) runs in Starlette's outermost\n"
            "# ServerErrorMiddleware, outside semlog's own bound context: its logs carry\n"
            "# no trace_id, and the ERROR completion event has no status code. Wrap\n"
            "# `app` instead (above) to keep exception handling inside semlog's context.\n",
            "",
            1,
        )
        self.assertNotEqual(mutated, self.english)
        problems = framework_recipes_narrative_problems(mutated, "en")
        self.assertTrue(any("ServerErrorMiddleware" in p for p in problems))

    def test_removing_the_django_middleware_list_entry_is_caught(self):
        mutated = self.english.replace('"semlog.DjangoMiddleware"', "", 1)
        self.assertNotEqual(mutated, self.english)
        self.assertIn(
            "missing the settings.MIDDLEWARE recipe",
            framework_recipes_narrative_problems(mutated, "en"),
        )

    def test_removing_process_exception_mentions_is_caught(self):
        mutated = self.english.replace("process_exception", "the exception hook")
        self.assertNotEqual(mutated, self.english)
        self.assertIn(
            "missing process_exception mention",
            framework_recipes_narrative_problems(mutated, "en"),
        )

    def test_removing_the_coexistence_note_is_caught(self):
        mutated = self.english.replace(
            "wrapping the same application is unsupported", "is a valid combination"
        )
        self.assertNotEqual(mutated, self.english)
        problems = framework_recipes_narrative_problems(mutated, "en")
        self.assertTrue(any("coexistence marker" in p for p in problems))

    def test_removing_the_semlog_log_requests_subsection_is_caught(self):
        start = self.english.index("### SEMLOG_LOG_REQUESTS")
        end = self.english.index("## Modes")
        mutated = self.english[:start] + self.english[end:]
        self.assertNotEqual(mutated, self.english)
        self.assertEqual(
            ["no SEMLOG_LOG_REQUESTS subsection"],
            semlog_log_requests_section_problems(mutated, "en"),
        )

    def test_deleting_the_framework_recipes_section_entirely_is_caught(self):
        start = self.english.index("## Framework recipes")
        end = self.english.index("## Configuration")
        mutated = self.english[:start] + self.english[end:]
        self.assertNotEqual(mutated, self.english)
        self.assertEqual(
            ["no Framework recipes section"],
            framework_recipes_narrative_problems(mutated, "en"),
        )

    def test_removing_the_second_limitation_and_no_defect_framing_is_caught(self):
        mutated = self.english.replace(
            "Two permanent limitations, neither a defect: it cannot observe "
            "an exception raised by another middleware's own code, since "
            "only a view exception ever reaches it, and a competing "
            "`process_exception` registered closer to the view can return "
            "a response first, pre-empting semlog's own hook for that "
            "request entirely.",
            "It cannot observe an exception raised by another middleware's "
            "own code, since only a view exception ever reaches it.",
            1,
        )
        self.assertNotEqual(mutated, self.english)
        problems = framework_recipes_narrative_problems(mutated, "en")
        self.assertTrue(
            any("pre-empting semlog's own hook" in p for p in problems), problems
        )
        self.assertTrue(any("neither a defect" in p for p in problems), problems)

    def test_removing_the_default_recommendation_wording_is_caught(self):
        # MINOR-A (round 2): the OLD marker set ("outermost", "closer to
        # the view") stayed green even when the whole tradeoff paragraph
        # was rewritten to say placement makes no difference at all,
        # since "closer to the view" also survives inside HTM-012's own
        # limitation sentence. This mutation keeps both old markers
        # present while deleting only the default-recommendation phrase.
        mutated = self.english.replace("the default recommendation", "", 1)
        self.assertNotEqual(mutated, self.english)
        problems = framework_recipes_narrative_problems(mutated, "en")
        self.assertTrue(
            any("the default recommendation" in p for p in problems), problems
        )

    def test_removing_the_outermost_direction_tradeoff_wording_is_caught(self):
        mutated = self.english.replace(
            "covers every other middleware's own logging", "", 1
        )
        self.assertNotEqual(mutated, self.english)
        problems = framework_recipes_narrative_problems(mutated, "en")
        self.assertTrue(
            any("covers every other middleware's own logging" in p for p in problems),
            problems,
        )

    def test_removing_the_closer_to_view_direction_tradeoff_wording_is_caught(self):
        mutated = self.english.replace("maximizes exception-capture priority", "", 1)
        self.assertNotEqual(mutated, self.english)
        problems = framework_recipes_narrative_problems(mutated, "en")
        self.assertTrue(
            any("maximizes exception-capture priority" in p for p in problems),
            problems,
        )

    def test_no_difference_rewrite_is_caught(self):
        # The exact round-2 finding, reproduced directly: rewriting the
        # whole tradeoff paragraph to claim placement makes no difference
        # must not stay green.
        start = self.english.index("Placed outermost (first in")
        end = self.english.index("`process_exception` stashes")
        mutated = (
            self.english[:start]
            + "Put it wherever you like: outermost or closer to the view, "
            "it makes no difference at all.\n\n" + self.english[end:]
        )
        self.assertNotEqual(mutated, self.english)
        problems = framework_recipes_narrative_problems(mutated, "en")
        self.assertTrue(problems, "the no-difference rewrite must be caught")


if __name__ == "__main__":
    unittest.main()
