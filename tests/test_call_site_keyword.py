"""The `semlog=True` call-site keyword and caller-metadata preservation.

`_modes.py` patches `logging.Logger._log` at import time (before
`configure()` ever runs) so every `logging.Logger` method accepts the
keyword-only `semlog=True` argument in every mode, without ever raising.
The patch bumps `stacklevel` by exactly one, to compensate for its own
extra call frame between the caller and the stdlib's real `_log`, so
caller-visible `LogRecord` metadata (`filename`, `lineno`, `funcName`,
`module`) stays identical to the same call site without the keyword.

No `Proves:` line yet: LM-002 is not declared in STANDARDS.md until Phase
7 of this change (same deferred-citation precedent as
`test_class_budget.py`/`test_source_budget.py`); tag the relevant classes
below `Proves: LM-002` once that declaration lands.
"""

from __future__ import annotations

import logging
import sys
import unittest
from unittest import mock

from semlog import _modes, _transport
from semlog._config import configure

from ._pipeline_support import FakeStdout, reset_pipeline


class _CapturingHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _bound_logger(name):
    logger = logging.getLogger(name)
    handler = _CapturingHandler()
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    return logger, handler


class PreConfigureKeywordTests(unittest.TestCase):
    """The keyword must be accepted before `configure()` ever runs, since
    `_modes.py`'s patch arms at import time, not inside `configure()`."""

    def test_semlog_true_accepted_before_configure_is_ever_called(self):
        logger, handler = _bound_logger("semlog.tests.keyword.preconfigure")
        logger.info("app.startup.check", semlog=True)  # must not raise
        self.assertEqual(1, len(handler.records))


class EveryModeNeverRaisesTests(unittest.TestCase):
    def tearDown(self):
        reset_pipeline()
        _modes.state.mode = "full"
        _modes.state.marking = False

    def test_semlog_true_never_raises_in_any_mode_at_any_enabled_level(self):
        for mode in ("full", "hybrid", "off"):
            with self.subTest(mode=mode):
                fake_stdout = FakeStdout()
                with (
                    mock.patch("sys.stdout", fake_stdout),
                    mock.patch.dict("os.environ", {}, clear=True),
                ):
                    configure(mode=mode, search_dir=".")
                    logger, _handler = _bound_logger(f"semlog.tests.keyword.{mode}")
                    for level_name in ("debug", "info", "warning", "error", "critical"):
                        getattr(logger, level_name)("app.marked", semlog=True)
                    _transport.flush(timeout=2)
                reset_pipeline()
                _modes.state.mode = "full"
                _modes.state.marking = False


class ExtraNotMutatedTests(unittest.TestCase):
    def test_callers_extra_dict_object_keeps_exactly_its_original_keys(self):
        logger, _handler = _bound_logger("semlog.tests.keyword.extra")
        extra = {"app.a": 1}
        logger.info("app.extra.call", extra=extra, semlog=True)
        self.assertEqual({"app.a": 1}, extra)


class MarkerAbsenceTests(unittest.TestCase):
    """Before hybrid routing ever arms `state.marking` (WU4), the marker
    must never appear anywhere: not on the record, not in the caller's
    `extra`, regardless of mode."""

    def tearDown(self):
        reset_pipeline()
        _modes.state.mode = "full"
        _modes.state.marking = False

    def test_marker_absent_from_the_record_before_marking_is_armed(self):
        logger, handler = _bound_logger("semlog.tests.keyword.marker.unarmed")
        logger.info("app.marked", extra={"app.a": 1}, semlog=True)
        record = handler.records[0]
        self.assertNotIn(_modes.MARKER, vars(record))

    def test_marker_absent_in_every_already_wired_mode(self):
        for mode in ("full", "hybrid", "off"):
            with self.subTest(mode=mode):
                fake_stdout = FakeStdout()
                with (
                    mock.patch("sys.stdout", fake_stdout),
                    mock.patch.dict("os.environ", {}, clear=True),
                ):
                    configure(mode=mode, search_dir=".")
                    logger, handler = _bound_logger(
                        f"semlog.tests.keyword.marker.{mode}"
                    )
                    logger.info("app.marked", semlog=True)
                    _transport.flush(timeout=2)
                if handler.records:
                    self.assertNotIn(_modes.MARKER, vars(handler.records[0]))
                reset_pipeline()
                _modes.state.mode = "full"
                _modes.state.marking = False


class MarkingMechanismTests(unittest.TestCase):
    """Direct, low-level proof that the `_log` patch attaches the marker
    exactly when `state.marking` is armed -- independent of hybrid's own
    routing wrapper (WU4), which is the thing that later pops it again
    before any handler runs."""

    def tearDown(self):
        _modes.state.marking = False

    def test_marker_attached_when_marking_is_armed_and_semlog_true(self):
        logger, handler = _bound_logger("semlog.tests.keyword.marker.armed")
        _modes.state.marking = True
        logger.info("app.marked", semlog=True)
        record = handler.records[0]
        self.assertTrue(record.__dict__.get(_modes.MARKER))

    def test_marker_absent_when_marking_is_armed_but_semlog_false(self):
        logger, handler = _bound_logger("semlog.tests.keyword.marker.armed.unmarked")
        _modes.state.marking = True
        logger.info("app.unmarked")
        record = handler.records[0]
        self.assertNotIn(_modes.MARKER, vars(record))

    def test_marker_never_mutates_the_callers_extra_even_when_armed(self):
        logger, _handler = _bound_logger("semlog.tests.keyword.marker.armed.extra")
        extra = {"app.a": 1}
        _modes.state.marking = True
        logger.info("app.marked", extra=extra, semlog=True)
        self.assertEqual({"app.a": 1}, extra)


def _direct_twin(logger, semlog):
    logger.info("app.twin.direct", semlog=semlog)


def _log_method_twin(logger, semlog):
    logger.log(logging.INFO, "app.twin.log", semlog=semlog)


def _exception_twin(logger, semlog):
    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("app.twin.exception", semlog=semlog)


def _adapter_twin(logger, semlog):
    adapter = logging.LoggerAdapter(logger, {})
    adapter.info("app.twin.adapter", stacklevel=2, semlog=semlog)


def _helper_twin(logger, semlog):
    def _via_helper():
        logger.info("app.twin.helper", stacklevel=2, semlog=semlog)

    _via_helper()


#: MINOR M3 (validate-wu25 #333): twin-vs-twin equality alone cannot
#: detect a `stacklevel` offset applied uniformly to both sides, since
#: both the marked and unmarked call go through the SAME patched `_log`
#: -- a mutant shifting both by the same wrong amount still leaves them
#: equal to each other. Each expected `funcName` below was derived by
#: running the real, unpatched stdlib frame-walking for this exact call
#: shape (confirmed by direct measurement on 3.10-3.14, matching the
#: 3.10-only `LoggerAdapter` quirk this module's docstring already
#: describes) and independently confirmed to kill both reported mutants
#: (`stacklevel + (1 if stacklevel == 1 else 0)` and
#: `stacklevel + (0 if stack_info or exc_info else 1)`) by applying each
#: to `_modes.py` and observing this exact assertion fail.
_EXPECTED_FUNC_NAME = {
    "_direct_twin": "_direct_twin",
    "_log_method_twin": "_log_method_twin",
    "_exception_twin": "_exception_twin",
    "_adapter_twin": "_twin_metadata"
    if sys.version_info >= (3, 11)
    else "_adapter_twin",
    "_helper_twin": "_helper_twin",
}


class CallerMetadataTwinTests(unittest.TestCase):
    """Metadata twins from one call site, executed once marked and once
    unmarked: `filename`/`lineno`/`funcName`/`module` must match between
    the two, on every supported Python version. Each case is ALSO checked
    against its known-correct absolute `funcName` (see
    `_EXPECTED_FUNC_NAME`), since twin equality alone cannot catch a
    uniform `stacklevel` offset bug (MINOR M3, validate-wu25 #333). The
    3.10 `LoggerAdapter` case resolves to a different (but still
    version-pinned, not twin-only) expectation, since 3.10's stdlib
    `LoggerAdapter` has its own pre-existing `stacklevel` quirk
    independent of this patch."""

    def _twin_metadata(self, twin_fn):
        # Both invocations come from the exact same call site (a loop body),
        # not two separately-written lines: this matters for a twin like
        # `_adapter_twin`, whose `stacklevel=2` walks past itself up to
        # *its own* caller -- this loop body -- so that caller's line must
        # be identical for both the marked and unmarked run.
        logger, handler = _bound_logger(f"semlog.tests.keyword.twin.{twin_fn.__name__}")
        for semlog in (True, False):
            twin_fn(logger, semlog)
        self.assertEqual(2, len(handler.records))
        marked, unmarked = handler.records

        def meta(record):
            return (record.filename, record.lineno, record.funcName, record.module)

        return meta(marked), meta(unmarked)

    def _assert_matches_twin_and_known_correct_func_name(self, twin_fn):
        marked, unmarked = self._twin_metadata(twin_fn)
        self.assertEqual(unmarked, marked)
        self.assertEqual(_EXPECTED_FUNC_NAME[twin_fn.__name__], marked[2])

    def test_direct_call_metadata_matches_its_twin(self):
        self._assert_matches_twin_and_known_correct_func_name(_direct_twin)

    def test_log_method_metadata_matches_its_twin(self):
        self._assert_matches_twin_and_known_correct_func_name(_log_method_twin)

    def test_exception_metadata_matches_its_twin(self):
        self._assert_matches_twin_and_known_correct_func_name(_exception_twin)

    def test_logger_adapter_metadata_matches_its_twin(self):
        self._assert_matches_twin_and_known_correct_func_name(_adapter_twin)

    def test_helper_wrapper_with_stacklevel_2_metadata_matches_its_twin(self):
        self._assert_matches_twin_and_known_correct_func_name(_helper_twin)


class ReArmIdempotenceTests(unittest.TestCase):
    """Idempotence of the `_log` patch across a REAL module reload (NIT,
    validate-wu25 #333) -- not just a direct `_arm_keyword()` call, which
    never actually exercises re-arming through the same path a defensive
    re-import would use. A real `importlib.reload(_modes)` always
    executes a fresh `def _log(...):`, so `logging.Logger._log` becomes a
    genuinely NEW function object every time; what must stay constant is
    `_semlog_original` resolving to the TRUE stdlib base, never to a
    previous wrapper, or a second reload after that would chain-wrap,
    breaking every caller-metadata guarantee above with a doubled
    `stacklevel` bump."""

    def setUp(self):
        # A reload resets the module-level routing-armed guard (once that
        # attribute exists) without touching an already-installed routing
        # wrapper; `getattr`/a conditional restore keep this test valid
        # across every commit in this change, whether or not that
        # attribute has landed yet, and never let it make a later hybrid
        # configure() double-wrap, whatever order the suite runs in.
        self._saved_routing_armed = getattr(_modes, "_routing_armed", None)

    def tearDown(self):
        if self._saved_routing_armed is not None:
            _modes._routing_armed = self._saved_routing_armed

    def test_reload_installs_a_fresh_wrapper_but_keeps_the_true_original(self):
        import importlib

        before_wrapper = logging.Logger._log
        true_original = before_wrapper._semlog_original
        importlib.reload(_modes)
        after_wrapper = logging.Logger._log
        self.assertIsNot(before_wrapper, after_wrapper)
        self.assertIs(true_original, after_wrapper._semlog_original)

    def test_metadata_still_correct_after_a_reload(self):
        import importlib

        importlib.reload(_modes)
        logger, handler = _bound_logger("semlog.tests.keyword.rearm")
        logger.info("app.after.rearm", semlog=True)
        record = handler.records[0]
        self.assertEqual(__file__, record.pathname)
        self.assertEqual("test_metadata_still_correct_after_a_reload", record.funcName)


if __name__ == "__main__":
    unittest.main()
