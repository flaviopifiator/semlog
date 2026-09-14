"""W3C traceparent/tracestate/span tests (STANDARDS §8, §8.1).

Proves TCP-001 through TCP-004: parsing and validating an inbound
``traceparent``, invalid-as-absent handling, version compatibility, and new
per-operation span generation that reuses the trace id and passes
``tracestate`` through unmodified.

A parsed or generated trace triple is a plain ``(trace_id, parent_id,
trace_flags)`` tuple, not a class (engram #238) -- assertions index or
unpack it instead of reading named attributes.
"""

from __future__ import annotations

import unittest

from semlog._trace import new_span, parse_traceparent


class TraceparentParsingTests(unittest.TestCase):
    """Proves: TCP-001, TCP-002, TCP-003"""

    def test_well_formed_header_accepted(self):
        parsed = parse_traceparent(
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        )
        self.assertIsNotNone(parsed)
        trace_id, parent_id, trace_flags = parsed
        self.assertEqual("4bf92f3577b34da6a3ce929d0e0e4736", trace_id)
        self.assertEqual("00f067aa0ba902b7", parent_id)
        self.assertEqual("01", trace_flags)

    def test_all_zero_trace_id_is_treated_as_absent(self):
        header = "00-" + "0" * 32 + "-00f067aa0ba902b7-01"
        self.assertIsNone(parse_traceparent(header))

    def test_all_zero_parent_id_is_treated_as_absent(self):
        header = "00-4bf92f3577b34da6a3ce929d0e0e4736-" + "0" * 16 + "-01"
        self.assertIsNone(parse_traceparent(header))

    def test_uppercase_hex_is_treated_as_absent(self):
        header = "00-4BF92F3577B34DA6A3CE929D0E0E4736-00F067AA0BA902B7-01"
        self.assertIsNone(parse_traceparent(header))

    def test_wrong_length_is_treated_as_absent(self):
        self.assertIsNone(parse_traceparent("00-abcd-00f067aa0ba902b7-01"))

    def test_wrong_field_count_is_treated_as_absent(self):
        self.assertIsNone(parse_traceparent("00-4bf92f3577b34da6a3ce929d0e0e4736"))

    def test_none_header_is_absent(self):
        self.assertIsNone(parse_traceparent(None))

    def test_version_ff_is_rejected(self):
        header = "ff-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        self.assertIsNone(parse_traceparent(header))

    def test_future_version_with_extra_field_is_parsed_ignoring_trailing(self):
        header = "01-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01-extra-field"
        parsed = parse_traceparent(header)
        self.assertIsNotNone(parsed)
        trace_id, parent_id, trace_flags = parsed
        self.assertEqual("4bf92f3577b34da6a3ce929d0e0e4736", trace_id)
        self.assertEqual("00f067aa0ba902b7", parent_id)
        self.assertEqual("01", trace_flags)

    def test_version_00_with_trailing_field_is_rejected(self):
        header = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01-extra"
        self.assertIsNone(parse_traceparent(header))


class NewSpanTests(unittest.TestCase):
    """Proves: TCP-004"""

    def test_inbound_trace_id_is_reused_with_a_new_span_id(self):
        inbound = parse_traceparent(
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        )
        span, _tracestate = new_span(inbound)
        trace_id, parent_id, _trace_flags = span
        self.assertEqual("4bf92f3577b34da6a3ce929d0e0e4736", trace_id)
        self.assertNotEqual("00f067aa0ba902b7", parent_id)
        self.assertEqual(16, len(parent_id))

    def test_absent_inbound_generates_a_fresh_trace(self):
        span, tracestate = new_span(None)
        trace_id, parent_id, trace_flags = span
        self.assertEqual(32, len(trace_id))
        self.assertEqual(16, len(parent_id))
        self.assertEqual("01", trace_flags)
        self.assertIsNone(tracestate)

    def test_two_calls_generate_different_span_ids(self):
        inbound = parse_traceparent(
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        )
        first, _ = new_span(inbound)
        second, _ = new_span(inbound)
        self.assertNotEqual(first[1], second[1])

    def test_tracestate_preserved_byte_for_byte(self):
        inbound = parse_traceparent(
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        )
        _span, tracestate = new_span(inbound, tracestate="vendor1=value1")
        self.assertEqual("vendor1=value1", tracestate)


if __name__ == "__main__":
    unittest.main()
