"""Like-for-like scenario tests: every subject emits the SAME field set as
semlog's envelope (timestamp, severity text and number, event name, body,
scope name, the three call-site attributes, and the 8 identity fields), in
the same order, so the steady-state comparison measures equal output work.
`loguru`/`structlog` variants skip gracefully when those packages are not
installed in the running interpreter.
"""

from __future__ import annotations

import importlib.util
import io
import json
import logging
import unittest

from benchmark import like_for_like

_HAS_LOGURU = importlib.util.find_spec("loguru") is not None
_HAS_STRUCTLOG = importlib.util.find_spec("structlog") is not None

_CALL = {"user_id": "u-42", "request_id": "r-7", "duration_ms": 3.25}


def _emit_lines(make_subject, **call):
    buffer = io.BytesIO()
    emit, shutdown = make_subject(buffer)
    try:
        emit(**(call or _CALL))
    finally:
        shutdown()
    return [line for line in buffer.getvalue().decode("utf-8").splitlines() if line]


def _emit_record(make_subject, **call):
    lines = _emit_lines(make_subject, **call)
    if len(lines) != 1:
        raise AssertionError(f"expected one JSON line, got {lines!r}")
    return json.loads(lines[0])


class LikeForLikeFieldSetTests(unittest.TestCase):
    """Proves: CP-005"""

    def _assert_same_fields_as_semlog(self, make_subject):
        record = _emit_record(make_subject)
        self.assertEqual(list(like_for_like.FIELDS), list(record))
        self.assertEqual("bench.request.completed", record["event_name"])
        self.assertIsNone(record["body"])
        self.assertEqual("INFO", record["severity_text"])
        self.assertEqual(9, record["severity_number"])
        self.assertEqual(like_for_like.SCOPE_NAME, record["otel.scope.name"])
        self.assertEqual("u-42", record["app.user_id"])
        self.assertEqual("bench-svc", record["service.name"])
        self.assertRegex(record["timestamp"], like_for_like.TIMESTAMP_RE)

    def test_field_set_is_semlog_envelope_order(self):
        self.assertEqual(
            (
                "timestamp",
                "severity_text",
                "severity_number",
                "event_name",
                "body",
                "otel.scope.name",
                "app.user_id",
                "app.request_id",
                "app.duration_ms",
                "service.name",
                "service.namespace",
                "service.version",
                "service.instance.id",
                "deployment.environment.name",
                "telemetry.sdk.name",
                "telemetry.sdk.version",
                "telemetry.sdk.language",
            ),
            like_for_like.FIELDS,
        )

    def test_semlog_subject(self):
        self._assert_same_fields_as_semlog(like_for_like.make_semlog)

    def test_stdlib_baseline_subject(self):
        self._assert_same_fields_as_semlog(like_for_like.make_stdlib_baseline)

    @unittest.skipUnless(_HAS_LOGURU, "loguru not installed in this interpreter")
    def test_loguru_subject(self):
        self._assert_same_fields_as_semlog(like_for_like.make_loguru)

    @unittest.skipUnless(_HAS_STRUCTLOG, "structlog not installed in this interpreter")
    def test_structlog_on_stdlib_subject(self):
        self._assert_same_fields_as_semlog(like_for_like.make_structlog_stdlib)

    @unittest.skipUnless(_HAS_STRUCTLOG, "structlog not installed in this interpreter")
    def test_structlog_subject_renders_through_a_stdlib_logging_handler(self):
        import structlog

        _emit, shutdown = like_for_like.make_structlog_stdlib(io.BytesIO())
        try:
            handlers = logging.getLogger(like_for_like.SCOPE_NAME).handlers
            self.assertEqual(1, len(handlers))
            self.assertIsInstance(
                handlers[0].formatter, structlog.stdlib.ProcessorFormatter
            )
            self.assertIsInstance(
                structlog.get_config()["logger_factory"],
                structlog.stdlib.LoggerFactory,
            )
        finally:
            shutdown()

    def test_shutdown_leaves_no_handler_behind(self):
        _emit_record(like_for_like.make_semlog)
        _emit_record(like_for_like.make_stdlib_baseline)
        self.assertEqual([], logging.getLogger(like_for_like.SCOPE_NAME).handlers)


class LikeForLikeFairnessCheckTests(unittest.TestCase):
    """Proves: CP-005"""

    def test_every_available_subject_is_fair_for_the_same_call(self):
        raw = {
            name: _emit_record(factory)
            for name, factory in like_for_like.available_factories().items()
        }
        self.assertIn("semlog", raw)
        self.assertEqual([], like_for_like.check_fairness(raw))

    def test_available_factories_match_the_interpreter(self):
        available = like_for_like.available_factories()
        self.assertIn("stdlib_baseline", available)
        self.assertEqual(_HAS_LOGURU, "loguru" in available)
        self.assertEqual(_HAS_STRUCTLOG, "structlog_stdlib" in available)

    def test_a_differing_value_is_reported(self):
        raw = {
            "semlog": _emit_record(like_for_like.make_semlog),
            "stdlib_baseline": _emit_record(
                like_for_like.make_stdlib_baseline,
                user_id="u-42",
                request_id="r-7",
                duration_ms=9.0,
            ),
        }
        problems = like_for_like.check_fairness(raw)
        self.assertTrue(any("app.duration_ms" in p for p in problems), problems)

    def test_a_missing_or_reordered_field_is_reported(self):
        reference = _emit_record(like_for_like.make_semlog)
        reordered = dict(reversed(list(reference.items())))
        missing = {k: v for k, v in reference.items() if k != "service.version"}
        problems = like_for_like.check_fairness(
            {"semlog": reference, "reordered": reordered, "missing": missing}
        )
        self.assertTrue(any("reordered" in p for p in problems), problems)
        self.assertTrue(any("service.version" in p for p in problems), problems)

    def test_a_malformed_timestamp_is_reported(self):
        reference = _emit_record(like_for_like.make_semlog)
        broken = dict(reference, timestamp="yesterday")
        problems = like_for_like.check_fairness({"semlog": reference, "x": broken})
        self.assertTrue(any("timestamp" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
