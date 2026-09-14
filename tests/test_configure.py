"""`configure()` entry point tests (design #162 §3.3/3.4, STANDARDS CP-015).

Covers the parameter-shape and idempotency behavior available before the
pipeline stages (formatter, queue handler, writer) exist -- keyword-only
parameters, no settings object/dict, invalid values raise at boot, calling
twice makes the last call win, and the catalog is accepted only as a
JSON-shaped document -- plus `PipelineWiringTests`, which closes the gap
flagged in engram #245: `configure()` resolved identity/config but never
called `_transport.install()`, so a plain `logging.getLogger(...).info(...)`
call after `configure()` reached no handler at all.
"""

from __future__ import annotations

import io
import json
import logging
import unittest
from unittest import mock

from semlog import _baggage, _config, _transport
from semlog._config import configure
from semlog._middleware import WSGIMiddleware

from ._pipeline_support import FakeStdout, reset_pipeline
from ._wsgi_support import environ, recording_start_response


class ConfigureEntryPointTests(unittest.TestCase):
    """Proves: CP-015"""

    def setUp(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            configure(search_dir=".")

    def tearDown(self):
        reset_pipeline()

    def test_parameters_are_keyword_only(self):
        with self.assertRaises(TypeError):
            configure("svc-a")  # type: ignore[misc]

    def test_no_settings_object_or_dict_accepted(self):
        with self.assertRaises(TypeError):
            configure(settings={"service_name": "svc-a"})  # type: ignore[call-arg]

    def test_invalid_catalog_mode_raises_value_error(self):
        with (
            mock.patch.dict("os.environ", {}, clear=True),
            self.assertRaises(ValueError),
        ):
            configure(catalog_mode="bogus", search_dir=".")

    def test_catalog_must_be_a_json_shaped_document(self):
        class NotADocument:
            pass

        with (
            mock.patch.dict("os.environ", {}, clear=True),
            self.assertRaises(TypeError),
        ):
            configure(catalog=NotADocument(), search_dir=".")

    def test_catalog_accepted_as_plain_dict(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            configure(
                catalog={"events": {"app.started": {}}},
                catalog_mode="warn",
                search_dir=".",
            )
        self.assertEqual("warn", _config.current().catalog_mode)

    def test_calling_configure_twice_last_call_wins(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            configure(service_name="svc-a", search_dir=".")
            configure(service_name="svc-b", search_dir=".")
        self.assertEqual("svc-b", _config.current().identity["service.name"])

    def test_default_catalog_mode_is_off_without_a_catalog(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            configure(search_dir=".")
        self.assertEqual("off", _config.current().catalog_mode)

    def test_default_catalog_mode_is_warn_with_a_catalog(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            configure(catalog={"events": {}}, search_dir=".")
        self.assertEqual("warn", _config.current().catalog_mode)


class ExplicitRootLevelTests(unittest.TestCase):
    """Proves: LP-010"""

    def tearDown(self):
        reset_pipeline()  # restores the stdlib default level too

    def test_default_level_is_info_not_the_stdlib_silent_warning_default(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            configure(search_dir=".")
        root = logging.getLogger()
        self.assertEqual(logging.INFO, root.getEffectiveLevel())
        self.assertTrue(root.isEnabledFor(logging.INFO))

    def test_explicit_level_overrides_the_default(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            configure(level="DEBUG", search_dir=".")
        self.assertEqual(logging.DEBUG, logging.getLogger().getEffectiveLevel())

    def test_invalid_level_raises_value_error_at_boot(self):
        with (
            mock.patch.dict("os.environ", {}, clear=True),
            self.assertRaises(ValueError),
        ):
            configure(level="NOT_A_LEVEL", search_dir=".")


class PipelineWiringTests(unittest.TestCase):
    """Regression tests closing the gap flagged in engram #245: `configure()`
    resolved identity/config and set the root level, but never installed the
    formatter/queue-handler/writer pipeline onto the root logger, so a plain
    `logging.getLogger(...).info(...)` call after `configure()` reached no
    handler at all."""

    def tearDown(self):
        reset_pipeline()

    def test_configure_then_log_call_emits_one_json_line_on_stdout(self):
        fake_stdout = FakeStdout()
        with (
            mock.patch("sys.stdout", fake_stdout),
            mock.patch.dict("os.environ", {}, clear=True),
        ):
            configure(service_name="wiring-test", search_dir=".")
            logging.getLogger("semlog.tests.wiring").info("app.wired")
            _transport.flush(timeout=2)

        lines = fake_stdout.buffer.getvalue().decode("utf-8").strip().splitlines()
        self.assertEqual(1, len(lines))
        record = json.loads(lines[0])
        self.assertEqual("app.wired", record["event_name"])
        self.assertEqual("wiring-test", record["service.name"])

    def test_configure_called_twice_does_not_stack_handlers(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            configure(service_name="svc-a", search_dir=".")
            configure(service_name="svc-b", search_dir=".")
        root = logging.getLogger()
        self.assertEqual(1, len(root.handlers))
        self.assertIsInstance(root.handlers[0], _transport.SemlogQueueHandler)


def _logging_app(environ, start_response):
    start_response("200 OK", [("Content-Type", "text/plain")])
    logging.getLogger("semlog.tests.configure.baggage").info("app.request.handled")
    return [b""]


class CaptureLoggersParameterTests(unittest.TestCase):
    """Engram #255 gap closure: `capture_loggers` wiring.

    `configure(capture_loggers=(...))` removes that logger's own handlers
    and turns propagation on, so its records reach the pipeline `configure()`
    just installed instead of the handler it originally owned. Not tied to
    a single STANDARDS.md requirement id (same precedent as
    `tests/test_class_budget.py`/`test_source_budget.py`: no `Proves:` line
    when no id cleanly backs the behavior)."""

    def tearDown(self):
        reset_pipeline()
        noisy = logging.getLogger("noisy.lib")
        noisy.handlers = []
        noisy.propagate = True

    def test_capture_loggers_removes_its_own_handler_and_reaches_the_pipeline(self):
        noisy = logging.getLogger("noisy.lib")
        own_sink = io.StringIO()
        noisy.addHandler(logging.StreamHandler(own_sink))
        noisy.propagate = False

        fake_stdout = FakeStdout()
        with (
            mock.patch("sys.stdout", fake_stdout),
            mock.patch.dict("os.environ", {}, clear=True),
        ):
            configure(
                service_name="capture-test",
                capture_loggers=("noisy.lib",),
                search_dir=".",
            )
            noisy.info("app.noisy.thing")
            _transport.flush(timeout=2)

        self.assertEqual([], noisy.handlers)
        self.assertTrue(noisy.propagate)
        self.assertEqual("", own_sink.getvalue())
        line = json.loads(fake_stdout.buffer.getvalue().decode("utf-8").strip())
        self.assertEqual("app.noisy.thing", line["event_name"])


class RedactKeysParameterTests(unittest.TestCase):
    """Engram #255 gap closure: `redact_keys` wiring.

    Proves: LP-005"""

    def tearDown(self):
        reset_pipeline()

    def test_redact_keys_redacts_the_configured_key_in_the_emitted_line(self):
        fake_stdout = FakeStdout()
        with (
            mock.patch("sys.stdout", fake_stdout),
            mock.patch.dict("os.environ", {}, clear=True),
        ):
            configure(
                service_name="redact-test", redact_keys=("app.card",), search_dir="."
            )
            logging.getLogger("semlog.tests.redact").info(
                "app.charge.attempted", extra={"app.card": "4111111111111111"}
            )
            _transport.flush(timeout=2)

        line = json.loads(fake_stdout.buffer.getvalue().decode("utf-8").strip())
        self.assertEqual("REDACTED", line["app.card"])


class BaggageConfigurationTests(unittest.TestCase):
    """Engram #255 gap closure: `baggage_allow`/`baggage_prefix`/
    `accept_inbound_baggage` wiring.

    `baggage_allow`/`baggage_prefix` become the process-wide default the
    middlewares fall back to when they are not given their own explicit
    value (`WSGIMiddleware(app)` below passes neither).

    Proves: TCP-007"""

    def tearDown(self):
        reset_pipeline()
        _baggage.defaults.accept_inbound_baggage = True
        _baggage.defaults.baggage_allow = ()

    def test_configured_baggage_allow_and_prefix_reach_the_emitted_line(self):
        fake_stdout = FakeStdout()
        with (
            mock.patch("sys.stdout", fake_stdout),
            mock.patch.dict("os.environ", {}, clear=True),
        ):
            configure(
                service_name="baggage-test",
                baggage_allow=("tenant",),
                baggage_prefix="ctx.",
                search_dir=".",
            )
            middleware = WSGIMiddleware(_logging_app)
            env = environ(headers={"baggage": "tenant=acme,debug=1"})
            middleware(env, recording_start_response())
            _transport.flush(timeout=2)

        line = json.loads(fake_stdout.buffer.getvalue().decode("utf-8").strip())
        self.assertEqual("acme", line["ctx.tenant"])
        self.assertNotIn("ctx.debug", line)

    def test_accept_inbound_baggage_false_ignores_the_header_entirely(self):
        fake_stdout = FakeStdout()
        with (
            mock.patch("sys.stdout", fake_stdout),
            mock.patch.dict("os.environ", {}, clear=True),
        ):
            configure(
                service_name="baggage-test-2",
                baggage_allow=("tenant",),
                accept_inbound_baggage=False,
                search_dir=".",
            )
            middleware = WSGIMiddleware(_logging_app)
            env = environ(headers={"baggage": "tenant=acme"})
            middleware(env, recording_start_response())
            _transport.flush(timeout=2)

        line = json.loads(fake_stdout.buffer.getvalue().decode("utf-8").strip())
        self.assertNotIn("baggage.tenant", line)
        self.assertNotIn("ctx.tenant", line)


def _drop_record(level, event):
    logger = logging.getLogger("semlog.tests.configure.transport")
    return logger.makeRecord(logger.name, level, __file__, 1, event, (), None)


class TransportParameterTests(unittest.TestCase):
    """Engram #255 gap closure: `queue`/`queue_size`/`overflow` wiring."""

    def tearDown(self):
        reset_pipeline()

    def test_queue_false_writes_synchronously_with_no_writer_thread(self):
        fake_stdout = FakeStdout()
        with (
            mock.patch("sys.stdout", fake_stdout),
            mock.patch.dict("os.environ", {}, clear=True),
        ):
            configure(service_name="sync-test", queue=False, search_dir=".")
            logging.getLogger("semlog.tests.sync").info("app.sync.written")
            # No flush() call: queue=False must already be synchronous.
            self.assertIsNone(_transport._state.writer)

        line = json.loads(fake_stdout.buffer.getvalue().decode("utf-8").strip())
        self.assertEqual("app.sync.written", line["event_name"])

    def test_overflow_drop_with_a_tiny_queue_size_drops_info_keeps_warning(self):
        """Proves: LP-007"""
        with mock.patch.dict("os.environ", {}, clear=True):
            configure(
                service_name="drop-test", queue_size=10, overflow="drop", search_dir="."
            )
        handler = logging.getLogger().handlers[0]
        self.assertIsInstance(handler, _transport.SemlogQueueHandler)
        self.assertEqual(10, handler.queue.maxsize)
        # Deterministic (no writer thread, no sleep-based race): this proves
        # `configure()` wired `queue_size`/`overflow` into the real installed
        # handler, driving the same overflow logic `test_transport.py` proves
        # in isolation.
        handler._on_first_emit = None
        for i in range(9):
            handler.emit(_drop_record(logging.INFO, f"app.kept.{i}"))
        self.assertEqual(9, handler.queue.qsize())  # 90% full

        handler.emit(_drop_record(logging.DEBUG, "app.dropped.debug"))
        self.assertEqual(9, handler.queue.qsize())
        self.assertEqual(1, handler.counters.dropped)

        handler.emit(_drop_record(logging.WARNING, "app.kept.warning"))
        self.assertEqual(10, handler.queue.qsize())  # the reserved 10% slot
        self.assertEqual(1, handler.counters.dropped)


class ConfigureValidationTests(unittest.TestCase):
    """Engram #255 gap closure: strict validation for the new transport
    parameters, matching the README's "falla de inmediato con ValueError"
    contract."""

    def tearDown(self):
        reset_pipeline()

    def test_invalid_overflow_raises_value_error_at_boot(self):
        with (
            mock.patch.dict("os.environ", {}, clear=True),
            self.assertRaises(ValueError),
        ):
            configure(overflow="bogus", search_dir=".")

    def test_non_positive_queue_size_raises_value_error_at_boot(self):
        with (
            mock.patch.dict("os.environ", {}, clear=True),
            self.assertRaises(ValueError),
        ):
            configure(queue_size=0, search_dir=".")


if __name__ == "__main__":
    unittest.main()
