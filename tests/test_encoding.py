"""JSON encoding and sanitizing-fallback tests (STANDARDS §2, design #162 §6.6).

Proves LRC-001: a value containing control characters (including a raw
newline) can never break the single-RFC-8259-object-per-line guarantee,
because `json.dumps` escapes them -- this is the mechanism that prevents
log injection (CWE-117). Also covers design #162 §6.6's sanitizing slow
path: `allow_nan=False` forces non-finite floats through a fallback that
turns them into their documented JSON-safe string token, a non-string map
key is stringified rather than raising, a self-referential structure
is bounded by a depth cap rather than looping forever, and a nested value
the encoder's `default=` hook coerces into a non-finite float renders the
same token instead of dropping the record.
"""

from __future__ import annotations

import enum
import json
import logging
import math
import unittest
from unittest import mock

from semlog import _transport
from semlog._config import configure
from semlog._format import Formatter

from ._pipeline_support import FakeStdout, reset_pipeline


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


def _record(extra):
    logger = logging.getLogger("semlog.tests.encoding")
    return logger.makeRecord(
        logger.name,
        logging.INFO,
        __file__,
        1,
        "app.custom.event",
        (),
        None,
        extra=extra,
    )


class LogInjectionTests(unittest.TestCase):
    """Proves: LRC-001"""

    def test_embedded_control_characters_never_break_the_single_line(self):
        formatter = Formatter(identity=_identity())
        record = _record({"note": "line one\nline two\ttabbed"})
        line = formatter.format(record)
        self.assertNotIn("\n", line)
        self.assertNotIn("\t", line)
        parsed = json.loads(line)
        self.assertEqual("line one\nline two\ttabbed", parsed["app.note"])


class SanitizingFallbackTests(unittest.TestCase):
    """Proves: LRC-001"""

    def test_nan_becomes_the_documented_string_token(self):
        formatter = Formatter(identity=_identity())
        record = _record({"score": math.nan})
        line = formatter.format(record)  # must not raise
        parsed = json.loads(line)
        self.assertEqual("NaN", parsed["app.score"])

    def test_infinity_becomes_the_documented_string_tokens(self):
        formatter = Formatter(identity=_identity())
        record = _record({"pos": math.inf, "neg": -math.inf})
        parsed = json.loads(formatter.format(record))  # must not raise
        self.assertEqual("Infinity", parsed["app.pos"])
        self.assertEqual("-Infinity", parsed["app.neg"])

    def test_non_string_map_key_is_stringified_not_rejected(self):
        formatter = Formatter(identity=_identity())
        record = _record({"weird": {("a", "b"): "value"}})
        line = formatter.format(record)  # must not raise
        parsed = json.loads(line)
        self.assertEqual({"('a', 'b')": "value"}, parsed["app.weird"])

    def test_self_referential_structure_does_not_hang_or_raise(self):
        cyclic: dict = {}
        cyclic["self"] = cyclic
        formatter = Formatter(identity=_identity())
        record = _record({"loop": cyclic})
        line = formatter.format(record)  # must not raise or hang
        json.loads(line)  # must still be valid JSON


class _Coerced(enum.Enum):
    """Members the encoder's `default=` hook coerces into non-finite floats."""

    NAN = math.nan
    POS_INF = math.inf
    NEG_INF = -math.inf


_TOKENS = {
    _Coerced.NAN: "NaN",
    _Coerced.POS_INF: "Infinity",
    _Coerced.NEG_INF: "-Infinity",
}


class NestedCoercedNonFiniteTests(unittest.TestCase):
    """Proves: LRC-001, LRC-009

    A non-JSON-native value nested in a container is coerced only by the
    encoder's `default=` hook, so it can become a non-finite float that the
    sanitizing fallback never saw. The record must still be emitted, with
    that value rendered exactly as the same value renders as a top-level
    attribute."""

    def _line(self, formatter, extra):
        record = _record(extra)
        record.created = 1_700_000_000.0
        return formatter.format(record)  # must not raise

    def test_nested_in_a_list_tuple_or_map_renders_like_a_top_level_attribute(self):
        formatter = Formatter(identity=_identity())
        for member, token in _TOKENS.items():
            top_level = self._line(formatter, {"value": member})
            self.assertIn(f'"app.value":"{token}",', top_level)
            for nested, rendered in (
                ([member], f'["{token}"]'),
                ((member,), f'["{token}"]'),
                ({"key": member}, f'{{"key":"{token}"}}'),
            ):
                with self.subTest(token=token, container=type(nested).__name__):
                    expected = top_level.replace(
                        f'"app.value":"{token}"', f'"app.value":{rendered}'
                    )
                    self.assertEqual(expected, self._line(formatter, {"value": nested}))


class NestedCoercedNonFinitePipelineTests(unittest.TestCase):
    """Proves: LRC-001, LRC-009

    End to end through the real `configure()` pipeline: the record reaches
    stdout instead of being counted as dropped by the queue handler."""

    def tearDown(self):
        reset_pipeline()

    def test_the_record_reaches_stdout_with_every_nested_token(self):
        fake_stdout = FakeStdout()
        with (
            mock.patch("sys.stdout", fake_stdout),
            mock.patch.dict("os.environ", {}, clear=True),
        ):
            configure(service_name="encoding-test", search_dir=".")
            logging.getLogger("semlog.tests.encoding").info(
                "app.custom.event",
                extra={
                    "items": [_Coerced.NAN],
                    "pair": (_Coerced.POS_INF,),
                    "map": {"key": _Coerced.NEG_INF},
                },
            )
            _transport.flush(timeout=2)
            dropped = _transport._state.handler.counters.dropped

        lines = fake_stdout.buffer.getvalue().decode("utf-8").splitlines()
        self.assertEqual(0, dropped)
        self.assertEqual(1, len(lines))
        parsed = json.loads(lines[0])
        self.assertEqual(["NaN"], parsed["app.items"])
        self.assertEqual(["Infinity"], parsed["app.pair"])
        self.assertEqual({"key": "-Infinity"}, parsed["app.map"])


if __name__ == "__main__":
    unittest.main()
