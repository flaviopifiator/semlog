"""`semlog.celery(app)` wiring tests (Phase 1 slice; STANDARDS.md CEL-001,
CEL-004, CEL-005, CEL-006; design #387 rev 2). Every test here uses plain
fakes for a Celery task/request -- no Celery installation is needed, so
this module always runs in the default suite (CP-008/CP-013). The real,
end-to-end Celery signal wiring is proven separately by
`tests/test_celery_integration.py`, which skips cleanly when Celery is
absent.
"""

from __future__ import annotations

import logging
import unittest
from unittest import mock

from semlog import _celery, _modes
from semlog._context import current, operation


class _FakeConf:
    """Stand-in for a Celery app's `.conf` (`app.conf`): a plain namespace
    carrying only the one attribute the phase 2 receivers touch, so a
    fake never silently grows a `worker_redirect_stdouts` attribute
    unless a test itself sets one (`RedirectNeverWrittenTests` relies on
    that absence)."""

    def __init__(self, worker_hijack_root_logger=True):
        self.worker_hijack_root_logger = worker_hijack_root_logger


class _FakeCeleryApp:
    def __init__(self, worker_hijack_root_logger=True):
        self.conf = _FakeConf(worker_hijack_root_logger)


class _FakeRequest:
    """Stand-in for a Celery `Context` (`task.request`): supports both the
    `.headers` mapping Celery 5.6.x's worker path (and eager
    `apply(headers=...)`) populates, and the top-level attribute access
    Celery 5.2.7's worker path uses instead (design D3/B1)."""

    def __init__(self, *, headers=None, **attrs):
        self.headers = headers
        for key, value in attrs.items():
            setattr(self, key, value)

    def get(self, key, default=None):
        return getattr(self, key, default)


class _FakeTask:
    def __init__(self, request):
        self.request = request


_TRACE_A = "4bf92f3577b34da6a3ce929d0e0e4736"
_SPAN_A = "00f067aa0ba902b7"


def _traceparent(trace_id=_TRACE_A, span_id=_SPAN_A):
    return f"00-{trace_id}-{span_id}-01"


class EntryPointValidationTests(unittest.TestCase):
    """Proves: CEL-001

    `celery(None)` raises `TypeError` before the lazy `from celery import
    signals` import ever runs (design D6), so this case needs no real
    Celery installed at all -- moved here from `tests/test_celery_
    integration.py` (validation-1 NIT) so it runs unconditionally in the
    default suite instead of skipping whenever Celery is absent."""

    def test_celery_none_raises_type_error(self):
        with self.assertRaises(TypeError):
            _celery.celery(None)


class CarrierTests(unittest.TestCase):
    """Proves: CEL-005"""

    def test_5_6_x_worker_path_returns_the_headers_mapping_verbatim(self):
        request = _FakeRequest(headers={"traceparent": _traceparent()})
        self.assertIs(request.headers, _celery._carrier(request))

    def test_eager_apply_headers_shape_is_the_same_as_5_6_x(self):
        # `apply(headers=...)` puts the trace fields on `.headers` on every
        # supported version (design D3), so this is the same code path as
        # the 5.6.x worker-path test above.
        request = _FakeRequest(headers={"traceparent": _traceparent()})
        self.assertEqual({"traceparent": _traceparent()}, _celery._carrier(request))

    def test_5_2_7_worker_path_builds_a_carrier_from_top_level_attributes(self):
        request = _FakeRequest(
            headers=None,
            traceparent=_traceparent(),
            tracestate="vendor=x",
        )
        self.assertEqual(
            {"traceparent": _traceparent(), "tracestate": "vendor=x"},
            _celery._carrier(request),
        )

    def test_5_2_7_worker_path_includes_trusted_baggage_when_present(self):
        request = _FakeRequest(
            headers=None, traceparent=_traceparent(), baggage="tier=gold"
        )
        carrier = _celery._carrier(request)
        self.assertEqual("tier=gold", carrier["baggage"])

    def test_no_traceparent_anywhere_returns_none(self):
        # An empty (but present) `.headers` mapping falls back to the
        # top-level lookup too, and still finds nothing there.
        request = _FakeRequest(headers={})
        self.assertIsNone(_celery._carrier(request))


class TaskSpanReceiverTests(unittest.TestCase):
    """Proves: CEL-005"""

    def setUp(self):
        self._prior_mode = _modes.state.mode
        _modes.state.mode = "full"
        _celery._scopes.clear()

    def tearDown(self):
        _modes.state.mode = self._prior_mode
        _celery._scopes.clear()

    def test_prerun_postrun_pairing_creates_and_closes_the_scope(self):
        request = _FakeRequest(headers={"traceparent": _traceparent()})
        task = _FakeTask(request)

        _celery._on_task_prerun(task=task)
        self.assertIn(id(request), _celery._scopes)
        snapshot = current()
        self.assertEqual(_TRACE_A, snapshot.trace_id)
        self.assertNotEqual(_SPAN_A, snapshot.span_id)

        _celery._on_task_postrun(task=task)
        self.assertNotIn(id(request), _celery._scopes)
        self.assertIsNone(current())

    def test_postrun_still_closes_a_scope_opened_before_a_mid_task_mode_flip(self):
        # validation-1 m1: postrun used to return early on `is_off()`, so a
        # task that started in `full` mode and then had the process mode
        # flipped to `off` before it finished would leak both its `_scopes`
        # entry and its still-pushed context -- the ContextVar and dict
        # entry a *later* task on the same thread/process would then
        # incorrectly inherit.
        request = _FakeRequest(headers={"traceparent": _traceparent()})
        task = _FakeTask(request)

        _celery._on_task_prerun(task=task)
        self.assertIn(id(request), _celery._scopes)

        _modes.state.mode = "off"
        _celery._on_task_postrun(task=task)

        self.assertNotIn(id(request), _celery._scopes)
        self.assertIsNone(current())

    def test_ambient_nesting_continues_the_parent_operation(self):
        with operation() as outer:
            request = _FakeRequest(headers={})
            task = _FakeTask(request)

            _celery._on_task_prerun(task=task)
            inner = current()
            self.assertEqual(outer.trace_id, inner.trace_id)
            self.assertNotEqual(outer.span_id, inner.span_id)

            _celery._on_task_postrun(task=task)
            self.assertEqual(outer.trace_id, current().trace_id)
            self.assertEqual(outer.span_id, current().span_id)

    def test_scopes_left_empty_after_a_failing_task_body(self):
        # Celery calls task_postrun from a `finally` regardless of whether
        # the task body raised (design D3); this simulates that ordering
        # directly, since these receivers never run the task body itself.
        request = _FakeRequest(headers={})
        task = _FakeTask(request)
        _celery._on_task_prerun(task=task)
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            pass
        finally:
            _celery._on_task_postrun(task=task)
        self.assertEqual({}, _celery._scopes)

    def test_concurrent_tasks_get_distinct_span_ids(self):
        request_a = _FakeRequest(headers={})
        request_b = _FakeRequest(headers={})
        task_a, task_b = _FakeTask(request_a), _FakeTask(request_b)

        _celery._on_task_prerun(task=task_a)
        span_a = current().span_id
        _celery._on_task_postrun(task=task_a)

        _celery._on_task_prerun(task=task_b)
        span_b = current().span_id
        _celery._on_task_postrun(task=task_b)

        self.assertNotEqual(span_a, span_b)


class PublishReceiverTests(unittest.TestCase):
    """Proves: CEL-006"""

    def setUp(self):
        self._prior_mode = _modes.state.mode
        _modes.state.mode = "full"

    def tearDown(self):
        _modes.state.mode = self._prior_mode

    def test_injects_traceparent_when_a_span_is_active(self):
        with operation(headers={"traceparent": _traceparent()}):
            headers: dict[str, str] = {}
            _celery._on_before_task_publish(headers=headers)
            self.assertEqual(
                f"00-{_TRACE_A}-{current().span_id}-01", headers["traceparent"]
            )

    def test_leaves_headers_unchanged_with_no_active_span(self):
        headers: dict[str, str] = {}
        _celery._on_before_task_publish(headers=headers)
        self.assertEqual({}, headers)

    def test_no_op_when_headers_is_none(self):
        # Must not raise even though there is nothing to inject into.
        _celery._on_before_task_publish(headers=None)

    def test_trusted_baggage_is_injected_alongside_traceparent(self):
        # A5-M4: CEL-006 also carries trusted baggage, not only traceparent.
        with operation(headers={"baggage": "tier=gold"}, baggage_allow=("tier",)):
            headers: dict[str, str] = {}
            _celery._on_before_task_publish(headers=headers)
            self.assertIn("traceparent", headers)
            self.assertEqual("tier=gold", headers["baggage"])


class OffModeReceiverTests(unittest.TestCase):
    """Proves: CEL-004"""

    def setUp(self):
        self._prior_mode = _modes.state.mode
        _modes.state.mode = "off"
        _celery._scopes.clear()

    def tearDown(self):
        _modes.state.mode = self._prior_mode
        _celery._scopes.clear()

    def test_off_mode_prerun_binds_no_scope(self):
        request = _FakeRequest(headers={"traceparent": _traceparent()})
        _celery._on_task_prerun(task=_FakeTask(request))
        self.assertEqual({}, _celery._scopes)
        self.assertIsNone(current())

    def test_off_mode_postrun_is_a_harmless_no_op(self):
        request = _FakeRequest(headers={})
        # No prior prerun call: postrun must not raise on an unknown request.
        _celery._on_task_postrun(task=_FakeTask(request))
        self.assertEqual({}, _celery._scopes)

    def test_off_mode_publish_leaves_headers_unchanged(self):
        headers: dict[str, str] = {}
        _celery._on_before_task_publish(headers=headers)
        self.assertEqual({}, headers)


class ReceiversNeverRaiseTests(unittest.TestCase):
    """Proves: CEL-001

    SC-3: no receiver `celery(app)` connects may ever raise into Celery.
    Celery's own `import_modules` signal re-raises whatever a connected
    receiver raises, aborting worker/beat startup; its `task_prerun`,
    `task_postrun` and `before_task_publish` signals only log a raising
    receiver's exception instead. A receiver connected here must never
    depend on which behavior applies, so every one stays defensive
    regardless -- this suite exercises exactly the three phase 1
    receivers, verified at the unit level since Celery's own dispatch
    would otherwise swallow the very failure being tested."""

    def setUp(self):
        self._prior_mode = _modes.state.mode
        _modes.state.mode = "full"
        _celery._scopes.clear()

    def tearDown(self):
        _modes.state.mode = self._prior_mode
        _celery._scopes.clear()

    def test_prerun_never_raises_when_operation_raises(self):
        with mock.patch.object(_celery, "operation", side_effect=RuntimeError("boom")):
            _celery._on_task_prerun(task=_FakeTask(_FakeRequest(headers={})))

    def test_prerun_never_raises_when_carrier_raises(self):
        with mock.patch.object(_celery, "_carrier", side_effect=RuntimeError("boom")):
            _celery._on_task_prerun(task=_FakeTask(_FakeRequest(headers={})))

    def test_prerun_never_raises_with_a_malformed_task(self):
        _celery._on_task_prerun(task=None)

    def test_postrun_never_raises_when_scope_exit_raises(self):
        class _BoomScope:
            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                raise RuntimeError("boom")

        request = _FakeRequest(headers={})
        _celery._scopes[id(request)] = _BoomScope()
        _celery._on_task_postrun(task=_FakeTask(request))

    def test_publish_never_raises_when_inject_raises(self):
        with mock.patch.object(_celery, "inject", side_effect=RuntimeError("boom")):
            _celery._on_before_task_publish(headers={})

    def test_import_modules_never_raises_when_owns_root_raises(self):
        with mock.patch.object(_celery, "_owns_root", side_effect=RuntimeError("boom")):
            _celery._on_import_modules(sender=_FakeCeleryApp())

    def test_import_modules_never_raises_with_no_sender(self):
        with mock.patch.object(_celery, "_owns_root", return_value=True):
            _celery._on_import_modules(sender=None)

    def test_after_setup_task_logger_never_raises_when_owns_root_raises(self):
        with mock.patch.object(_celery, "_owns_root", side_effect=RuntimeError("boom")):
            _celery._on_after_setup_task_logger(logger=object())

    def test_after_setup_task_logger_never_raises_when_capture_raises(self):
        with (
            mock.patch.object(_celery, "_owns_root", return_value=True),
            mock.patch.object(
                _celery, "capture_loggers", side_effect=RuntimeError("boom")
            ),
        ):
            _celery._on_after_setup_task_logger(logger=object())

    def test_worker_process_init_never_raises(self):
        # Whether Celery is genuinely installed in this environment or not,
        # this receiver's own body (`from celery import signals` plus a
        # `.connect(...)` call) must never raise into Celery's dispatch.
        _celery._on_worker_process_init()

    def test_worker_process_shutdown_never_raises_when_flush_raises(self):
        with mock.patch.object(_celery, "flush", side_effect=RuntimeError("boom")):
            _celery._on_worker_process_shutdown()


class OwnsRootTests(unittest.TestCase):
    """Proves: CEL-002

    design D1/NIT 3: `_owns_root()` also checks that semlog's own handler
    is still attached to the root logger, not only that mode is `full` and
    a handler object exists -- a later `dictConfig` or an explicit hijack
    that detached it must leave Celery vanilla instead of silently
    capturing loggers onto a handler nobody delivers records through
    anymore."""

    def setUp(self):
        self._prior_mode = _modes.state.mode
        self._prior_handler = _celery._transport._state.handler
        _modes.state.mode = "full"

    def tearDown(self):
        _modes.state.mode = self._prior_mode
        _celery._transport._state.handler = self._prior_handler

    def test_false_when_mode_is_not_full(self):
        _modes.state.mode = "hybrid"
        _celery._transport._state.handler = logging.Handler()
        self.assertFalse(_celery._owns_root())

    def test_false_when_no_handler_is_installed(self):
        _celery._transport._state.handler = None
        self.assertFalse(_celery._owns_root())

    def test_false_when_the_handler_is_installed_but_not_on_root(self):
        _celery._transport._state.handler = logging.Handler()
        self.assertFalse(_celery._owns_root())

    def test_true_when_full_and_the_handler_is_attached_to_root(self):
        handler = logging.Handler()
        _celery._transport._state.handler = handler
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            self.assertTrue(_celery._owns_root())
        finally:
            root.removeHandler(handler)


class FullModeFlagTests(unittest.TestCase):
    """Proves: CEL-002

    design D1: the lazy `import_modules` write, isolated from real Celery
    signal dispatch and from `_owns_root`'s own logic (`OwnsRootTests`
    covers that directly) by mocking `_owns_root` itself."""

    def test_owning_root_writes_hijack_false(self):
        app = _FakeCeleryApp(worker_hijack_root_logger=True)
        with mock.patch.object(_celery, "_owns_root", return_value=True):
            _celery._on_import_modules(sender=app)
        self.assertFalse(app.conf.worker_hijack_root_logger)

    def test_not_owning_root_leaves_hijack_untouched(self):
        app = _FakeCeleryApp(worker_hijack_root_logger=True)
        with mock.patch.object(_celery, "_owns_root", return_value=False):
            _celery._on_import_modules(sender=app)
        self.assertTrue(app.conf.worker_hijack_root_logger)


class RedirectNeverWrittenTests(unittest.TestCase):
    """Proves: CEL-002

    A7/#376 amended: semlog writes only `worker_hijack_root_logger`, never
    `worker_redirect_stdouts`, in any mode."""

    def test_owning_root_never_sets_the_redirect_attribute(self):
        app = _FakeCeleryApp()
        with mock.patch.object(_celery, "_owns_root", return_value=True):
            _celery._on_import_modules(sender=app)
        self.assertFalse(hasattr(app.conf, "worker_redirect_stdouts"))

    def test_not_owning_root_never_sets_the_redirect_attribute_either(self):
        app = _FakeCeleryApp()
        with mock.patch.object(_celery, "_owns_root", return_value=False):
            _celery._on_import_modules(sender=app)
        self.assertFalse(hasattr(app.conf, "worker_redirect_stdouts"))


class TaskLoggerCaptureTests(unittest.TestCase):
    """Proves: CEL-002

    design D2."""

    def test_owning_root_captures_celery_task_and_multiprocessing(self):
        with (
            mock.patch.object(_celery, "_owns_root", return_value=True),
            mock.patch.object(_celery, "capture_loggers") as captured,
        ):
            _celery._on_after_setup_task_logger(logger=object())
        captured.assert_called_once_with(("celery.task", "multiprocessing"))

    def test_not_owning_root_never_captures(self):
        with (
            mock.patch.object(_celery, "_owns_root", return_value=False),
            mock.patch.object(_celery, "capture_loggers") as captured,
        ):
            _celery._on_after_setup_task_logger(logger=object())
        captured.assert_not_called()


class ShutdownFlushTests(unittest.TestCase):
    """Proves: CEL-007"""

    def setUp(self):
        self._prior_mode = _modes.state.mode
        _modes.state.mode = "full"

    def tearDown(self):
        _modes.state.mode = self._prior_mode

    def test_full_mode_flushes_with_a_5_second_timeout(self):
        with mock.patch.object(_celery, "flush") as flushed:
            _celery._on_worker_process_shutdown()
        flushed.assert_called_once_with(timeout=5)

    def test_off_mode_never_flushes(self):
        _modes.state.mode = "off"
        with mock.patch.object(_celery, "flush") as flushed:
            _celery._on_worker_process_shutdown()
        flushed.assert_not_called()


class HybridInertTests(unittest.TestCase):
    """Proves: CEL-003

    Hybrid mode adds only span, inject and flush (CEL-005/006/007): the
    logging-ownership receivers (`_on_import_modules`, `_on_after_setup_
    task_logger`) stay inert, since `_owns_root()` already requires mode
    `full`, but the shutdown flush still runs."""

    def setUp(self):
        self._prior_mode = _modes.state.mode
        _modes.state.mode = "hybrid"

    def tearDown(self):
        _modes.state.mode = self._prior_mode

    def test_hybrid_never_writes_the_hijack_flag(self):
        app = _FakeCeleryApp(worker_hijack_root_logger=True)
        _celery._on_import_modules(sender=app)
        self.assertTrue(app.conf.worker_hijack_root_logger)

    def test_hybrid_never_captures_the_task_logger(self):
        with mock.patch.object(_celery, "capture_loggers") as captured:
            _celery._on_after_setup_task_logger(logger=object())
        captured.assert_not_called()

    def test_hybrid_still_flushes_on_shutdown(self):
        with mock.patch.object(_celery, "flush") as flushed:
            _celery._on_worker_process_shutdown()
        flushed.assert_called_once_with(timeout=5)


if __name__ == "__main__":
    unittest.main()
