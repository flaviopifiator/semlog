"""Hybrid mode routing and marker-handling tests.

In `hybrid` mode, `configure()` never touches the root logger's handlers
or level. A record logged with `semlog=True` reaches semlog's own JSON
pipeline exactly once, hidden only from `logging.StreamHandler` instances
and subclasses during the one dispatch pass that follows; every other
handler (an "observer", any `logging.Handler` subclass that is not a
`StreamHandler` subclass) still receives the record unchanged, marker
already gone. An unmarked call prints byte-identical output to a process
where semlog was never imported.

LM-003 (STANDARDS.md, WU6); the relevant classes below carry their own
`Proves: LM-003` docstring line.
"""

from __future__ import annotations

import io
import json
import logging
import logging.handlers
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from semlog import _modes, _transport
from semlog._config import configure
from semlog._middleware import WSGIMiddleware

from ._pipeline_support import FakeStdout, reset_pipeline
from ._wsgi_support import environ, recording_start_response


class _Observer(logging.Handler):
    """A plain, non-`StreamHandler` handler: LM-003's "observer" example
    (an error-tracking-style integration)."""

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _reset_state():
    reset_pipeline()
    _modes.state.mode = "full"
    _modes.state.marking = False


def _hybrid_configure(**kwargs):
    with mock.patch.dict("os.environ", {}, clear=True):
        configure(mode="hybrid", service_name="svc", search_dir=".", **kwargs)


class HybridRoutingTests(unittest.TestCase):
    """Proves: LM-003"""

    def tearDown(self):
        _reset_state()

    def test_root_handlers_and_level_untouched(self):
        root = logging.getLogger()
        sentinel = logging.StreamHandler(io.StringIO())
        root.addHandler(sentinel)
        root.setLevel(logging.WARNING)
        try:
            _hybrid_configure()
            self.assertEqual([sentinel], root.handlers)
            self.assertEqual(logging.WARNING, root.level)
        finally:
            root.removeHandler(sentinel)
            root.setLevel(logging.WARNING)

    def test_marked_record_emitted_as_json_exactly_once(self):
        fake_stdout = FakeStdout()
        with mock.patch("sys.stdout", fake_stdout):
            _hybrid_configure()
            logger = logging.getLogger("semlog.tests.hybrid.json")
            logger.setLevel(logging.INFO)  # hybrid never touches root's level
            logger.info("app.hybrid.event", extra={"app.a": 1}, semlog=True)
            _transport.flush(timeout=2)
        lines = fake_stdout.buffer.getvalue().decode("utf-8").strip().splitlines()
        self.assertEqual(1, len(lines))
        record = json.loads(lines[0])
        self.assertEqual("app.hybrid.event", record["event_name"])
        self.assertEqual(1, record["app.a"])

    def test_marked_record_hidden_from_streamhandler(self):
        fake_stdout = FakeStdout()
        with mock.patch("sys.stdout", fake_stdout):
            _hybrid_configure()
            captured = io.StringIO()
            logger = logging.getLogger("semlog.tests.hybrid.hidden")
            logger.handlers = [logging.StreamHandler(captured)]
            logger.propagate = False
            logger.setLevel(logging.INFO)
            self.addCleanup(setattr, logger, "handlers", [])
            logger.info("app.hidden", semlog=True)
            _transport.flush(timeout=2)
        self.assertEqual("", captured.getvalue())

    def test_marked_record_still_rendered_by_a_handle_override_without_super(self):
        """LM-004 (validate-wu69 #338, m2): the suppression patch lives on
        `StreamHandler.handle` itself; a subclass that overrides `handle()`
        WITHOUT calling `super().handle()` never reaches the patched
        method, so it still renders a marked record as text -- a
        documented limitation, not a defect. A sibling subclass that DOES
        call `super().handle()` stays correctly suppressed."""
        fake_stdout = FakeStdout()
        with mock.patch("sys.stdout", fake_stdout):
            _hybrid_configure()
            captured_override, captured_super = io.StringIO(), io.StringIO()

            class _OverridingHandler(logging.StreamHandler):
                def handle(self, record):
                    # Bypasses the patched base-class method entirely.
                    self.stream.write(self.format(record) + "\n")
                    return True

            class _SuperCallingHandler(logging.StreamHandler):
                def handle(self, record):
                    return super().handle(record)

            overriding = _OverridingHandler(captured_override)
            super_calling = _SuperCallingHandler(captured_super)
            logger = logging.getLogger("semlog.tests.hybrid.override")
            logger.handlers = [overriding, super_calling]
            logger.propagate = False
            logger.setLevel(logging.INFO)
            self.addCleanup(setattr, logger, "handlers", [])
            logger.info("app.override.marked", semlog=True)
            _transport.flush(timeout=2)
        self.assertIn("app.override.marked", captured_override.getvalue())
        self.assertEqual("", captured_super.getvalue())

    def test_marked_record_hidden_from_filehandler(self):
        fake_stdout = FakeStdout()
        with mock.patch("sys.stdout", fake_stdout):
            _hybrid_configure()
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "out.log"
                file_handler = logging.FileHandler(path)
                logger = logging.getLogger("semlog.tests.hybrid.filehandler")
                logger.handlers = [file_handler]
                logger.propagate = False
                logger.setLevel(logging.INFO)
                try:
                    logger.info("app.hidden.file", semlog=True)
                    _transport.flush(timeout=2)
                finally:
                    logger.handlers = []
                    file_handler.close()
                self.assertEqual("", path.read_text(encoding="utf-8"))

    def test_marked_record_hidden_from_last_resort(self):
        # lastResort is a module-level _StderrHandler (a StreamHandler
        # subclass): tested directly against the dispatch identity, since
        # relying on "no handler anywhere in this shared test process"
        # would be fragile. Proves the patch reaches it through normal
        # StreamHandler.handle inheritance, same as FileHandler above.
        # Routing must be armed by this test itself (MINOR M2,
        # validate-wu25 #333): `logging.StreamHandler.handle` is only
        # wrapped after a hybrid configure(), so this test passed only by
        # accident when an earlier test in the same run had already armed
        # it; run alone, it must still pass.
        _hybrid_configure()
        logger = logging.getLogger("semlog.tests.hybrid.lastresort")
        record = logger.makeRecord(
            logger.name, logging.INFO, __file__, 1, "app.marked", (), None
        )
        previous = getattr(_modes._dispatching, "record", None)
        _modes._dispatching.record = record
        try:
            with mock.patch("sys.stderr", io.StringIO()) as captured:
                result = logging.lastResort.handle(record)
            self.assertFalse(result)
            self.assertEqual("", captured.getvalue())
        finally:
            _modes._dispatching.record = previous

    def test_observer_handler_still_receives_the_marked_record_unchanged(self):
        fake_stdout = FakeStdout()
        with mock.patch("sys.stdout", fake_stdout):
            _hybrid_configure()
            observer = _Observer()
            logger = logging.getLogger("semlog.tests.hybrid.observer")
            logger.handlers = [observer]
            logger.propagate = False
            logger.setLevel(logging.INFO)
            self.addCleanup(setattr, logger, "handlers", [])
            logger.info("app.observed", extra={"app.a": 1}, semlog=True)
            _transport.flush(timeout=2)
        self.assertEqual(1, len(observer.records))
        record = observer.records[0]
        self.assertEqual(1, record.__dict__["app.a"])
        self.assertNotIn(_modes.MARKER, vars(record))

    def test_unmarked_call_still_reaches_the_streamhandler(self):
        _hybrid_configure()
        captured = io.StringIO()
        logger = logging.getLogger("semlog.tests.hybrid.unmarked")
        logger.handlers = [logging.StreamHandler(captured)]
        logger.propagate = False
        logger.setLevel(logging.INFO)
        self.addCleanup(setattr, logger, "handlers", [])
        logger.info("app.unmarked.visible")
        self.assertIn("app.unmarked.visible", captured.getvalue())

    def test_nested_marked_record_inside_a_handler_keeps_outer_hiding(self):
        fake_stdout = FakeStdout()
        with mock.patch("sys.stdout", fake_stdout):
            _hybrid_configure()
            outer_captured = io.StringIO()
            inner_logger = logging.getLogger("semlog.tests.hybrid.nested.inner")
            inner_captured = io.StringIO()
            inner_logger.handlers = [logging.StreamHandler(inner_captured)]
            inner_logger.propagate = False
            inner_logger.setLevel(logging.INFO)
            self.addCleanup(setattr, inner_logger, "handlers", [])

            class _LoggingObserver(logging.Handler):
                def emit(self, record):
                    inner_logger.info("app.inner", semlog=True)

            outer_logger = logging.getLogger("semlog.tests.hybrid.nested.outer")
            outer_stream_handler = logging.StreamHandler(outer_captured)
            outer_logger.handlers = [_LoggingObserver(), outer_stream_handler]
            outer_logger.propagate = False
            outer_logger.setLevel(logging.INFO)
            self.addCleanup(setattr, outer_logger, "handlers", [])

            outer_logger.info("app.outer", semlog=True)
            _transport.flush(timeout=2)

        # Both the outer and the (nested) inner marked record are hidden
        # from their own StreamHandler, and dispatch identity is restored
        # after the nested call returns, not leaked into the outer one.
        self.assertEqual("", outer_captured.getvalue())
        self.assertEqual("", inner_captured.getvalue())

    def test_pre_configure_hybrid_prints_like_unmarked(self):
        # SEMLOG_MODE=hybrid alone (no configure() call) never arms
        # marking or routing: a marked call must print exactly like an
        # unmarked one.
        captured = io.StringIO()
        logger = logging.getLogger("semlog.tests.hybrid.preconfigure")
        logger.handlers = [logging.StreamHandler(captured)]
        logger.propagate = False
        logger.setLevel(logging.INFO)
        self.addCleanup(setattr, logger, "handlers", [])
        with mock.patch.dict("os.environ", {"SEMLOG_MODE": "hybrid"}, clear=True):
            self.assertEqual("hybrid", _modes.resolve_mode(None))
            logger.info("app.preconfigure", semlog=True)
        self.assertIn("app.preconfigure", captured.getvalue())

    def test_capture_loggers_with_hybrid_raises_value_error(self):
        with (
            mock.patch.dict("os.environ", {}, clear=True),
            self.assertRaises(ValueError),
        ):
            configure(
                mode="hybrid",
                service_name="svc",
                capture_loggers=("noisy.lib",),
                search_dir=".",
            )

    def test_marked_below_effective_level_produces_nothing(self):
        fake_stdout = FakeStdout()
        with mock.patch("sys.stdout", fake_stdout):
            _hybrid_configure()
            logger = logging.getLogger("semlog.tests.hybrid.filtered")
            logger.setLevel(logging.WARNING)
            logger.info("app.filtered", semlog=True)
            _transport.flush(timeout=2)
        self.assertEqual(b"", fake_stdout.buffer.getvalue())

    def test_exactly_once_including_a_manual_redispatch(self):
        fake_stdout = FakeStdout()
        with mock.patch("sys.stdout", fake_stdout):
            _hybrid_configure()
            logger = logging.getLogger("semlog.tests.hybrid.redispatch")
            record = logger.makeRecord(
                logger.name,
                logging.INFO,
                __file__,
                1,
                "app.redispatch",
                (),
                None,
                extra={_modes.MARKER: True},
            )
            logger.handle(record)  # first dispatch: routes once, pops MARKER
            logger.handle(record)  # re-dispatch of the SAME object: no marker left
            _transport.flush(timeout=2)
        lines = fake_stdout.buffer.getvalue().decode("utf-8").strip().splitlines()
        self.assertEqual(1, len(lines))

    def test_third_party_callhandlers_wrapper_before_and_after(self):
        # Simulates a third-party layer (for example a test framework's log
        # capture fixture) that itself wraps Logger.callHandlers around
        # semlog's own wrapper: calls must still reach the real dispatch,
        # and hybrid routing must still work through it.
        fake_stdout = FakeStdout()
        with mock.patch("sys.stdout", fake_stdout):
            _hybrid_configure()
            semlog_wrapper = logging.Logger.callHandlers
            calls = []

            def third_party_wrapper(self, record):
                calls.append(record)
                return semlog_wrapper(self, record)

            logging.Logger.callHandlers = third_party_wrapper
            try:
                logger = logging.getLogger("semlog.tests.hybrid.thirdparty")
                logger.setLevel(logging.INFO)
                logger.info("app.wrapped", semlog=True)
                _transport.flush(timeout=2)
            finally:
                logging.Logger.callHandlers = semlog_wrapper
        self.assertEqual(1, len(calls))
        lines = fake_stdout.buffer.getvalue().decode("utf-8").strip().splitlines()
        self.assertEqual(1, len(lines))
        self.assertEqual("app.wrapped", json.loads(lines[0])["event_name"])


def _captured_logger(name):
    logger = logging.getLogger(name)
    captured = io.StringIO()
    logger.handlers = [logging.StreamHandler(captured)]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    return logger, captured


class HybridSemlogOwnedRecordsTests(unittest.TestCase):
    """MINOR M4 (validate-wu25 #333): three LM-003 "semlog-owned records
    route to JSON only, never legacy text" scenarios had no direct test,
    so a mutant reverting any of them to immediate/unmarked emission
    survived unnoticed: the deferred SI-005 pyproject diagnostic (erratum
    8), the `bind_ignored` out-of-scope diagnostic, and the
    catalog-violation warning.

    Proves: LM-003
    """

    def tearDown(self):
        _reset_state()

    def test_si005_pyproject_diagnostic_is_json_only_in_hybrid(self):
        fake_stdout = FakeStdout()
        logger, captured = _captured_logger("semlog")
        try:
            with (
                tempfile.TemporaryDirectory() as tmp,
                mock.patch("sys.stdout", fake_stdout),
                mock.patch.dict("os.environ", {}, clear=True),
            ):
                configure(mode="hybrid", search_dir=tmp)
                _transport.flush(timeout=2)
        finally:
            logger.handlers = []
            logger.propagate = True
        self.assertEqual("", captured.getvalue())
        lines = fake_stdout.buffer.getvalue().decode("utf-8").strip().splitlines()
        self.assertEqual(1, len(lines))
        record = json.loads(lines[0])
        self.assertIn("pyproject.toml", record["body"])

    def test_bind_ignored_diagnostic_is_json_only_in_hybrid(self):
        from semlog._context import bind

        fake_stdout = FakeStdout()
        logger, captured = _captured_logger("semlog")
        try:
            with (
                mock.patch("sys.stdout", fake_stdout),
                mock.patch.dict("os.environ", {}, clear=True),
            ):
                _hybrid_configure()
                bind({"app.a": 1})  # out of scope: TCP-012's once-per-process warning
                _transport.flush(timeout=2)
        finally:
            logger.handlers = []
            logger.propagate = True
        self.assertEqual("", captured.getvalue())
        lines = fake_stdout.buffer.getvalue().decode("utf-8").strip().splitlines()
        self.assertEqual(1, len(lines))
        record = json.loads(lines[0])
        self.assertEqual("semlog.log.bind_ignored", record["event_name"])

    def test_catalog_violation_warning_is_json_only_in_hybrid(self):
        fake_stdout = FakeStdout()
        logger, captured = _captured_logger("semlog")
        try:
            with (
                mock.patch("sys.stdout", fake_stdout),
                mock.patch.dict("os.environ", {}, clear=True),
            ):
                configure(
                    mode="hybrid",
                    service_name="svc",
                    search_dir=".",
                    catalog={"events": {"app.known": {}}},
                    catalog_mode="warn",
                )
                app_logger = logging.getLogger("semlog.tests.hybrid.catalog")
                app_logger.setLevel(logging.INFO)
                app_logger.info("app.undeclared", semlog=True)
                _transport.flush(timeout=2)
        finally:
            logger.handlers = []
            logger.propagate = True
        self.assertEqual("", captured.getvalue())
        lines = fake_stdout.buffer.getvalue().decode("utf-8").strip().splitlines()
        # Both the app's own marked record and the catalog-violation
        # warning must land in JSON, and neither on the "semlog" StreamHandler.
        self.assertEqual(2, len(lines))
        bodies = [json.loads(line).get("body") for line in lines]
        self.assertTrue(any(body and "catalog_violation" in body for body in bodies))


class HybridConfigureRepeatTests(unittest.TestCase):
    """A repeat `configure(mode="hybrid")` call that raises during
    validation must leave `state.marking` and routing exactly as the
    first successful call left them (validation-4 minor).

    Proves: LM-003
    """

    def tearDown(self):
        _reset_state()

    def test_a_raising_repeat_hybrid_configure_leaves_marking_and_routing_intact(self):
        _hybrid_configure()
        self.assertTrue(_modes.state.marking)
        self.assertTrue(_modes._routing_armed)

        with (
            mock.patch.dict("os.environ", {}, clear=True),
            self.assertRaises(ValueError),
        ):
            configure(mode="hybrid", level="NOT_A_LEVEL", search_dir=".")

        self.assertTrue(_modes.state.marking)
        self.assertEqual("hybrid", _modes.state.mode)
        self.assertTrue(_modes._routing_armed)


class HybridByteIdentityTests(unittest.TestCase):
    """Subprocess A/B: an unmarked call's `StreamHandler` output must be
    byte-identical whether or not semlog was ever imported, using
    `%(funcName)s`/`%(lineno)d` so any stacklevel drift would be caught.

    Proves: LM-003
    """

    def test_unmarked_streamhandler_output_matches_a_process_without_semlog(self):
        src_dir = str(Path(__file__).resolve().parents[1] / "src")
        fmt = "%(funcName)s:%(lineno)d:%(levelname)s:%(message)s"
        semlog_setup = [
            "import sys",
            f"sys.path.insert(0, {src_dir!r})",
            "import semlog",
            "semlog.configure(mode='hybrid', service_name='svc', search_dir='.')",
        ]
        baseline_setup = [""] * len(semlog_setup)

        def build(setup_lines):
            lines = [
                "import logging",
                *setup_lines,
                f"logging.basicConfig(level=logging.INFO, format={fmt!r})",
                'logger = logging.getLogger("app")',
                "",
                "",
                "def emit():",
                '    logger.info("app.event")',
                "",
                "",
                "emit()",
            ]
            return "\n".join(lines) + "\n"

        baseline = subprocess.run(
            [sys.executable, "-c", build(baseline_setup)],
            capture_output=True,
            timeout=15,
            check=False,
        )
        with_semlog = subprocess.run(
            [sys.executable, "-c", build(semlog_setup)],
            capture_output=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(0, baseline.returncode, baseline.stderr)
        self.assertEqual(0, with_semlog.returncode, with_semlog.stderr)
        self.assertEqual(baseline.stderr, with_semlog.stderr)
        self.assertIn(b"emit:", baseline.stderr)


def _logging_app(environ, start_response):
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [b""]


class HybridRequestEventTests(unittest.TestCase):
    """Proves: HTM-007"""

    def setUp(self):
        self._middleware_logger = logging.getLogger("semlog._middleware")
        self._original_handlers = list(self._middleware_logger.handlers)
        self._original_propagate = self._middleware_logger.propagate
        self._original_level = self._middleware_logger.level

    def tearDown(self):
        self._middleware_logger.handlers = self._original_handlers
        self._middleware_logger.propagate = self._original_propagate
        self._middleware_logger.setLevel(self._original_level)
        _reset_state()

    def test_http_server_request_routes_to_json_only_in_hybrid(self):
        fake_stdout = FakeStdout()
        captured = io.StringIO()
        self._middleware_logger.handlers = [logging.StreamHandler(captured)]
        self._middleware_logger.propagate = False
        self._middleware_logger.setLevel(logging.INFO)
        with mock.patch("sys.stdout", fake_stdout):
            _hybrid_configure()
            middleware = WSGIMiddleware(_logging_app, log_requests=True)
            # The completion event fires only once the response iterable
            # is fully consumed (HTM-007/HTM-003).
            list(middleware(environ(), recording_start_response()))
            _transport.flush(timeout=2)
        self.assertEqual("", captured.getvalue())
        lines = fake_stdout.buffer.getvalue().decode("utf-8").strip().splitlines()
        self.assertEqual(1, len(lines))
        self.assertEqual("http.server.request", json.loads(lines[0])["event_name"])


if __name__ == "__main__":
    unittest.main()
