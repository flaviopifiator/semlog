"""Baggage parser robustness tests (task 4.9; STANDARDS §8, TCP-007;
design #162 §9: "seeded `random.Random` generators (property-style, no
Hypothesis): never raise, round-trip valid input, enforce limits").

Distinct from `tests/test_baggage.py`'s hand-picked unit tests: this
module drives `parse_baggage` with seeded pseudo-random input so the
property "never raises, and always honors the 64-member/8192-byte caps"
is checked against many shapes at once, deterministically (a fixed seed
reproduces the exact same cases on every run, unlike a real fuzzer).
"""

from __future__ import annotations

import random
import string
import unittest

from semlog._baggage import parse_baggage

_SEED = 20260911
_GARBAGE_ALPHABET = string.printable + "áéíóúñ€中文😀"
_MAX_MEMBERS = 64
_MAX_BYTES = 8192


def _random_garbage(rng: random.Random, length: int) -> str:
    return "".join(rng.choice(_GARBAGE_ALPHABET) for _ in range(length))


class BaggageParserNeverRaisesTests(unittest.TestCase):
    """Proves: TCP-007"""

    def test_random_garbage_headers_never_raise(self):
        rng = random.Random(_SEED)
        for _ in range(200):
            length = rng.randint(0, 500)
            header = _random_garbage(rng, length)
            try:
                parse_baggage(header)
            except Exception as exc:  # noqa: BLE001 -- deliberately broad: any
                # exception here is itself the property violation under test.
                self.fail(f"parse_baggage raised {exc!r} for input {header!r}")

    def test_known_degenerate_inputs_never_raise(self):
        for header in (None, "", " ", ",", ";", "=", "==", ",,,,", "a" * 20000):
            with self.subTest(header=header):
                parse_baggage(header)  # must not raise


class BaggageRoundTripTests(unittest.TestCase):
    """Proves: TCP-007"""

    def test_well_formed_random_header_round_trips(self):
        rng = random.Random(_SEED + 1)
        member_count = rng.randint(1, 20)
        alphabet = string.ascii_lowercase + string.digits
        keys = [
            f"k{i}{rng.choice(string.ascii_lowercase)}" for i in range(member_count)
        ]
        values = [
            "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 10)))
            for _ in range(member_count)
        ]
        header = ",".join(f"{k}={v}" for k, v in zip(keys, values, strict=True))

        members = parse_baggage(header)

        self.assertEqual(tuple(zip(keys, values, strict=True)), members)


class BaggageLimitEnforcementUnderRandomInputTests(unittest.TestCase):
    """Proves: TCP-007"""

    def test_random_oversized_member_count_stays_at_or_under_the_cap(self):
        rng = random.Random(_SEED + 2)
        count = rng.randint(_MAX_MEMBERS + 1, _MAX_MEMBERS * 3)
        header = ",".join(f"k{i}=v{i}" for i in range(count))

        members = parse_baggage(header)

        self.assertLessEqual(len(members), _MAX_MEMBERS)
        self.assertGreater(len(members), 0)

    def test_random_oversized_byte_length_stays_at_or_under_the_cap(self):
        rng = random.Random(_SEED + 3)
        values = []
        total = 0
        while total < _MAX_BYTES + 800:
            value = "v" * rng.randint(50, 300)
            values.append(value)
            total += len(value) + 4
        header = ",".join(f"k{i}={value}" for i, value in enumerate(values))

        members = parse_baggage(header)

        total_bytes = sum(len(f"{k}={v}".encode()) for k, v in members)
        self.assertLessEqual(total_bytes, _MAX_BYTES)
        self.assertGreater(len(members), 0)


if __name__ == "__main__":
    unittest.main()
