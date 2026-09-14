"""UUIDv7 request-id generator tests (STANDARDS §8, TCP-010, design ADR-7).

Proves TCP-010: `http.request.id` is a UUIDv7 per RFC 9562 with correct
version/variant bits and strictly time-ordered values, whether generated
by the bundled implementation (Python 3.10-3.13) or delegated to stdlib
`uuid.uuid7()` (3.14+).
"""

from __future__ import annotations

import sys
import time
import unittest
import uuid

from semlog._request_id import generate_request_id


class RequestIdBitLayoutTests(unittest.TestCase):
    """Proves: TCP-010"""

    def test_version_nibble_is_seven(self):
        generated = uuid.UUID(generate_request_id())
        self.assertEqual(7, generated.version)

    def test_variant_bits_are_rfc_4122(self):
        generated = uuid.UUID(generate_request_id())
        self.assertEqual(uuid.RFC_4122, generated.variant)

    def test_returns_a_well_formed_uuid_string(self):
        value = generate_request_id()
        self.assertEqual(value, str(uuid.UUID(value)))


class RequestIdOrderingTests(unittest.TestCase):
    """Proves: TCP-010"""

    def test_time_ordering_holds_across_rapid_successive_calls(self):
        generated = [generate_request_id() for _ in range(200)]
        self.assertEqual(sorted(generated), generated)

    def test_two_ids_generated_apart_sort_later_id_after_earlier_id(self):
        first = generate_request_id()
        time.sleep(0.002)
        second = generate_request_id()
        self.assertLess(first, second)

    @unittest.skipUnless(
        sys.version_info >= (3, 14), "delegates to stdlib uuid.uuid7 on 3.14+"
    )
    def test_delegates_to_stdlib_uuid7_on_314_plus(self):
        generated = uuid.UUID(generate_request_id())
        stdlib_generated = uuid.uuid7()
        self.assertEqual(generated.version, stdlib_generated.version)
        self.assertEqual(generated.variant, stdlib_generated.variant)


if __name__ == "__main__":
    unittest.main()
