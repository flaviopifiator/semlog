"""Real-Celery integration tests for `semlog.celery(app)` (Phase 1 slice;
STANDARDS.md CEL-001, CEL-004, CEL-005, CEL-006; design #387 rev 2,
addendum #390). Skips cleanly, with an explicit reason, when `celery` is
not installed, so `python -m unittest discover` stays stdlib-only
(CP-008/CP-013):

    uv run --with "celery==5.6.3" python -m unittest tests.test_celery_integration -v
    uv run --python 3.10 --with "celery==5.2.7" python -m unittest tests.test_celery_integration -v

A solo-pool in-process worker (`celery.contrib.testing.worker.start_worker`)
runs against an in-memory broker/backend (`memory://` / `cache+memory://`,
CP-020): no external broker service is needed. `_FakeRequest`-based fakes
belong to `tests/test_celery_wiring.py`; every test here drives real
Celery signal dispatch end to end.
"""

from __future__ import annotations

import logging
import os
import threading
import unittest
import warnings
from unittest import mock

try:
    import celery as _celery_pkg
    import celery._state as _celery_state
    import celery.app.log as _celery_log
    from celery import signals as _celery_signals
    from celery.contrib.testing.worker import start_worker
except ImportError:  # pragma: no cover -- exercised only with celery installed
    _celery_pkg = None
    _celery_state = None
    _celery_log = None
    _celery_signals = None
    start_worker = None

from semlog import _celery, _modes
from semlog._context import current, operation

_TRACE_A = "a" * 32
_TRACE_B = "b" * 32
_TRACE_C = "c" * 32

# `Logging.setup()` (celery/app/log.py) writes exactly these two prefixes
# to `os.environ` (validation-1 m3/task 1.7): `CELERY_LOG_LEVEL`/
# `CELERY_LOG_FILE`/`CELERY_LOG_REDIRECT`/`CELERY_LOG_REDIRECT_LEVEL`, and
# `_MP_FORK_LOGLEVEL_`/`_MP_FORK_LOGFILE_`/`_MP_FORK_LOGFORMAT_`.
_ENV_PREFIXES_TO_RESTORE = ("CELERY_LOG_", "_MP_FORK_")


def _traceparent(trace_id, span_id="1" * 16):
    return f"00-{trace_id}-{span_id}-01"


def _build_app(name, **conf_overrides):
    app = _celery_pkg.Celery(name)
    conf = {
        "broker_url": "memory://",
        "result_backend": "cache+memory://",
        "task_always_eager": False,
        "worker_hijack_root_logger": False,
    }
    conf.update(conf_overrides)
    app.conf.update(**conf)
    return app


def _snapshot_celery_state():
    """Every piece of process-global state a real `start_worker()` run can
    touch (validation-1 m3/task 1.7's original, unmet claim): `_modes.
    state` (mode and marking), `_celery._scopes`, Celery's current/default
    app (`start_worker` calls `app.set_current()`/`set_default_app()`),
    the root logger's level and handler list, whether `logging.
    captureWarnings` was installed, `celery.app.log.Logging._setup` (a
    process-wide "logging already configured" flag `app.log.setup()`
    flips permanently), `warnings.filters`, and every `CELERY_LOG_*`/
    `_MP_FORK_*` environment variable `Logging.setup()` writes."""
    root = logging.getLogger()
    return {
        "mode": _modes.state.mode,
        "marking": _modes.state.marking,
        "scopes": dict(_celery._scopes),
        "current_app": _celery_state._tls.current_app,
        "default_app": _celery_state.default_app,
        "root_level": root.level,
        "root_handlers": list(root.handlers),
        "capture_warnings_installed": logging._warnings_showwarning is not None,
        "logging_setup": _celery_log.Logging._setup,
        "warnings_filters": list(warnings.filters),
        "environ": {
            key: value
            for key, value in os.environ.items()
            if key.startswith(_ENV_PREFIXES_TO_RESTORE)
        },
    }


def _restore_celery_state(snapshot):
    _modes.state.mode = snapshot["mode"]
    _modes.state.marking = snapshot["marking"]
    _celery._scopes.clear()
    _celery._scopes.update(snapshot["scopes"])
    _celery_state._tls.current_app = snapshot["current_app"]
    _celery_state.default_app = snapshot["default_app"]

    root = logging.getLogger()
    root.setLevel(snapshot["root_level"])
    root.handlers[:] = snapshot["root_handlers"]

    logging.captureWarnings(snapshot["capture_warnings_installed"])
    _celery_log.Logging._setup = snapshot["logging_setup"]
    warnings.filters[:] = snapshot["warnings_filters"]

    for key in [k for k in os.environ if k.startswith(_ENV_PREFIXES_TO_RESTORE)]:
        if key not in snapshot["environ"]:
            del os.environ[key]
    os.environ.update(snapshot["environ"])


class _CeleryStateSnapshotMixin:
    """Shared setUp/tearDown for every real-Celery test class below:
    snapshots the full process-global state `_snapshot_celery_state`
    describes before the test, defaults `_modes.state.mode` to `"full"`
    (subclasses override after calling `super().setUp()` when they need
    another mode), and restores the exact snapshot afterward -- so no test
    in this module leaks Celery/logging process state into another test
    in this file, into `tests/test_celery_matrix.py` (phase 2), or into
    the rest of the default suite when Celery happens to be installed."""

    def setUp(self):
        super().setUp()
        self._celery_state_snapshot = _snapshot_celery_state()
        _modes.state.mode = "full"

    def tearDown(self):
        _restore_celery_state(self._celery_state_snapshot)
        super().tearDown()


@unittest.skipIf(_celery_pkg is None, "celery is not installed in this environment")
class EntryPointIdempotencyTests(_CeleryStateSnapshotMixin, unittest.TestCase):
    """Proves: CEL-001

    `celery(None)` raising `TypeError` needs no real Celery signal
    dispatch at all (the check runs before the lazy `celery.signals`
    import), so that case lives in `tests/test_celery_wiring.py` instead,
    where it runs in the default suite unconditionally."""

    def test_repeated_calls_connect_each_receiver_exactly_once(self):
        # CEL-001: idempotent across repeated calls, with the same app or a
        # different one -- a fixed `dispatch_uid` per receiver means
        # Celery's own dispatcher never adds a second entry for the same
        # (dispatch_uid, sender) pair (design D6). `import_modules` is
        # scoped `sender=app` instead (design D1/D6) and is proven
        # separately below, per app, since its own lookup key includes the
        # sender and therefore never collapses across different apps.
        app1 = _build_app("semlog-idempotency-1")
        app2 = _build_app("semlog-idempotency-2")
        _celery.celery(app1)
        _celery.celery(app1)
        _celery.celery(app2)
        for signal, dispatch_uid in (
            (_celery_signals.task_prerun, "semlog.celery.task_prerun"),
            (_celery_signals.task_postrun, "semlog.celery.task_postrun"),
            (_celery_signals.before_task_publish, "semlog.celery.before_task_publish"),
            (
                _celery_signals.after_setup_task_logger,
                "semlog.celery.after_setup_task_logger",
            ),
            (_celery_signals.worker_process_init, "semlog.celery.worker_process_init"),
        ):
            with self.subTest(dispatch_uid=dispatch_uid):
                matching = [r for r in signal.receivers if r[0][0] == dispatch_uid]
                self.assertEqual(1, len(matching))

    def test_import_modules_receiver_count_is_per_app(self):
        # validation-2 A5 NIT: `import_modules` connects with `sender=app`
        # (design D1/D6), so its own dispatcher lookup key is `(dispatch_
        # uid, id(sender))` -- two apps get two independent entries
        # instead of collapsing into the one shared entry every other
        # receiver above gets. Filtered by the exact `id()` of these two
        # apps, not an absolute count: every OTHER test in this module
        # that also calls `_celery.celery(app)` on its own app instance
        # leaves its own `(dispatch_uid, id(that app))` entry in this same
        # process-global receivers list for the rest of the test run.
        app1 = _build_app("semlog-idempotency-3")
        app2 = _build_app("semlog-idempotency-4")
        _celery.celery(app1)
        _celery.celery(app1)
        _celery.celery(app2)
        dispatch_uid = "semlog.celery.import_modules"

        def _count_for(app):
            return sum(
                1
                for r in _celery_signals.import_modules.receivers
                if r[0] == (dispatch_uid, id(app))
            )

        self.assertEqual(1, _count_for(app1))
        self.assertEqual(1, _count_for(app2))


@unittest.skipIf(_celery_pkg is None, "celery is not installed in this environment")
class OffModeCeleryTests(_CeleryStateSnapshotMixin, unittest.TestCase):
    """Proves: CEL-004"""

    def setUp(self):
        super().setUp()
        _modes.state.mode = "off"
        self.app = _build_app("semlog-off-mode")
        _celery.celery(self.app)

        @self.app.task(name="semlog_tests.off_child")
        def off_child():
            snapshot = current()
            return {"trace_id": snapshot.trace_id if snapshot is not None else None}

        @self.app.task(name="semlog_tests.off_parent")
        def off_parent():
            # Fire-and-forget: a solo-pool worker has exactly one task
            # slot, so blocking here on the child's own result would
            # deadlock (the child could never be picked up). The test
            # fetches the child's result separately, after the parent
            # completes and frees that slot.
            snapshot = current()
            child = off_child.apply_async()
            return {
                "trace_id": snapshot.trace_id if snapshot is not None else None,
                "child_id": child.id,
            }

        self.off_child = off_child
        self.off_parent = off_parent

    def test_off_mode_produces_no_span_and_injects_no_header(self):
        captured_headers = []

        def _capture(headers=None, **kwargs):
            captured_headers.append(dict(headers or {}))

        _celery_signals.before_task_publish.connect(_capture, weak=False)
        try:
            with start_worker(self.app, pool="solo", perform_ping_check=False):
                result = self.off_parent.apply_async()
                value = result.get(timeout=10)
                child_value = self.off_child.AsyncResult(value["child_id"]).get(
                    timeout=10
                )
        finally:
            _celery_signals.before_task_publish.disconnect(_capture)

        self.assertIsNone(value["trace_id"])
        self.assertIsNone(child_value["trace_id"])
        self.assertTrue(captured_headers, "no publish observed")
        for headers in captured_headers:
            self.assertNotIn("traceparent", headers)


@unittest.skipIf(_celery_pkg is None, "celery is not installed in this environment")
class WorkerSpanTests(_CeleryStateSnapshotMixin, unittest.TestCase):
    """Proves: CEL-005

    The floor row (Celery 5.2.7) never populates `request.headers` on the
    worker path (design B1); running this class there is the RED that
    forced `_celery._carrier`'s top-level-attribute fallback (D3)."""

    def setUp(self):
        super().setUp()
        self.app = _build_app("semlog-worker-span")
        _celery.celery(self.app)

        @self.app.task(name="semlog_tests.span_child")
        def span_child():
            snapshot = current()
            return {"trace_id": snapshot.trace_id, "span_id": snapshot.span_id}

        @self.app.task(name="semlog_tests.span_parent")
        def span_parent():
            # Fire-and-forget (see OffModeCeleryTests.off_parent): a solo
            # pool has exactly one task slot, so blocking here on the
            # child's own result would deadlock it.
            snapshot = current()
            child = span_child.apply_async()
            return {
                "trace_id": snapshot.trace_id,
                "span_id": snapshot.span_id,
                "child_id": child.id,
            }

        self.span_child = span_child
        self.span_parent = span_parent

    def test_publisher_to_task_continuation_on_solo(self):
        with (
            start_worker(self.app, pool="solo", perform_ping_check=False),
            operation(headers={"traceparent": _traceparent(_TRACE_A)}),
        ):
            result = self.span_child.apply_async()
            value = result.get(timeout=10)
        self.assertEqual(_TRACE_A, value["trace_id"])

    def test_task_to_child_task_continuation_on_solo(self):
        with start_worker(self.app, pool="solo", perform_ping_check=False):
            result = self.span_parent.apply_async()
            value = result.get(timeout=10)
            child_value = self.span_child.AsyncResult(value["child_id"]).get(timeout=10)
        self.assertEqual(value["trace_id"], child_value["trace_id"])
        self.assertNotEqual(value["span_id"], child_value["span_id"])

    def test_task_to_child_task_continuation_on_threads(self):
        with start_worker(
            self.app, pool="threads", concurrency=2, perform_ping_check=False
        ):
            result = self.span_parent.apply_async()
            value = result.get(timeout=10)
            child_value = self.span_child.AsyncResult(value["child_id"]).get(timeout=10)
        self.assertEqual(value["trace_id"], child_value["trace_id"])
        self.assertNotEqual(value["span_id"], child_value["span_id"])

    def test_concurrent_tasks_on_threads_never_share_a_span_id(self):
        barrier = threading.Barrier(2)

        @self.app.task(name="semlog_tests.span_barrier")
        def span_barrier():
            barrier.wait(timeout=10)
            return current().span_id

        with start_worker(
            self.app, pool="threads", concurrency=2, perform_ping_check=False
        ):
            first = span_barrier.apply_async()
            second = span_barrier.apply_async()
            first_span = first.get(timeout=10)
            second_span = second.get(timeout=10)
        self.assertNotEqual(first_span, second_span)


@unittest.skipIf(_celery_pkg is None, "celery is not installed in this environment")
class EagerSpanTests(_CeleryStateSnapshotMixin, unittest.TestCase):
    """Proves: CEL-005

    Eager `apply(headers=...)` puts the trace fields on `request.headers`
    on every supported version (design D3), unlike the 5.2.7 worker path."""

    def setUp(self):
        super().setUp()
        self.app = _build_app("semlog-eager-span", task_always_eager=True)
        _celery.celery(self.app)

        @self.app.task(name="semlog_tests.eager_span")
        def eager_span():
            snapshot = current()
            return {"trace_id": snapshot.trace_id, "span_id": snapshot.span_id}

        self.eager_span = eager_span

    def test_eager_apply_with_headers_continues_the_trace(self):
        result = self.eager_span.apply(headers={"traceparent": _traceparent(_TRACE_B)})
        value = result.get()
        self.assertEqual(_TRACE_B, value["trace_id"])

    def test_eager_apply_with_no_headers_starts_a_fresh_trace(self):
        result = self.eager_span.apply()
        value = result.get()
        self.assertIsNotNone(value["trace_id"])
        self.assertNotEqual(_TRACE_B, value["trace_id"])


@unittest.skipIf(_celery_pkg is None, "celery is not installed in this environment")
class PublishInjectTests(_CeleryStateSnapshotMixin, unittest.TestCase):
    """Proves: CEL-006"""

    def setUp(self):
        super().setUp()
        self.app = _build_app("semlog-publish-inject")
        _celery.celery(self.app)

        @self.app.task(name="semlog_tests.publish_inject_noop")
        def noop():
            return None

        self.noop = noop
        self.captured = []

        def _capture(headers=None, **kwargs):
            self.captured.append(dict(headers or {}))

        self._capture = _capture
        _celery_signals.before_task_publish.connect(_capture, weak=False)

    def tearDown(self):
        _celery_signals.before_task_publish.disconnect(self._capture)
        super().tearDown()

    def test_header_injected_only_with_an_active_span(self):
        with start_worker(self.app, pool="solo", perform_ping_check=False):
            with operation(headers={"traceparent": _traceparent(_TRACE_C)}):
                self.noop.apply_async().get(timeout=10)
            self.noop.apply_async().get(timeout=10)

        self.assertEqual(2, len(self.captured))
        self.assertIn("traceparent", self.captured[0])
        self.assertEqual(
            f"00-{_TRACE_C}-", self.captured[0]["traceparent"][: len(_TRACE_C) + 4]
        )
        self.assertNotIn("traceparent", self.captured[1])


@unittest.skipIf(_celery_pkg is None, "celery is not installed in this environment")
class ReceiverIntegrationDoesNotAbortStartupTests(
    _CeleryStateSnapshotMixin, unittest.TestCase
):
    """Proves: CEL-001

    validation-1 m2: Celery's own `Signal.send` (`celery/utils/dispatch/
    signal.py`) already catches a raising `task_prerun`/`task_postrun`/
    `before_task_publish` receiver itself and logs `'Signal handler %r
    raised: %r'` through the `celery.utils.dispatch.signal` logger, so
    `apply_async().get()` succeeding proves nothing about SC-3's own
    try/except guard for these three signals -- it would succeed exactly
    the same with the guard removed. The meaningful, non-vacuous
    assertion is that OUR guard catches the exception BEFORE it ever
    reaches Celery's dispatch layer, so that error-level log line is
    never emitted at all. (SC-3's real, load-bearing test is for the
    `import_modules` signal, which Celery's loader genuinely re-raises;
    that receiver is phase 2 scope and is covered there instead.)"""

    def setUp(self):
        super().setUp()
        self.app = _build_app("semlog-never-raise")
        _celery.celery(self.app)

        @self.app.task(name="semlog_tests.never_raise_noop")
        def noop():
            return "ok"

        self.noop = noop

    def test_a_raising_operation_is_caught_before_celerys_own_dispatch_logs_it(self):
        with (
            mock.patch.object(_celery, "operation", side_effect=RuntimeError("boom")),
            start_worker(self.app, pool="solo", perform_ping_check=False),
            self.assertNoLogs(logger="celery.utils.dispatch.signal", level="ERROR"),
        ):
            result = self.noop.apply_async()
            self.assertEqual("ok", result.get(timeout=10))


@unittest.skipIf(_celery_pkg is None, "celery is not installed in this environment")
class ImportModulesFlagTests(_CeleryStateSnapshotMixin, unittest.TestCase):
    """Proves: CEL-002, CEL-001, CEL-004

    No running worker needed: `app.loader.init_worker()` alone fires
    `import_modules` (design D1), which is enough to observe `_on_import_
    modules`'s real effect on `app.conf`. `_owns_root` itself is mocked
    for the full-mode cases below (its own real logic, which needs a
    genuinely installed pipeline handler, is proven directly by `tests/
    test_celery_wiring.py::OwnsRootTests`); the hybrid/off cases use the
    real, unmocked `_owns_root`, since it already returns `False` from the
    mode check alone, with no pipeline needed."""

    def test_full_mode_owning_root_sets_hijack_false(self):
        app = _build_app("semlog-import-modules-full", worker_hijack_root_logger=True)
        _celery.celery(app)
        with mock.patch.object(_celery, "_owns_root", return_value=True):
            app.loader.init_worker()
        self.assertFalse(app.conf.worker_hijack_root_logger)

    def test_full_mode_not_owning_root_leaves_hijack_untouched(self):
        app = _build_app("semlog-import-modules-full-2", worker_hijack_root_logger=True)
        _celery.celery(app)
        with mock.patch.object(_celery, "_owns_root", return_value=False):
            app.loader.init_worker()
        self.assertTrue(app.conf.worker_hijack_root_logger)

    def test_full_mode_never_writes_the_redirect_flag(self):
        # Celery declares a built-in default for `worker_redirect_stdouts`
        # on every app (so `hasattr` is always true regardless of what
        # semlog does); the real proof that semlog never WRITES it is that
        # the key never appears in `app.conf.changes`, the runtime-override
        # map every `app.conf.x = value` assignment lands in.
        app = _build_app("semlog-import-modules-full-3")
        default_redirect = app.conf.worker_redirect_stdouts
        _celery.celery(app)
        with mock.patch.object(_celery, "_owns_root", return_value=True):
            app.loader.init_worker()
        self.assertEqual(default_redirect, app.conf.worker_redirect_stdouts)
        self.assertNotIn("worker_redirect_stdouts", app.conf.changes)

    def test_hybrid_mode_never_touches_the_hijack_flag(self):
        _modes.state.mode = "hybrid"
        app = _build_app("semlog-import-modules-hybrid", worker_hijack_root_logger=True)
        _celery.celery(app)
        app.loader.init_worker()
        self.assertTrue(app.conf.worker_hijack_root_logger)

    def test_off_mode_never_touches_the_hijack_flag(self):
        _modes.state.mode = "off"
        app = _build_app("semlog-import-modules-off", worker_hijack_root_logger=True)
        _celery.celery(app)
        app.loader.init_worker()
        self.assertTrue(app.conf.worker_hijack_root_logger)

    def test_a5_m2_a_namespaced_hijack_true_beats_semlogs_own_write(self):
        # validation-2 M2: Celery's own `ConfigurationView.__getitem__`
        # (`utils/collections.py`) looks up the PREFIXED key
        # (`CELERY_WORKER_HIJACK_ROOT_LOGGER`) across every config layer
        # BEFORE ever falling back to the unprefixed key semlog writes to
        # (`app.conf.worker_hijack_root_logger = False` lands in
        # `.changes`, unprefixed) -- so a namespaced Django-style `True`
        # wins over semlog's own write, unlike a plain `app.conf.
        # worker_hijack_root_logger = True` (the sibling test below),
        # which semlog's write DOES override. Verified directly against
        # real Celery `Settings`/`ConfigurationView` behavior, no Django
        # needed: `config_from_object(..., namespace="CELERY")` is the
        # exact mechanism Django's own Celery fixup uses.
        class _NamespacedSettings:
            CELERY_WORKER_HIJACK_ROOT_LOGGER = True

        app = _build_app("semlog-import-modules-namespaced")
        app.config_from_object(_NamespacedSettings, namespace="CELERY")
        _celery.celery(app)
        with mock.patch.object(_celery, "_owns_root", return_value=True):
            app.loader.init_worker()
        self.assertTrue(app.conf.worker_hijack_root_logger)

    def test_a_plain_hijack_true_is_overridden_by_semlogs_own_write(self):
        # The counterpart to the namespaced case above: a PLAIN attribute
        # assignment lands in `.changes` too, unprefixed, so semlog's own
        # later write to that same unprefixed key overwrites it directly.
        app = _build_app("semlog-import-modules-plain", worker_hijack_root_logger=True)
        _celery.celery(app)
        with mock.patch.object(_celery, "_owns_root", return_value=True):
            app.loader.init_worker()
        self.assertFalse(app.conf.worker_hijack_root_logger)

    def test_a_raising_import_modules_receiver_never_aborts_worker_startup(self):
        # SC-3's real, load-bearing case (validation-1 m2's own deferred
        # note): unlike task_prerun/task_postrun/before_task_publish,
        # Celery's `import_default_modules()` genuinely re-raises whatever
        # a connected receiver raises (`Signal.send` catches it, logs it,
        # and returns it as the response; `import_default_modules` then
        # re-raises any response that is an `Exception` instance) -- so
        # this is where SC-3's guard is actually load-bearing, not merely
        # defensive like the other receivers in this module.
        app = _build_app("semlog-import-modules-never-raise")
        _celery.celery(app)
        with mock.patch.object(_celery, "_owns_root", side_effect=RuntimeError("boom")):
            app.loader.init_worker()  # must not raise


@unittest.skipIf(_celery_pkg is None, "celery is not installed in this environment")
class ChildFlushWiringTests(_CeleryStateSnapshotMixin, unittest.TestCase):
    """Proves: CEL-007, CEL-001, CEL-004

    No forking needed: `worker_process_init`/`worker_process_shutdown` are
    ordinary Celery signals, dispatched here directly to prove the
    connect-inside-a-receiver wiring (design D5) without a real prefork
    child process -- the full, real end-to-end proof against an actual
    forked child is `tests/test_celery_matrix.py::PreforkShutdownFlushTests`
    (phase 2's subprocess matrix)."""

    def test_worker_process_init_connects_the_shutdown_receiver_exactly_once(self):
        app = _build_app("semlog-child-flush-1")
        _celery.celery(app)
        _celery_signals.worker_process_init.send(sender=None)
        _celery_signals.worker_process_init.send(sender=None)  # idempotent (D6)
        dispatch_uid = "semlog.celery.worker_process_shutdown"
        matching = [
            r
            for r in _celery_signals.worker_process_shutdown.receivers
            if r[0][0] == dispatch_uid
        ]
        self.assertEqual(1, len(matching))

    def test_shutdown_flushes_with_a_5_second_timeout_after_init_connects_it(self):
        app = _build_app("semlog-child-flush-2")
        _celery.celery(app)
        _celery_signals.worker_process_init.send(sender=None)
        with mock.patch.object(_celery, "flush") as flushed:
            _celery_signals.worker_process_shutdown.send(sender=None)
        flushed.assert_called_once_with(timeout=5)

    def test_off_mode_extension_no_flag_no_capture_no_flush(self):
        # validation-2/task 2.3's "off extended" case, exercised across
        # all three phase-2 receivers in one place: `off` mode leaves the
        # hijack flag untouched, never captures the task logger, and
        # never flushes on shutdown -- matching CEL-004's "no side
        # effect" guarantee for every receiver `celery(app)` connects.
        _modes.state.mode = "off"
        app = _build_app("semlog-child-flush-off", worker_hijack_root_logger=True)
        _celery.celery(app)

        app.loader.init_worker()
        self.assertTrue(app.conf.worker_hijack_root_logger)

        with mock.patch.object(_celery, "capture_loggers") as captured:
            _celery_signals.after_setup_task_logger.send(sender=None, logger=None)
        captured.assert_not_called()

        _celery_signals.worker_process_init.send(sender=None)
        with mock.patch.object(_celery, "flush") as flushed:
            _celery_signals.worker_process_shutdown.send(sender=None)
        flushed.assert_not_called()


if __name__ == "__main__":
    unittest.main()
