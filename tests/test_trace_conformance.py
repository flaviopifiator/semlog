"""W3C Trace Context Level 1 conformance suite (task 4.8; STANDARDS §8,
TCP-001..003; design #162 §9: "table-driven; cases ported from the spec's
own parsing rules as data").

Distinct from `tests/test_trace.py`'s hand-picked unit tests: this module
enumerates, in one table, every documented valid/invalid `traceparent`
shape the W3C Trace Context spec and STANDARDS.md's TCP-001..003 describe,
so a future parsing regression shows up as one failing table row instead of
a scattered set of individual tests. No new production behavior is
expected or required by this suite; it is an additive conformance guard.
"""

from __future__ import annotations

import unittest

from semlog._trace import parse_traceparent

_VALID = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
_PARENT_ID = "00f067aa0ba902b7"

# (case name, header, expected (trace_id, parent_id, trace_flags) or None)
CASES = [
    ("valid_canonical", _VALID, (_TRACE_ID, _PARENT_ID, "01")),
    (
        "version_ff_rejected",
        "ff-" + _TRACE_ID + "-" + _PARENT_ID + "-01",
        None,
    ),
    (
        "uppercase_trace_id_rejected",
        "00-" + _TRACE_ID.upper() + "-" + _PARENT_ID + "-01",
        None,
    ),
    (
        "uppercase_parent_id_rejected",
        "00-" + _TRACE_ID + "-" + _PARENT_ID.upper() + "-01",
        None,
    ),
    (
        "uppercase_version_rejected",
        "0A-" + _TRACE_ID + "-" + _PARENT_ID + "-01",
        None,
    ),
    ("all_zero_trace_id_rejected", "00-" + "0" * 32 + "-" + _PARENT_ID + "-01", None),
    ("all_zero_parent_id_rejected", "00-" + _TRACE_ID + "-" + "0" * 16 + "-01", None),
    (
        "trace_id_too_short_rejected",
        "00-" + _TRACE_ID[:-1] + "-" + _PARENT_ID + "-01",
        None,
    ),
    (
        "trace_id_too_long_rejected",
        "00-" + _TRACE_ID + "6-" + _PARENT_ID + "-01",
        None,
    ),
    (
        "parent_id_too_short_rejected",
        "00-" + _TRACE_ID + "-" + _PARENT_ID[:-1] + "-01",
        None,
    ),
    (
        "parent_id_too_long_rejected",
        "00-" + _TRACE_ID + "-" + _PARENT_ID + "1-01",
        None,
    ),
    (
        "non_hex_character_in_trace_id_rejected",
        "00-" + _TRACE_ID[:-1] + "g-" + _PARENT_ID + "-01",
        None,
    ),
    ("missing_trailing_field_rejected", "00-" + _TRACE_ID + "-" + _PARENT_ID, None),
    ("empty_string_rejected", "", None),
    ("whitespace_only_rejected", "   ", None),
    ("none_rejected", None, None),
    ("comma_joined_repeated_header_rejected", _VALID + "," + _VALID, None),
    (
        "future_version_with_trailing_field_parsed_ignoring_trailing",
        "01-" + _TRACE_ID + "-" + _PARENT_ID + "-01-extra-field",
        (_TRACE_ID, _PARENT_ID, "01"),
    ),
    ("version_00_with_trailing_field_rejected", _VALID + "-extra", None),
    (
        "future_version_fe_with_trailing_field_parsed",
        "fe-" + _TRACE_ID + "-" + _PARENT_ID + "-01-extra",
        (_TRACE_ID, _PARENT_ID, "01"),
    ),
]


class TraceContextConformanceTableTests(unittest.TestCase):
    """Proves: TCP-001, TCP-002, TCP-003"""

    def test_conformance_table(self):
        for name, header, expected in CASES:
            with self.subTest(case=name):
                self.assertEqual(expected, parse_traceparent(header))


if __name__ == "__main__":
    unittest.main()
