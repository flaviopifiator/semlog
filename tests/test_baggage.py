"""Baggage parsing/allowlist tests (STANDARDS §8, TCP-007).

Proves TCP-007: inbound W3C baggage is split into members in header
order, malformed members are skipped without discarding the whole
header, the spec limits of 64 members / 8192 bytes are enforced by
truncation (not full rejection), and only allowlisted keys are copied
into flat, percent-decoded, UNPREFIXED log attributes -- the formatter
applies the configured baggage prefix once, at format time (design #162
§2.1 step 3c). `to_log_attributes` must never prefix on its own: doing so
double-prefixes once a future `operation()`/`bind()` wires its result
straight into `Snapshot.baggage` and the formatter prefixes it again
(``baggage.baggage.x``, engram #239 follow-up).
"""

from __future__ import annotations

import unittest

from semlog._baggage import parse_baggage, to_log_attributes


class BaggageParsingTests(unittest.TestCase):
    """Proves: TCP-007"""

    def test_well_formed_members_parsed_in_header_order(self):
        members = parse_baggage("user_tier=gold,debug_flag=1")
        self.assertEqual((("user_tier", "gold"), ("debug_flag", "1")), members)

    def test_malformed_member_is_skipped_not_fatal(self):
        members = parse_baggage("user_tier=gold,not-a-pair,debug_flag=1")
        self.assertEqual((("user_tier", "gold"), ("debug_flag", "1")), members)

    def test_empty_header_returns_no_members(self):
        self.assertEqual((), parse_baggage(""))
        self.assertEqual((), parse_baggage(None))

    def test_properties_after_semicolon_are_ignored_for_the_value(self):
        members = parse_baggage("user_tier=gold;source=probe")
        self.assertEqual((("user_tier", "gold"),), members)

    def test_oversized_member_count_truncated_not_rejected(self):
        header = ",".join(f"k{i}=v{i}" for i in range(80))
        members = parse_baggage(header)
        self.assertEqual(64, len(members))
        self.assertEqual(("k0", "v0"), members[0])
        self.assertEqual(("k63", "v63"), members[-1])

    def test_oversized_byte_length_truncated_not_rejected(self):
        header = ",".join(f"k{i}=" + "v" * 200 for i in range(60))
        members = parse_baggage(header)
        self.assertLess(len(members), 60)
        self.assertGreater(len(members), 0)


class BaggageAllowlistTests(unittest.TestCase):
    """Proves: TCP-007"""

    def test_allowlisted_key_copied_others_excluded(self):
        members = parse_baggage("user_tier=gold,debug_flag=1")
        attributes = to_log_attributes(members, allow=("user_tier",))
        self.assertEqual({"user_tier": "gold"}, attributes)

    def test_allowlisted_value_is_percent_decoded(self):
        members = parse_baggage("region=us%20east")
        attributes = to_log_attributes(members, allow=("region",))
        self.assertEqual({"region": "us east"}, attributes)

    def test_empty_allowlist_copies_nothing(self):
        members = parse_baggage("user_tier=gold,debug_flag=1")
        self.assertEqual({}, to_log_attributes(members, allow=()))

    def test_output_is_unprefixed_the_formatter_owns_the_prefix(self):
        """Regression guard for the baggage double-prefix bug (engram
        #238/#239 follow-up): `to_log_attributes` must never add
        `baggage_prefix` itself."""
        members = parse_baggage("user_tier=gold")
        attributes = to_log_attributes(members, allow=("user_tier",))
        self.assertNotIn("baggage.user_tier", attributes)
        self.assertIn("user_tier", attributes)


if __name__ == "__main__":
    unittest.main()
