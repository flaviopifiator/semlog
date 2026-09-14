"""Exception capture, exactly once tests (STANDARDS §7, LP-004).

Proves LP-004: exceptions are captured into `error.type`, `exception.type`,
`exception.message` and `exception.stacktrace` (including the chained
`__cause__`/`__context__`), and the stacktrace field is rendered exactly
once per exception INSTANCE -- design #162 §2.1 step 3g's mechanical
support for "logged exactly once": a marker set on the instance the first
time it is rendered suppresses re-rendering the (possibly large) stacktrace
text if the same instance is ever logged again.
"""

from __future__ import annotations

import json
import logging
import sys
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


def _record(exc_info=None, stack_info=None):
    logger = logging.getLogger("semlog.tests.exceptions")
    return logger.makeRecord(
        logger.name,
        logging.ERROR,
        __file__,
        1,
        "app.request.failed",
        (),
        exc_info,
        extra=None,
        sinfo=stack_info,
    )


class ExceptionCaptureTests(unittest.TestCase):
    """Proves: LP-004"""

    def test_no_exc_info_omits_every_exception_field(self):
        formatter = Formatter(identity=_identity())
        parsed = json.loads(formatter.format(_record()))
        for field in (
            "error.type",
            "exception.type",
            "exception.message",
            "exception.stacktrace",
        ):
            self.assertNotIn(field, parsed)

    def test_exception_type_and_message_captured(self):
        try:
            raise ValueError("bad amount")
        except ValueError:
            record = _record(exc_info=sys.exc_info())
        formatter = Formatter(identity=_identity())
        parsed = json.loads(formatter.format(record))
        self.assertIn("ValueError", parsed["exception.type"])
        self.assertEqual("bad amount", parsed["exception.message"])
        self.assertEqual(parsed["exception.type"], parsed["error.type"])

    def test_chained_exception_stacktrace_reflects_the_full_chain(self):
        try:
            try:
                raise ValueError("original failure")
            except ValueError as original:
                raise RuntimeError("outer failure") from original
        except RuntimeError:
            record = _record(exc_info=sys.exc_info())
        formatter = Formatter(identity=_identity())
        parsed = json.loads(formatter.format(record))
        self.assertIn("RuntimeError", parsed["exception.type"])
        self.assertIn("outer failure", parsed["exception.message"])
        self.assertIn("original failure", parsed["exception.stacktrace"])
        self.assertIn("outer failure", parsed["exception.stacktrace"])

    def test_same_exception_instance_renders_stacktrace_only_once(self):
        try:
            raise ValueError("boom")
        except ValueError:
            exc_info = sys.exc_info()
        formatter = Formatter(identity=_identity())

        first = json.loads(formatter.format(_record(exc_info=exc_info)))
        second = json.loads(formatter.format(_record(exc_info=exc_info)))

        self.assertIn("exception.stacktrace", first)
        self.assertNotIn("exception.stacktrace", second)
        # error.type/exception.type/exception.message are still present:
        # only the (potentially large) stacktrace text is deduplicated.
        self.assertIn("exception.type", second)
        self.assertIn("exception.message", second)

    def test_stack_info_becomes_code_stacktrace(self):
        record = _record(stack_info="Stack (most recent call last):\n  fake")
        formatter = Formatter(identity=_identity())
        parsed = json.loads(formatter.format(record))
        self.assertIn("fake", parsed["code.stacktrace"])


if __name__ == "__main__":
    unittest.main()
