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


class ReadmeModesParityTests(unittest.TestCase):
    """LM-004 (errata 12): both README editions must document hybrid mode's
    suppression limitation, naming `StreamHandler` as the suppression
    scope and both `QueueHandler`/`QueueListener` and `MemoryHandler` as
    handlers that may still render a marked record as text; this is one
    of DOC-012's four required README topics. The mode/precedence/keyword
    narrative itself is a later documentation work unit (WU7); this class
    covers only the limitation text landing now.

    Proves: LM-004, DOC-012
    """

    @classmethod
    def setUpClass(cls):
        cls.texts = {language: readme_text(language) for language in READMES}

    def test_both_editions_document_the_streamhandler_only_scope(self):
        for language, text in self.texts.items():
            with self.subTest(language=language):
                self.assertIn("StreamHandler", text)

    def test_both_editions_name_queuehandler_and_memoryhandler_as_exceptions(self):
        for language, text in self.texts.items():
            with self.subTest(language=language):
                self.assertIn("QueueHandler", text)
                self.assertIn("QueueListener", text)
                self.assertIn("MemoryHandler", text)


if __name__ == "__main__":
    unittest.main()
