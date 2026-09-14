"""Compliance-annex conformance (task 4.30; STANDARDS.md CMA-001, CMA-002,
CMA-003; design #223 §4/§10, P3-ADR-6). Parses `## Annex B` in STANDARDS.md
with `re` only -- this module never imports STANDARDS.md as code, since it
is prose, not a test source.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STANDARDS_PATH = REPO_ROOT / "STANDARDS.md"

ANNEX_B_HEADER = (
    "| Control | What it requires (paraphrase) | SEMLOG contributes "
    "| The adopting organization must |"
)
DISCLAIMER = (
    "This annex is informative. Using SEMLOG does not make an organization "
    "compliant with any control or standard in this table, and SEMLOG holds "
    'no certification. The "SEMLOG contributes" column only lists '
    "requirements of this document that have a conformance test; "
    "compliance depends on the adopting organization."
)
REQUIRED_CONTROL_SUBSTRINGS = (
    "ASVS 5.0.0 16.1.1",
    "ASVS 16.2.1",
    "ASVS 16.2.2",
    "ASVS 16.2.4",
    "ASVS 16.2.5",
    "ASVS 16.4.1",
    "16.2.3",  # the combined "none supported" row
    "ISO/IEC 27001:2022 A.8.15",
    "ISO/IEC 27001:2022 A.8.17",
    "NIST SP 800-53r5 AU-3",
    "NIST SP 800-53r5 AU-8",
    "PCI DSS v4.0.1 10.2.2",
    "PCI DSS v4.0.1 10.6",
    "CWE-117",
    "CWE-532",
    "CWE-778",
    "OWASP Logging Cheat Sheet",
    "OWASP Top 10:2025 A09:2025",
)
MAX_PARAPHRASE_LENGTH = 200


def parse_annex_b(text):
    """Return `(header_line, disclaimer_line, rows)`: `rows` is a list of
    `(control_cell, requires_cell, contributes_cell, must_cell)` tuples for
    every real Annex B data row (excluding the header and the `|---|` separator).
    """
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("## Annex B"))
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## ") or lines[i].startswith("# "):
            end = i
            break
    section = lines[start:end]

    header_idx = next(
        i for i, line in enumerate(section) if line.strip() == ANNEX_B_HEADER
    )
    disclaimer_line = None
    for line in reversed(section[:header_idx]):
        stripped = line.strip()
        if stripped:
            disclaimer_line = stripped
            break

    rows = []
    for line in section[header_idx + 1 :]:
        stripped = line.strip()
        if stripped.startswith("|---"):
            continue
        if not stripped.startswith("|"):
            break
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) == 4:
            rows.append(tuple(cells))
    return section[header_idx].strip(), disclaimer_line, rows


class ComplianceAnnexTests(unittest.TestCase):
    """Proves: CMA-001, CMA-002, CMA-003"""

    @classmethod
    def setUpClass(cls):
        cls.text = STANDARDS_PATH.read_text(encoding="utf-8")
        cls.header, cls.disclaimer, cls.rows = parse_annex_b(cls.text)

    def test_header_is_exact(self):
        self.assertEqual(ANNEX_B_HEADER, self.header)

    def test_disclaimer_present_immediately_before_the_table(self):
        self.assertEqual(DISCLAIMER, self.disclaimer)

    def test_all_18_required_control_rows_present(self):
        control_cells = " || ".join(row[0] for row in self.rows)
        missing = [s for s in REQUIRED_CONTROL_SUBSTRINGS if s not in control_cells]
        self.assertEqual([], missing)
        self.assertEqual(18, len(self.rows), "expected exactly 18 control rows")

    def test_no_row_mentions_soc_2_or_cc7_2(self):
        offenders = [
            row
            for row in self.rows
            if re.search(r"SOC\s*2|CC7\.2", " ".join(row), re.IGNORECASE)
        ]
        self.assertEqual([], offenders)

    def test_soc2_exclusion_note_exists_outside_the_table(self):
        self.assertIn(
            "SOC 2 (criterion CC7.2) is not included: its text could not be "
            "verified against the primary source.",
            self.text,
        )

    def test_iso_and_pci_rows_carry_unverified_no_quotes_under_200_chars(self):
        offenders = []
        for control, paraphrase, _contributes, _must in self.rows:
            if not re.search(r"\bISO\b|\bPCI\b", control):
                continue
            if "(unverified)" not in control:
                offenders.append(f"{control}: missing '(unverified)'")
            if '"' in control or '"' in paraphrase:
                offenders.append(f"{control}: contains a quotation mark")
            if len(paraphrase) >= MAX_PARAPHRASE_LENGTH:
                offenders.append(
                    f"{control}: paraphrase is {len(paraphrase)} chars (>= 200)"
                )
        self.assertEqual([], offenders)

    def test_every_supports_cell_cites_only_declared_requirement_ids(self):
        """CMA-004 is proven by `tests/test_traceability.py`'s
        `test_annex_b_claims_only_declared_ids`; this is a lightweight,
        non-`Proves`-tagged re-affirmation at the compliance-annex layer
        that every "SEMLOG contributes" cell that is not the literal "none"
        spells at least one requirement-ID-shaped token."""
        id_re = re.compile(r"[A-Z]{2,4}-[0-9]{3}")
        for control, _paraphrase, contributes, _must in self.rows:
            with self.subTest(control=control):
                if contributes.strip().lower() == "none":
                    continue
                self.assertRegex(contributes, id_re)


class ComplianceAnnexParserSelfTests(unittest.TestCase):
    """Fixture self-tests over crafted strings, never the real file --
    proves the parser and each check can actually fail."""

    def _fixture(self, control_row):
        return (
            "## Annex B: audit control mapping (informative)\n\n"
            f"{DISCLAIMER}\n\n"
            f"{ANNEX_B_HEADER}\n"
            "|---|---|---|---|\n"
            f"{control_row}\n"
        )

    def test_missing_disclaimer_is_detected(self):
        text = (
            "## Annex B: audit control mapping (informative)\n\n"
            "A filler text that is not the exact disclaimer.\n\n"
            f"{ANNEX_B_HEADER}\n"
            "|---|---|---|---|\n"
            "| ISO/IEC 27001:2022 A.8.15 (unverified) | x | LRC-001 | y |\n"
        )
        _header, disclaimer, _rows = parse_annex_b(text)
        self.assertNotEqual(DISCLAIMER, disclaimer)

    def test_soc2_row_is_caught(self):
        text = self._fixture("| SOC 2 (criterion CC7.2) | x | none | y |")
        _header, _disclaimer, rows = parse_annex_b(text)
        offenders = [row for row in rows if re.search(r"SOC\s*2|CC7\.2", " ".join(row))]
        self.assertEqual(1, len(offenders))

    def test_iso_row_missing_unverified_is_caught(self):
        text = self._fixture("| ISO/IEC 27001:2022 A.8.15 | x | LRC-001 | y |")
        _header, _disclaimer, rows = parse_annex_b(text)
        control = rows[0][0]
        self.assertNotIn("(unverified)", control)

    def test_overlong_paraphrase_is_caught(self):
        long_paraphrase = "x" * 250
        text = self._fixture(
            f"| ISO/IEC 27001:2022 A.8.15 (unverified) | {long_paraphrase} "
            "| LRC-001 | y |"
        )
        _header, _disclaimer, rows = parse_annex_b(text)
        self.assertGreaterEqual(len(rows[0][1]), 200)


if __name__ == "__main__":
    unittest.main()
