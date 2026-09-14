"""Always-on recursive redaction tests (STANDARDS §9, LP-005).

Proves LP-005: redaction of configured sensitive keys is always on (no
runtime disable switch exists at all) and applies recursively into nested
maps and arrays before serialization. Case-insensitive match on either the
full key or its last dotted segment; the built-in list can only grow via
`redact_keys`, never shrink.
"""

from __future__ import annotations

import json
import logging
import unittest

from semlog._format import Formatter


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
    logger = logging.getLogger("semlog.tests.redaction")
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


class BuiltInRedactionTests(unittest.TestCase):
    """Proves: LP-005"""

    def test_top_level_sensitive_key_is_redacted(self):
        formatter = Formatter(identity=_identity())
        record = _record({"password": "hunter2"})
        parsed = json.loads(formatter.format(record))
        self.assertEqual("REDACTED", parsed["app.password"])

    def test_match_is_case_insensitive(self):
        formatter = Formatter(identity=_identity())
        record = _record({"API_KEY": "sk-abc"})
        parsed = json.loads(formatter.format(record))
        self.assertEqual("REDACTED", parsed["app.API_KEY"])

    def test_nested_sensitive_value_redacted_not_just_top_level(self):
        formatter = Formatter(identity=_identity())
        record = _record({"payload": {"password": "secret", "amount": 100}})
        parsed = json.loads(formatter.format(record))
        self.assertEqual({"password": "REDACTED", "amount": 100}, parsed["app.payload"])

    def test_last_dotted_segment_match_inside_an_array(self):
        formatter = Formatter(identity=_identity())
        record = _record({"accounts": [{"user.token": "abc"}, {"user.token": "def"}]})
        parsed = json.loads(formatter.format(record))
        self.assertEqual(
            [{"user.token": "REDACTED"}, {"user.token": "REDACTED"}],
            parsed["app.accounts"],
        )

    def test_non_sensitive_values_are_left_untouched(self):
        formatter = Formatter(identity=_identity())
        record = _record({"payment": {"amount": 100, "currency": "USD"}})
        parsed = json.loads(formatter.format(record))
        self.assertEqual({"amount": 100, "currency": "USD"}, parsed["app.payment"])


class ConfiguredRedactionTests(unittest.TestCase):
    """Proves: LP-005"""

    def test_configured_extra_key_is_redacted(self):
        formatter = Formatter(identity=_identity(), redact_keys=("card_number",))
        record = _record({"card_number": "4111111111111111"})
        parsed = json.loads(formatter.format(record))
        self.assertEqual("REDACTED", parsed["app.card_number"])

    def test_built_ins_cannot_be_removed(self):
        formatter = Formatter(identity=_identity(), redact_keys=("card_number",))
        record = _record({"password": "hunter2", "card_number": "4111"})
        parsed = json.loads(formatter.format(record))
        self.assertEqual("REDACTED", parsed["app.password"])
        self.assertEqual("REDACTED", parsed["app.card_number"])


if __name__ == "__main__":
    unittest.main()
