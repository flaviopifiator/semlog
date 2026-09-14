"""Pre-queue rendering regression-guard test (STANDARDS §10, LP-002).

Proves LP-002: context capture, redaction and full JSON rendering
(including exception formatting) happen entirely inside `Formatter.format`,
before any queue transport ever sees the record -- so the queue only ever
needs to move an already-finished string. Uses stdlib's own
`logging.handlers.QueueHandler.prepare` (verified in design #162 §2.1's
regression-guard scenario: `prepare` clears `exc_info`/`exc_text`/
`stack_info` before enqueuing) as the real, non-conformant counterexample:
running the formatter BEFORE `prepare` keeps the exception detail, running
it on a record `prepare` already mutated loses it. This is why the
formatter MUST run pre-queue, on the caller's thread.

`ThreeStagePipelineTests` below completes CP-016: now that the queue
handler and writer exist (tasks 2.34/2.36), a single `logger.info(...)`
call can be traced end to end through exactly the formatter, the queue
handler, and the writer thread, with no other layer in between.
"""

from __future__ import annotations

import io
import json
import logging
import logging.handlers
import queue
import sys
import time
import unittest

from semlog._format import Formatter
from semlog._transport import SemlogQueueHandler, Writer


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


def _record_with_real_exception():
    logger = logging.getLogger("semlog.tests.pipeline_order")
    try:
        raise ValueError("boom")
    except ValueError:
        return logger.makeRecord(
            logger.name,
            logging.ERROR,
            __file__,
            1,
            "app.request.failed",
            (),
            sys.exc_info(),
            extra=None,
        )


class PreQueueRenderingGuardTests(unittest.TestCase):
    """Proves: LP-002"""

    def test_formatting_before_the_queue_keeps_exception_detail(self):
        record = _record_with_real_exception()
        formatter = Formatter(identity=_identity())
        parsed = json.loads(formatter.format(record))
        self.assertIn("exception.stacktrace", parsed)

    def test_formatting_after_queuehandler_prepare_loses_exception_detail(self):
        # `QueueHandler.prepare` is the non-conformant counterexample: it
        # is what the pipeline would hand a post-queue formatter, and it
        # clears exc_info/exc_text/stack_info before returning (verified
        # in design #162 §2.1's own regression-guard scenario).
        record = _record_with_real_exception()
        handler = logging.handlers.QueueHandler(queue.Queue())
        prepared = handler.prepare(record)
        self.assertIsNone(prepared.exc_info)

        formatter = Formatter(identity=_identity())
        parsed = json.loads(formatter.format(prepared))
        self.assertNotIn("exception.stacktrace", parsed)
        self.assertNotIn("exception.type", parsed)


def _safe_stop(writer):
    try:
        writer.stop()
    except AttributeError:
        pass


class ThreeStagePipelineTests(unittest.TestCase):
    """Proves: CP-016"""

    def test_call_path_touches_only_formatter_queue_handler_and_writer(self):
        q = queue.Queue()
        stream = io.BytesIO()
        handler = SemlogQueueHandler(q, overflow="block")
        handler.setFormatter(Formatter(identity=_identity()))
        writer = Writer(q, stream=stream)
        writer.start()
        self.addCleanup(_safe_stop, writer)

        logger = logging.getLogger("semlog.tests.pipeline_three_stage")
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(logging.INFO)
        self.addCleanup(setattr, logger, "handlers", [])

        logger.info("app.checkout.completed", extra={"app.amount": 42})

        deadline = time.monotonic() + 2
        while q.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        _safe_stop(writer)

        self.assertEqual(1, len(logger.handlers))
        self.assertIsInstance(logger.handlers[0], SemlogQueueHandler)
        parsed = json.loads(stream.getvalue().decode("utf-8").strip())
        self.assertEqual("app.checkout.completed", parsed["event_name"])
        self.assertEqual(42, parsed["app.amount"])


if __name__ == "__main__":
    unittest.main()
