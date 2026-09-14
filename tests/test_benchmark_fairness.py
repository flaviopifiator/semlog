"""Fairness-check tests (task 5.1; STANDARDS.md CP-005: "todos los sujetos
se configuran para emitir las mismas claves y valores ante la misma
llamada, verificado campo por campo antes de medir tiempos"). Proves the
check genuinely compares call-site content across subjects, not just that
it imports."""

from __future__ import annotations

import io
import json
import unittest

from benchmark import fairness, subjects


def _emit_and_parse(make_subject, **call_kwargs):
    buffer = io.BytesIO()
    emit, shutdown = make_subject(buffer)
    try:
        emit(**call_kwargs)
    finally:
        shutdown()
    line = buffer.getvalue().decode("utf-8").splitlines()[0]
    return json.loads(line)


class FairnessCheckTests(unittest.TestCase):
    def test_semlog_and_stdlib_baseline_are_fair_for_the_same_call(self):
        call = {"user_id": "u-42", "request_id": "r-7", "duration_ms": 3.25}
        raw_records = {
            "semlog": _emit_and_parse(subjects.make_semlog, **call),
            "stdlib_baseline": _emit_and_parse(subjects.make_stdlib_baseline, **call),
        }
        problems = fairness.check_fairness(raw_records)
        self.assertEqual([], problems, problems)

    def test_fairness_check_reports_a_real_discrepancy(self):
        # Triangulation: a deliberately mismatched pair (different
        # duration_ms) must be caught, proving the check compares actual
        # values, not just field presence.
        call_a = {"user_id": "u-1", "request_id": "r-1", "duration_ms": 1.0}
        call_b = {"user_id": "u-1", "request_id": "r-1", "duration_ms": 2.0}
        raw_records = {
            "semlog": _emit_and_parse(subjects.make_semlog, **call_a),
            "stdlib_baseline": _emit_and_parse(subjects.make_stdlib_baseline, **call_b),
        }
        problems = fairness.check_fairness(raw_records)
        self.assertNotEqual([], problems)

    def test_fairness_check_flags_a_missing_canonical_field(self):
        raw_records = {
            "semlog": {"event_name": "x", "app.user_id": "u", "app.request_id": "r"},
            "stdlib_baseline": {
                "event": "x",
                "user_id": "u",
                "request_id": "r",
                "duration_ms": None,
            },
        }
        problems = fairness.check_fairness(raw_records)
        self.assertTrue(any("duration_ms" in problem for problem in problems), problems)


if __name__ == "__main__":
    unittest.main()
