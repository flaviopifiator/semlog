"""Non-serializable value coercion and attribute-limit tests (STANDARDS §6).

Proves LRC-009 (a non-JSON-serializable value is coerced to its string
representation instead of raising, and an oversized value is truncated
rather than rejected) and LRC-010 (configurable `max_attributes` /
`max_attribute_length` limits, both attribute-count overflow discarding
the excess and value-length overflow truncating in place, each counted
via the two library-specific `{ns}.dropped_attributes_count` /
`{ns}.truncated_attributes_count` fields, and both resolvable from
`OTEL_*` environment variables with the documented precedence).
"""

from __future__ import annotations

import base64
import json
import logging
import unittest
from unittest import mock

from semlog import _config
from semlog._config import configure
from semlog._format import Formatter

from ._pipeline_support import reset_pipeline


def _identity():
    return {
        "service.name": "svc",
        "service.namespace": None,
        "service.version": None,
        "service.instance.id": "11111111-1111-1111-1111-111111111111",
        "deployment.environment.name": None,
        "telemetry.sdk.name": "semlog",
        "telemetry.sdk.version": "0.0.0",
        "telemetry.sdk.language": "python",
    }


def _record(extra=None):
    logger = logging.getLogger("semlog.tests.limits")
    return logger.makeRecord(
        logger.name,
        logging.INFO,
        __file__,
        1,
        "app.custom.event",
        (),
        None,
        extra=extra or {},
    )


class NonSerializableValueTests(unittest.TestCase):
    """Proves: LRC-009"""

    def test_non_serializable_object_becomes_its_string_representation(self):
        class Money:
            def __str__(self):
                return "Money(amount=15000)"

        formatter = Formatter(identity=_identity())
        record = _record({"amount": Money()})
        parsed = json.loads(formatter.format(record))  # must not raise
        self.assertEqual("Money(amount=15000)", parsed["app.amount"])


class AttributeValueLengthLimitTests(unittest.TestCase):
    """Proves: LRC-009, LRC-010"""

    def test_oversized_string_is_truncated_to_the_configured_limit(self):
        formatter = Formatter(identity=_identity(), max_attribute_length=5)
        record = _record({"note": "abcdefghij"})
        parsed = json.loads(formatter.format(record))
        self.assertEqual("abcde", parsed["app.note"])

    def test_truncation_is_counted_and_reported(self):
        formatter = Formatter(identity=_identity(), max_attribute_length=5)
        record = _record({"note": "abcdefghij"})
        parsed = json.loads(formatter.format(record))
        self.assertEqual(1, parsed["app.truncated_attributes_count"])

    def test_no_truncation_counter_when_nothing_is_truncated(self):
        formatter = Formatter(identity=_identity(), max_attribute_length=5)
        record = _record({"note": "abc"})
        parsed = json.loads(formatter.format(record))
        self.assertNotIn("app.truncated_attributes_count", parsed)

    def test_unlimited_by_default(self):
        formatter = Formatter(identity=_identity())
        record = _record({"note": "x" * 5000})
        parsed = json.loads(formatter.format(record))
        self.assertEqual(5000, len(parsed["app.note"]))

    def test_bytes_are_truncated_raw_then_base64_encoded(self):
        """Proves: LRC-009, LRC-010

        Pins the order the code already implements (truncate raw bytes,
        then base64-encode): only the test was missing (Phase 2
        source-budget refactor)."""
        formatter = Formatter(identity=_identity(), max_attribute_length=4)
        record = _record({"blob": b"abcdefghij"})
        parsed = json.loads(formatter.format(record))
        self.assertEqual(base64.b64encode(b"abcd").decode("ascii"), parsed["app.blob"])


class TupleAttributeTests(unittest.TestCase):
    """Proves: LRC-010

    Regression tests for the tuple-as-JSON-array fix (Phase 2
    source-budget refactor, engram #239 follow-up): `json` already
    encodes a `tuple` as an array, so a tuple attribute value must flow
    through exactly like a `list` -- per-element limits, per-element
    bytes/base64 coercion -- instead of being stringified as a whole
    before truncation."""

    def test_tuple_attribute_encodes_as_a_json_array_not_a_stringified_tuple(self):
        formatter = Formatter(identity=_identity())
        record = _record({"coords": (1, "two", 3.0)})
        parsed = json.loads(formatter.format(record))
        self.assertEqual([1, "two", 3.0], parsed["app.coords"])

    def test_tuple_elements_are_truncated_per_element_like_a_list(self):
        formatter = Formatter(identity=_identity(), max_attribute_length=3)
        record = _record({"notes": ("abcdefgh", "xy")})
        parsed = json.loads(formatter.format(record))
        self.assertEqual(["abc", "xy"], parsed["app.notes"])
        self.assertEqual(1, parsed["app.truncated_attributes_count"])

    def test_bytes_inside_a_tuple_are_base64_encoded_not_skipped(self):
        formatter = Formatter(identity=_identity())
        record = _record({"payload": (b"abc", "meta")})
        parsed = json.loads(formatter.format(record))
        expected = base64.b64encode(b"abc").decode("ascii")
        self.assertEqual([expected, "meta"], parsed["app.payload"])


class AttributeCountLimitTests(unittest.TestCase):
    """Proves: LRC-010"""

    def test_excess_attributes_are_discarded_and_counted(self):
        formatter = Formatter(identity=_identity(), max_attributes=2)
        record = _record({"a": 1, "b": 2, "c": 3, "d": 4, "e": 5})
        parsed = json.loads(formatter.format(record))
        kept = [k for k in ("app.a", "app.b", "app.c", "app.d", "app.e") if k in parsed]
        self.assertEqual(2, len(kept))
        self.assertEqual(3, parsed["app.dropped_attributes_count"])

    def test_default_count_limit_is_128(self):
        formatter = Formatter(identity=_identity())
        record = _record({f"k{i}": i for i in range(130)})
        parsed = json.loads(formatter.format(record))
        self.assertEqual(2, parsed["app.dropped_attributes_count"])


class LimitPrecedenceTests(unittest.TestCase):
    """Proves: LRC-010"""

    def tearDown(self):
        reset_pipeline()

    def test_explicit_parameter_overrides_all_environment_variables(self):
        with mock.patch.dict(
            "os.environ",
            {
                "OTEL_ATTRIBUTE_COUNT_LIMIT": "64",
                "OTEL_LOGRECORD_ATTRIBUTE_COUNT_LIMIT": "100",
            },
            clear=True,
        ):
            configure(max_attributes=32, search_dir=".")
        self.assertEqual(32, _config.current().max_attributes)

    def test_log_record_specific_variable_wins_over_generic(self):
        with mock.patch.dict(
            "os.environ",
            {
                "OTEL_ATTRIBUTE_COUNT_LIMIT": "64",
                "OTEL_LOGRECORD_ATTRIBUTE_COUNT_LIMIT": "100",
            },
            clear=True,
        ):
            configure(search_dir=".")
        self.assertEqual(100, _config.current().max_attributes)

    def test_default_when_nothing_is_set(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            configure(search_dir=".")
        self.assertEqual(128, _config.current().max_attributes)
        self.assertIsNone(_config.current().max_attribute_length)


if __name__ == "__main__":
    unittest.main()
