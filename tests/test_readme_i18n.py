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


_OPENING_H2_KEYS = ("why", "installation", "name", "quick_start")
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
# The name explanation: the two word parts, and the specification the name
# refers to. All four stay untranslated in the Spanish edition, being code
# spans and a proper noun.
_NAME_TOKENS = ("`semantic`", "`log`", "OpenTelemetry", "Semantic Conventions")
# The one prose fact of the name section that IS translated, so it is looked
# up per language (same precedent as `_SEARCH_DIRECTION_PHRASES` below): a
# mutation applied IDENTICALLY to both editions defeats cross-file parity
# alone, since the parity checks only compare one edition against the other.
_NAME_RATIONALE_PHRASES = {
    "en": "stable name and a stable meaning",
    "es": "nombre y un significado estables",
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
    configuration table to learn what the project does has already left.
    So the H1 is followed immediately by a one-line summary, the first
    four level-two sections are the "why", the install instructions, the
    name explanation and the quick start, and the "why" section states the
    two differentiators in a fixed order. Scoped to the relevant section
    span via `section()`, never a whole-document `in` check, so an
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


def name_section_problems(text, language):
    """Every way a README's name section fails to explain where the name
    comes from and why that matters (empty when it explains both)."""
    labels = READMES[language]
    try:
        name_text = section(text, 2, labels["name"])
    except ValueError:
        return ["no name section"]
    problems = [
        f"missing name-explanation token {token!r}"
        for token in _NAME_TOKENS
        if token not in name_text
    ]
    if _NAME_RATIONALE_PHRASES[language] not in name_text:
        problems.append("missing stable-meaning rationale")
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
    the two differentiators in order, and the install command, followed by
    the section that explains where the name comes from. Not tied to a
    single STANDARDS.md requirement id (same precedent as
    `tests/test_class_budget.py`), so no `Proves:` line: DOC-002's parity
    is proven above, and no requirement governs the order in which a
    README introduces the project."""

    @classmethod
    def setUpClass(cls):
        cls.texts = {language: readme_text(language) for language in READMES}

    def test_both_editions_open_with_summary_differentiators_and_install(self):
        for language, text in self.texts.items():
            with self.subTest(language=language):
                self.assertEqual([], opening_problems(text, language))

    def test_both_editions_explain_the_name(self):
        for language, text in self.texts.items():
            with self.subTest(language=language):
                self.assertEqual([], name_section_problems(text, language))


class ReadmeOpeningPerturbationTests(unittest.TestCase):
    """Every check `opening_problems` and `name_section_problems` performs
    is demonstrated here to be capable of failing, against deliberately
    broken in-memory copies of the real README text. The real files are
    never written to."""

    @classmethod
    def setUpClass(cls):
        cls.english = readme_text("en")
        cls.spanish = readme_text("es")

    def test_baseline_has_no_problems(self):
        self.assertEqual([], opening_problems(self.english, "en"))
        self.assertEqual([], name_section_problems(self.english, "en"))

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

    def test_deleting_the_name_section_entirely_is_caught(self):
        start = self.english.index("## What the name means")
        end = self.english.index("## Quick start")
        mutated = self.english[:start] + self.english[end:]
        self.assertNotEqual(mutated, self.english)
        self.assertEqual(["no name section"], name_section_problems(mutated, "en"))

    def test_a_name_section_that_drops_the_specification_is_caught(self):
        mutated = self.english.replace("Semantic Conventions", "conventions")
        self.assertNotEqual(mutated, self.english)
        self.assertIn(
            "missing name-explanation token 'Semantic Conventions'",
            name_section_problems(mutated, "en"),
        )

    def test_name_rationale_mutated_identically_in_both_editions_is_caught(self):
        mutated_en = self.english.replace(_NAME_RATIONALE_PHRASES["en"], "nice name", 1)
        mutated_es = self.spanish.replace(
            _NAME_RATIONALE_PHRASES["es"], "un nombre lindo", 1
        )
        self.assertNotEqual(mutated_en, self.english)
        self.assertNotEqual(mutated_es, self.spanish)
        self.assertIn(
            "missing stable-meaning rationale", name_section_problems(mutated_en, "en")
        )
        self.assertIn(
            "missing stable-meaning rationale", name_section_problems(mutated_es, "es")
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


if __name__ == "__main__":
    unittest.main()
