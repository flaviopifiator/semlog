"""Formatter envelope/classification/severity/correlation/null-rule tests
(design #162 §1.1 "Formatter", §2.1, §4.1, §4.2).

Proves LRC-001, LRC-002, LRC-003, LRC-004, LRC-005, LRC-012: the formatter
renders exactly one parseable RFC 8259 JSON object with no embedded
newline, an RFC 3339 UTC microsecond timestamp, dual severity encoding,
event_name/body classification, trace correlation fields present only
when a context is bound, and null (never empty string) for optional
identity fields whose value is legitimately unknown.
"""

from __future__ import annotations

import json
import logging
import unittest

from semlog._context import Snapshot, pop, push
from semlog._format import Formatter


def _make_record(level, msg, args=()):
    return logging.LogRecord(
        name="test.logger",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )


def _identity(**overrides):
    """Build an identity dict (`_identity.resolve_identity`'s return shape,
    engram #238) using the same underscore-style keyword names the old
    `Identity` dataclass fixture accepted, so call sites stay unchanged."""
    fields = {
        "service_name": "svc",
        "service_version": None,
        "service_namespace": None,
        "service_instance_id": "11111111-1111-1111-1111-111111111111",
        "deployment_environment_name": None,
        "telemetry_sdk_name": "semlog",
        "telemetry_sdk_version": "0.0.0",
        "telemetry_sdk_language": "python",
    }
    fields.update(overrides)
    return {
        "service.name": fields["service_name"],
        "service.namespace": fields["service_namespace"],
        "service.version": fields["service_version"],
        "service.instance.id": fields["service_instance_id"],
        "deployment.environment.name": fields["deployment_environment_name"],
        "telemetry.sdk.name": fields["telemetry_sdk_name"],
        "telemetry.sdk.version": fields["telemetry_sdk_version"],
        "telemetry.sdk.language": fields["telemetry_sdk_language"],
    }


class FormatterEnvelopeTests(unittest.TestCase):
    """Proves: LRC-001, LRC-002"""

    def test_single_line_valid_json(self):
        formatter = Formatter(identity=_identity())
        line = formatter.format(_make_record(logging.INFO, "payment.completed"))
        self.assertNotIn("\n", line)
        parsed = json.loads(line)
        self.assertIsInstance(parsed, dict)

    def test_timestamp_is_rfc3339_utc_microseconds(self):
        formatter = Formatter(identity=_identity())
        record = _make_record(logging.INFO, "payment.completed")
        record.created = 1_700_000_000.123456
        parsed = json.loads(formatter.format(record))
        self.assertRegex(
            parsed["timestamp"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$"
        )
        self.assertTrue(parsed["timestamp"].startswith("2023-11-14T22:13:20."))


class FormatterSeverityTests(unittest.TestCase):
    """Proves: LRC-003"""

    def test_error_level_maps_to_otel_error_range(self):
        formatter = Formatter(identity=_identity())
        parsed = json.loads(
            formatter.format(_make_record(logging.ERROR, "payment.failed"))
        )
        self.assertEqual("ERROR", parsed["severity_text"])
        self.assertIn(parsed["severity_number"], range(17, 21))

    def test_every_standard_level_has_one_range_and_text(self):
        expectations = {
            logging.DEBUG: ("DEBUG", range(5, 9)),
            logging.INFO: ("INFO", range(9, 13)),
            logging.WARNING: ("WARNING", range(13, 17)),
            logging.ERROR: ("ERROR", range(17, 21)),
            logging.CRITICAL: ("CRITICAL", range(21, 25)),
        }
        formatter = Formatter(identity=_identity())
        for level, (text, number_range) in expectations.items():
            with self.subTest(level=level):
                parsed = json.loads(
                    formatter.format(_make_record(level, "app.event.happened"))
                )
                self.assertEqual(text, parsed["severity_text"])
                self.assertIn(parsed["severity_number"], number_range)


class SeverityNumberBandingTests(unittest.TestCase):
    """Proves: LRC-003

    Approval test for the Phase 2 source-budget refactor: pins
    `_severity_number`'s tens-band formula (STANDARDS §3) against every
    level 0-60, comparing it to the original comparison-ladder
    implementation kept here as a reference oracle, before replacing the
    production code with its arithmetic equivalent -- so the slimming
    refactor cannot silently change a single record's `severity_number`.
    """

    def test_arithmetic_formula_matches_the_band_table_for_every_level_0_to_60(self):
        from semlog._format import _severity_number

        def _band_reference(levelno: int) -> int:
            if levelno < 10:
                base = 1
            elif levelno < 20:
                base = 5
            elif levelno < 30:
                base = 9
            elif levelno < 40:
                base = 13
            elif levelno < 50:
                base = 17
            else:
                base = 21
            return base + min(levelno % 10, 3)

        for levelno in range(61):
            with self.subTest(levelno=levelno):
                self.assertEqual(_band_reference(levelno), _severity_number(levelno))


class FormatterClassificationTests(unittest.TestCase):
    """Proves: LRC-004"""

    def test_conformant_event_name_becomes_event_name_with_null_body(self):
        formatter = Formatter(identity=_identity())
        parsed = json.loads(
            formatter.format(
                _make_record(logging.INFO, "payment.authorization.completed")
            )
        )
        self.assertEqual("payment.authorization.completed", parsed["event_name"])
        self.assertIsNone(parsed["body"])

    def test_third_party_text_message_becomes_body_with_null_event_name(self):
        formatter = Formatter(identity=_identity())
        parsed = json.loads(
            formatter.format(
                _make_record(logging.INFO, "Starting up on port %s", (8080,))
            )
        )
        self.assertIsNone(parsed["event_name"])
        self.assertEqual("Starting up on port 8080", parsed["body"])


class FormatterTraceCorrelationTests(unittest.TestCase):
    """Proves: LRC-005"""

    def test_no_bound_context_omits_correlation_fields(self):
        formatter = Formatter(identity=_identity())
        parsed = json.loads(
            formatter.format(_make_record(logging.INFO, "app.startup.completed"))
        )
        self.assertNotIn("trace_id", parsed)
        self.assertNotIn("span_id", parsed)
        self.assertNotIn("trace_flags", parsed)

    def test_bound_context_includes_correlation_fields(self):
        token = push(Snapshot(trace_id="a" * 32, span_id="b" * 16, trace_flags="01"))
        try:
            formatter = Formatter(identity=_identity())
            parsed = json.loads(
                formatter.format(_make_record(logging.INFO, "app.request.handled"))
            )
        finally:
            pop(token)
        self.assertEqual("a" * 32, parsed["trace_id"])
        self.assertEqual("b" * 16, parsed["span_id"])
        self.assertEqual("01", parsed["trace_flags"])


class FormatterNullRuleTests(unittest.TestCase):
    """Proves: LRC-012"""

    def test_unconfigured_deployment_environment_is_null_not_empty_string(self):
        formatter = Formatter(identity=_identity(deployment_environment_name=None))
        parsed = json.loads(
            formatter.format(_make_record(logging.INFO, "app.startup.completed"))
        )
        self.assertIsNone(parsed["deployment.environment.name"])
        self.assertNotEqual("", parsed["deployment.environment.name"])

    def test_configured_deployment_environment_is_the_string_value(self):
        formatter = Formatter(
            identity=_identity(deployment_environment_name="production")
        )
        parsed = json.loads(
            formatter.format(_make_record(logging.INFO, "app.startup.completed"))
        )
        self.assertEqual("production", parsed["deployment.environment.name"])


if __name__ == "__main__":
    unittest.main()
