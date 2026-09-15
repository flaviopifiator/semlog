"""Transport tests: queue handler overflow, writer, fork safety,
thread-safety, flush/atexit (design #162 §1.1/§2.1/§3.4/§6.2/§6.4;
STANDARDS LP-003/006/007/008/009/010, CP-006).

The formatter already renders one finished JSON line per record before the
queue (`_format.Formatter`, LP-002, `test_pipeline_order.py`); this module
tests the two remaining pipeline stages that move that line to stdout: the
queue handler (entry, overflow policy) and the writer (exit, lazy start,
never raises).
"""

from __future__ import annotations

import io
import json
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import unittest
import warnings
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import semlog
from semlog import _transport
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


def _record(level=logging.INFO, event="app.event"):
    logger = logging.getLogger("semlog.tests.transport")
    return logger.makeRecord(logger.name, level, __file__, 1, event, (), None)


def _handler(maxsize, overflow):
    handler = SemlogQueueHandler(queue.Queue(maxsize=maxsize), overflow=overflow)
    handler.setFormatter(Formatter(identity=_identity()))
    return handler


class QueueHandlerOverflowTests(unittest.TestCase):
    """Proves: LP-007, LP-009, CP-006"""

    def test_block_mode_waits_for_room_nothing_lost(self):
        handler = _handler(maxsize=1, overflow="block")
        handler.emit(_record())  # fills the queue
        self.assertEqual(1, handler.queue.qsize())

        started = threading.Event()

        def producer():
            started.set()
            handler.emit(_record(event="app.second"))

        thread = threading.Thread(target=producer)
        thread.start()
        self.assertTrue(started.wait(timeout=2))
        # Nothing drains the queue, so block mode must still be waiting for
        # room rather than dropping or giving up.
        thread.join(timeout=0.2)
        self.assertTrue(thread.is_alive())

        handler.queue.get()  # frees room
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(1, handler.queue.qsize())
        self.assertEqual(0, handler.counters.dropped)

    def test_drop_mode_prioritizes_warning_and_above(self):
        handler = _handler(maxsize=10, overflow="drop")
        for _ in range(9):
            handler.emit(_record(level=logging.INFO))
        self.assertEqual(9, handler.queue.qsize())  # 90% full

        handler.emit(_record(level=logging.INFO, event="app.dropped.info"))
        self.assertEqual(9, handler.queue.qsize())
        self.assertEqual(1, handler.counters.dropped)

        handler.emit(_record(level=logging.ERROR, event="app.kept.error"))
        self.assertEqual(10, handler.queue.qsize())  # the reserved 10% slot
        self.assertEqual(1, handler.counters.dropped)

        handler.emit(_record(level=logging.ERROR, event="app.dropped.error"))
        self.assertEqual(10, handler.queue.qsize())
        self.assertEqual(2, handler.counters.dropped)  # full: WARNING+ drops too

    def test_drop_mode_never_waits(self):
        handler = _handler(maxsize=1, overflow="drop")
        handler.emit(_record())
        handler.emit(_record(event="app.overflow"))  # returns immediately
        self.assertEqual(1, handler.queue.qsize())
        self.assertEqual(1, handler.counters.dropped)

    def test_queue_memory_bounded_under_sustained_overflow(self):
        maxsize = 50
        handler = _handler(maxsize=maxsize, overflow="drop")
        for i in range(maxsize * 20):
            handler.emit(_record(event=f"app.load.{i}"))
        self.assertLessEqual(handler.queue.qsize(), maxsize)
        self.assertGreater(handler.counters.dropped, 0)

    def test_call_site_never_performs_stdout_io(self):
        handler = _handler(maxsize=10, overflow="block")
        with mock.patch("sys.stdout") as stdout_mock:
            handler.emit(_record())
        stdout_mock.write.assert_not_called()

    def test_queue_mutex_not_held_during_writer_io(self):
        write_started = threading.Event()
        release_write = threading.Event()

        class SlowStream:
            def write(self, data):
                write_started.set()
                release_write.wait(timeout=2)

            def flush(self):
                pass

        q = queue.Queue(maxsize=10)
        handler = SemlogQueueHandler(q, overflow="block")
        handler.setFormatter(Formatter(identity=_identity()))
        writer = Writer(q, stream=SlowStream())
        writer.start()
        self.addCleanup(_safe_stop, writer)

        handler.emit(_record())  # the writer picks this up and blocks in write()
        self.assertTrue(write_started.wait(timeout=2))
        # The writer holds no queue lock while stuck in I/O: a second
        # producer must still be able to enqueue immediately.
        handler.emit(_record(event="app.second"))
        self.assertEqual(1, q.qsize())
        release_write.set()


def _safe_stop(writer):
    """`QueueListener.stop()` raises `AttributeError` if called a second
    time (its `_thread` is already `None`); tests may stop a writer
    explicitly and still register this as a cleanup safety net."""
    try:
        writer.stop()
    except AttributeError:
        pass


def _bounded_stop(writer, timeout=5):
    """Like `_safe_stop`, but never blocks forever: enqueues the sentinel,
    then joins with a `timeout` instead of `QueueListener.stop()`'s
    unbounded join. A stress test's tearDown must fail on a bounded
    timeout when something regresses, not hang the whole process."""
    if writer is None:
        return
    try:
        writer.enqueue_sentinel()
    except Exception:  # noqa: BLE001, S110 -- best-effort cleanup only
        pass
    thread = getattr(writer, "_thread", None)
    if thread is not None:
        thread.join(timeout=timeout)


class WriterTests(unittest.TestCase):
    """Writer/transport tests (design #162 §1.1/§2.1 steps 5-6). Individual
    test methods carry their own `Proves:` line: `test_writer_never_starts_at_import`
    is a partial LP-003 regression guard only (the lazy-start-on-first-record
    and fork scenarios are completed by `ForkSafetyTests`, so it stays untagged
    here, matching the LRC-013/CP-016 precedent in engram #188)."""

    def _drained_writer(self, q, stream, namespace="app"):
        writer = Writer(q, namespace=namespace, stream=stream)
        writer.start()
        self.addCleanup(_safe_stop, writer)
        return writer

    def test_writer_writes_finished_lines_as_utf8_json(self):
        q = queue.Queue()
        stream = io.BytesIO()
        writer = self._drained_writer(q, stream)
        q.put('{"event_name":"app.ok"}')
        _safe_stop(writer)  # drains everything queued before returning
        line = json.loads(stream.getvalue().decode("utf-8").strip())
        self.assertEqual("app.ok", line["event_name"])

    def test_writer_never_starts_at_import(self):
        self.assertIsNone(_transport._state.writer)

    def test_writer_keeps_draining_when_the_sink_is_broken(self):
        """Proves: LP-006"""

        class BrokenStream:
            def write(self, data):
                raise OSError("broken pipe")

            def flush(self):
                raise OSError("broken pipe")

        q = queue.Queue()
        writer = self._drained_writer(q, BrokenStream())
        q.put('{"event_name":"app.lost.1"}')
        q.put('{"event_name":"app.lost.2"}')
        _safe_stop(writer)  # would hang/leave the thread dead if handle() ever raised
        self.assertEqual(2, writer.counters.dropped)

    def test_drop_count_delta_reported_after_flush(self):
        """Proves: LP-007"""
        q = queue.Queue()
        stream = io.BytesIO()
        writer = self._drained_writer(q, stream, namespace="app")
        writer.counters.dropped = 3
        q.put('{"event_name":"app.ok"}')
        _safe_stop(writer)
        text = stream.getvalue().decode("utf-8")
        self.assertIn('"app.log.records_dropped":3', text)

    def test_broken_stdout_never_leaves_a_blocked_producer_waiting_forever(self):
        """Proves: LP-007"""

        class BrokenStream:
            def write(self, data):
                raise OSError("broken pipe")

            def flush(self):
                raise OSError("broken pipe")

        q = queue.Queue(maxsize=1)
        handler = SemlogQueueHandler(q, overflow="block")
        handler.setFormatter(Formatter(identity=_identity()))
        writer = self._drained_writer(q, BrokenStream())

        done = threading.Event()

        def producer():
            for i in range(5):
                handler.emit(_record(event=f"app.msg.{i}"))
            done.set()

        thread = threading.Thread(target=producer)
        thread.start()
        self.assertTrue(
            done.wait(timeout=2),
            "producer must not block forever even though the sink is broken",
        )
        thread.join(timeout=2)
        # Poll (bounded, deterministic timeout) instead of stop(): `stop()`'s
        # own `put_nowait` sentinel can race a still-full 1-slot queue here.
        deadline = time.monotonic() + 2
        while writer.counters.dropped < 5 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(5, writer.counters.dropped)


class WriterStreamResolutionTests(unittest.TestCase):
    """Proves: LP-006

    Both `Writer.handle` and the `queue=False` direct path must resolve
    their output stream robustly to a replaced `sys.stdout` that lacks a
    `.buffer` attribute (for example a text-only proxy installed by a
    process supervisor), instead of raising and killing the writer thread
    (design D1)."""

    def tearDown(self):
        _transport._state.handler = None

    def test_stdout_proxy_without_buffer_falls_back_to_dunder_stdout(self):
        fallback = SimpleNamespace(buffer=io.BytesIO())
        writer = Writer(queue.Queue())
        with mock.patch("sys.stdout", object()), mock.patch("sys.__stdout__", fallback):
            writer.handle('{"event_name":"app.fallback"}')
        line = fallback.buffer.getvalue().decode("utf-8").strip()
        self.assertEqual('{"event_name":"app.fallback"}', line)

    def test_no_stream_available_counts_as_a_dropped_record(self):
        writer = Writer(queue.Queue())
        with mock.patch("sys.stdout", object()), mock.patch("sys.__stdout__", object()):
            writer.handle('{"event_name":"app.nowhere"}')
        self.assertEqual(1, writer.counters.dropped)

    def test_marker_is_set_even_when_no_stream_is_available(self):
        writer = Writer(queue.Queue())
        marker = threading.Event()
        with mock.patch("sys.stdout", object()), mock.patch("sys.__stdout__", object()):
            writer.handle(marker)
        self.assertTrue(
            marker.is_set(), "flush() would hang forever waiting for this marker"
        )

    def test_direct_handler_returns_null_handler_when_no_stream_available(self):
        with mock.patch("sys.stdout", object()), mock.patch("sys.__stdout__", object()):
            handler = _transport.install(Formatter(identity=_identity()), queue=False)
        self.assertIsInstance(handler, logging.NullHandler)

    def test_direct_handler_falls_back_to_dunder_stdout_when_stdout_lacks_buffer(self):
        fallback = SimpleNamespace(buffer=io.BytesIO())
        with mock.patch("sys.stdout", object()), mock.patch("sys.__stdout__", fallback):
            handler = _transport.install(Formatter(identity=_identity()), queue=False)
            handler.emit(_record(event="app.direct.fallback"))
        line = fallback.buffer.getvalue().decode("utf-8")
        self.assertIn('"app.direct.fallback"', line)

    def test_garbage_collected_text_handler_does_not_close_its_shared_sink(self):
        import gc

        sink = io.BytesIO()
        handler = _transport._text_handler(sink)
        del handler
        gc.collect()  # the wrapper lives in a self-cycle (close -> flush -> self)
        self.assertFalse(
            sink.closed, "a discarded handler must not close a shared sink"
        )


class RootHelperTests(unittest.TestCase):
    """`attach_root` and `capture_loggers`: root-logger helpers consumed by
    later phases' mode resolution (design D2). No requirement id is
    declared for these helpers yet on their own -- they are exercised
    directly here, matching the precedent in
    `test_class_budget.py` for infrastructure whose
    formal `Proves:` citation lands with a later STANDARDS.md update."""

    def setUp(self):
        self.root = logging.getLogger()
        self._original_handlers = list(self.root.handlers)
        self._original_level = self.root.level

    def tearDown(self):
        self.root.handlers = self._original_handlers
        self.root.setLevel(self._original_level)
        if _transport._state.writer is not None:
            _safe_stop(_transport._state.writer)
        _transport._state.handler = None
        _transport._state.writer = None
        _transport._state.stream = None
        _transport._state.was_running = False

    def test_attach_root_replaces_semlogs_own_previous_handler_only(self):
        unrelated = logging.NullHandler()
        self.root.addHandler(unrelated)
        first = logging.NullHandler()
        _transport.attach_root(first)
        second = logging.NullHandler()
        _transport.attach_root(second)
        self.assertNotIn(first, self.root.handlers)
        self.assertIn(second, self.root.handlers)
        self.assertIn(unrelated, self.root.handlers)

    def test_attach_root_sets_level_only_when_given(self):
        self.root.setLevel(logging.WARNING)
        _transport.attach_root(logging.NullHandler(), level=None)
        self.assertEqual(logging.WARNING, self.root.level)
        _transport.attach_root(logging.NullHandler(), level=logging.INFO)
        self.assertEqual(logging.INFO, self.root.level)

    def test_capture_loggers_detaches_own_handlers_and_enables_propagation(self):
        name = "semlog.tests.capture.target"
        captured = logging.getLogger(name)
        captured.addHandler(logging.NullHandler())
        captured.propagate = False
        self.addCleanup(setattr, captured, "handlers", [])
        self.addCleanup(setattr, captured, "propagate", True)

        _transport.capture_loggers((name,))

        self.assertEqual([], captured.handlers)
        self.assertTrue(captured.propagate)

    def test_repeated_install_and_attach_root_leaves_exactly_one_handler(self):
        # install() already overwrites _state.handler before attach_root()
        # runs (design D2 order): attach_root must not rely on _state.handler
        # to find the handler it previously put on root, or a second
        # install()+attach_root() pair leaves the first queue handler
        # orphaned on root (its queue is never drained again; block mode
        # then hangs once that orphan queue fills up).
        handler1 = _transport.install(
            Formatter(identity=_identity()), stream=io.BytesIO()
        )
        _transport.attach_root(handler1, level=logging.INFO)
        handler2 = _transport.install(
            Formatter(identity=_identity()), stream=io.BytesIO()
        )
        _transport.attach_root(handler2, level=logging.INFO)

        semlog_handlers = [
            h for h in self.root.handlers if isinstance(h, SemlogQueueHandler)
        ]
        self.assertEqual([handler2], semlog_handlers)


def _bind_logger(name, handler):
    logger = logging.getLogger(name)
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    return logger


@unittest.skipUnless(hasattr(os, "fork"), "os.fork is POSIX-only")
class ForkSafetyTests(unittest.TestCase):
    """Proves: LP-003

    (LP-008's fork-safety clause is exercised here too -- no corrupted fd
    or lock across fork -- but the ID is tagged instead on
    `ConcurrentEmissionTests` below, once its 50-threads-x-100-records
    scenario makes LP-008 fully provable.)
    """

    def tearDown(self):
        if _transport._state.writer is not None:
            _safe_stop(_transport._state.writer)
        _transport._state.handler = None
        _transport._state.writer = None
        _transport._state.stream = None
        _transport._state.was_running = False

    def test_writer_starts_lazily_on_first_record_not_at_install(self):
        handler = _transport.install(
            Formatter(identity=_identity()),
            queue_size=8,
            overflow="block",
            namespace="app",
            stream=io.BytesIO(),
        )
        self.assertIsNone(_transport._state.writer)
        logger = _bind_logger("semlog.tests.fork.lazy", handler)
        self.addCleanup(setattr, logger, "handlers", [])

        logger.info("app.first")

        self.assertIsNotNone(_transport._state.writer)

    def test_parent_writer_restarts_immediately_after_fork(self):
        handler = _transport.install(
            Formatter(identity=_identity()),
            queue_size=8,
            overflow="block",
            namespace="app",
            stream=io.BytesIO(),
        )
        logger = _bind_logger("semlog.tests.fork.parent", handler)
        self.addCleanup(setattr, logger, "handlers", [])

        logger.info("app.before.fork")  # starts the writer lazily
        self.assertIsNotNone(_transport._state.writer)

        pid = os.fork()
        if pid == 0:
            os._exit(0)  # child: nothing to prove here, exit immediately
        _, status = os.waitpid(pid, 0)
        self.assertEqual(0, os.WEXITSTATUS(status))

        # `after_in_parent` must have restarted the writer immediately,
        # since it was running before the fork (LP-003).
        self.assertIsNotNone(_transport._state.writer)
        logger.info("app.after.fork")
        _transport.flush(timeout=2)

    def test_child_resets_to_lazy_state_and_logs_independently(self):
        handler = _transport.install(
            Formatter(identity=_identity()),
            queue_size=8,
            overflow="block",
            namespace="app",
        )
        logger = _bind_logger("semlog.tests.fork.child", handler)
        self.addCleanup(setattr, logger, "handlers", [])
        logger.info("app.before.fork")
        _transport.flush(timeout=2)

        r_fd, w_fd = os.pipe()
        pid = os.fork()
        if pid == 0:
            try:
                os.close(r_fd)
                os.dup2(w_fd, 1)
                os.close(w_fd)
                # `after_in_child` reset this process to the lazy-start
                # state; this call creates a fresh queue and writer.
                logger.info("app.after.fork.child")
                _transport.flush(timeout=2)
            finally:
                os._exit(0)
        os.close(w_fd)
        with os.fdopen(r_fd, "rb") as reader:
            data = reader.read()
        _, status = os.waitpid(pid, 0)
        self.assertEqual(0, os.WEXITSTATUS(status))
        self.assertIn(b'"app.after.fork.child"', data)


@unittest.skipUnless(hasattr(os, "fork"), "os.fork is POSIX-only")
class ForkFlushRaceTests(unittest.TestCase):
    """The design's "no second writer" case: with base-style at-fork hooks
    (no lock held across the fork; flush() never restarts a writer, design
    erratum D1), a concurrent flush() racing before()/after_in_parent()
    must never start a second writer thread. before() stops the writer;
    a concurrent flush() reads it as dead and simply returns; only
    after_in_parent() restarts it, once, after the real fork completes."""

    def tearDown(self):
        _bounded_stop(_transport._state.writer)  # never an unbounded hang
        _transport._state.handler = None
        _transport._state.writer = None
        _transport._state.stream = None
        _transport._state.was_running = False

    def test_concurrent_flush_never_starts_a_second_writer_across_a_fork(self):
        stream = io.BytesIO()
        handler = _transport.install(
            Formatter(identity=_identity()), queue_size=64, stream=stream
        )
        logger = _bind_logger("semlog.tests.fork.race", handler)
        self.addCleanup(setattr, logger, "handlers", [])

        lock = threading.Lock()
        state = {"active": 0, "max": 0}
        original_monitor = Writer._monitor

        def tracked_monitor(self):
            with lock:
                state["active"] += 1
                state["max"] = max(state["max"], state["active"])
            try:
                original_monitor(self)
            finally:
                with lock:
                    state["active"] -= 1

        stop_event = threading.Event()

        def hammer():
            while not stop_event.is_set():
                logger.info("app.hammer")
                _transport.flush(timeout=0.02)

        def do_fork(result):
            pid = os.fork()
            if pid == 0:
                # Bounded even if something in the child hangs: the default
                # SIGALRM disposition terminates the process, so the
                # parent's os.waitpid() below cannot block forever either.
                signal.alarm(5)
                os._exit(0)
            _, status = os.waitpid(pid, 0)
            result["status"] = status

        with (
            mock.patch.object(Writer, "_monitor", tracked_monitor),
            warnings.catch_warnings(),
        ):
            warnings.simplefilter("ignore", DeprecationWarning)
            threads = [threading.Thread(target=hammer, daemon=True) for _ in range(4)]
            for t in threads:
                t.start()
            try:
                time.sleep(0.02)  # let the hammer threads get going
                for _ in range(5):
                    result = {}
                    fork_thread = threading.Thread(
                        target=do_fork, args=(result,), daemon=True
                    )
                    fork_thread.start()
                    fork_thread.join(timeout=8)
                    self.assertFalse(
                        fork_thread.is_alive(), "fork()/before() deadlocked"
                    )
                    status = result.get("status")
                    self.assertIsNotNone(status, "fork/waitpid never completed")
                    self.assertEqual(0, os.waitstatus_to_exitcode(status))
            finally:
                stop_event.set()
                for t in threads:
                    t.join(timeout=2)

        self.assertLessEqual(
            state["max"], 1, "more than one writer thread ran concurrently"
        )

    def test_early_registered_before_fork_hook_that_logs_and_flushes(self):
        """An application's own before-fork hook, registered BEFORE
        semlog.configure(), runs AFTER semlog's own before() hook (at-fork
        before hooks run in reverse registration order). If that hook logs
        and calls semlog.flush(), the re-entrant flush() must not start a
        second writer or cause a later fork to deadlock: with no restart in
        flush(), it just finds the writer before() already stopped and
        returns, matching base semantics."""
        src_dir = str(Path(__file__).resolve().parents[1] / "src")
        script = f"""
import logging, os, signal, sys


def _alarm(signum, frame):
    sys.stderr.write("DEADLOCK\\n")
    os._exit(2)


signal.signal(signal.SIGALRM, _alarm)
sys.path.insert(0, {src_dir!r})
import semlog

log = logging.getLogger("app")


def pre_fork():
    log.info("app.pre_fork")
    semlog.flush(timeout=0.2)


os.register_at_fork(before=pre_fork)  # registered before configure()
semlog.configure(service_name="svc")
log.info("app.start")

for _ in range(10):
    signal.alarm(8)
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    os.waitpid(pid, 0)
    signal.alarm(0)

sys.stdout.flush()
sys.stderr.write("OK\\n")
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(
            0, result.returncode, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        self.assertIn(b"OK", result.stderr)

    def test_full_queue_at_fork_time_does_not_deadlock(self):
        """before()'s own `_state.writer.stop()` calls `Writer.enqueue_sentinel()`
        (QueueListener.stop()'s first step) while the writer is still alive and
        actively draining. A small queue plus threads hammering both with and
        without flush() makes it likely the queue is completely full exactly
        when that sentinel needs to be enqueued: base's `put_nowait()` shape
        raises `queue.Full` there, `after_in_parent()` then starts a SECOND
        writer alongside the still-alive first one, and the next fork's
        before() deadlocks joining a writer that will never see its own
        sentinel. Runs in a subprocess with signal.alarm re-armed in the
        parent before each individual fork/waitpid pair (bounding that one
        fork, not the whole loop) and set again in each child (bounding a
        hung child), plus an outer subprocess.run timeout, so a regression
        fails cleanly instead of hanging the suite."""
        src_dir = str(Path(__file__).resolve().parents[1] / "src")
        script = f"""
import logging, os, signal, sys, threading, time


def _alarm(signum, frame):
    sys.stderr.write("DEADLOCK\\n")
    os._exit(2)


signal.signal(signal.SIGALRM, _alarm)
sys.path.insert(0, {src_dir!r})
from semlog import _transport
from semlog._format import Formatter

identity = {{
    "service.name": "svc", "service.namespace": None, "service.version": None,
    "service.instance.id": "11111111-1111-1111-1111-111111111111",
    "deployment.environment.name": None, "telemetry.sdk.name": "semlog",
    "telemetry.sdk.version": "0.0.0", "telemetry.sdk.language": "python",
}}
handler = _transport.install(
    Formatter(identity=identity), queue_size=2, stream=open(os.devnull, "wb")
)
logger = logging.getLogger("stress")
logger.handlers = [handler]
logger.propagate = False
logger.setLevel(logging.INFO)

stop = threading.Event()


def hammer_flush():
    while not stop.is_set():
        logger.info("app.hammer")
        _transport.flush(timeout=0.02)


def hammer_noflush():
    while not stop.is_set():
        logger.info("app.hammer")


threads = [threading.Thread(target=hammer_flush, daemon=True) for _ in range(2)] + [
    threading.Thread(target=hammer_noflush, daemon=True) for _ in range(2)
]
for t in threads:
    t.start()
time.sleep(0.05)


def one_fork():
    signal.alarm(8)  # bounds the parent across this one fork/waitpid pair
    pid = os.fork()
    if pid == 0:
        signal.alarm(5)  # bounds a hung child independently of the parent
        os._exit(0)
    os.waitpid(pid, 0)
    signal.alarm(0)


for _ in range(20):
    one_fork()

stop.set()
sys.stdout.flush()
sys.stderr.write("OK\\n")
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(
            0, result.returncode, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        self.assertIn(b"OK", result.stderr)


@unittest.skipUnless(hasattr(os, "fork"), "os.fork is POSIX-only")
@unittest.skipUnless(
    sys.version_info[:2] == (3, 12),
    "3.12 is where os.fork() issues the fork-with-threads DeprecationWarning "
    "early enough, relative to semlog's own before() hook, for this to matter",
)
class ForkCaptureWarningsTests(unittest.TestCase):
    """On 3.12, os.fork() in a multi-threaded process issues a
    DeprecationWarning. With logging.captureWarnings(True) and the warning
    shown, that warning is logged through root -- semlog's own queue
    handler -- possibly on the forking thread itself. With base-style
    hooks (no lock held across the fork), this must not deadlock: at most
    it lazily starts the writer for the first time, from inside the
    warning-handling code path, same as any other first log call. Runs in
    a subprocess with a bounded timeout; the child uses signal.alarm (not
    faulthandler) to turn a real deadlock into a clean, bounded, non-zero
    exit instead of hanging the test suite."""

    def test_captured_fork_warning_does_not_deadlock_the_forking_thread(self):
        src_dir = str(Path(__file__).resolve().parents[1] / "src")
        script = f"""
import logging, os, signal, sys, threading, warnings


def _on_alarm(signum, frame):
    sys.stderr.write("ALARM: forking thread deadlocked\\n")
    os._exit(2)


signal.signal(signal.SIGALRM, _on_alarm)
sys.path.insert(0, {src_dir!r})
warnings.simplefilter("always")
from semlog import _transport
from semlog._format import Formatter

identity = {{
    "service.name": "svc", "service.namespace": None, "service.version": None,
    "service.instance.id": "11111111-1111-1111-1111-111111111111",
    "deployment.environment.name": None, "telemetry.sdk.name": "semlog",
    "telemetry.sdk.version": "0.0.0", "telemetry.sdk.language": "python",
}}
handler = _transport.install(
    Formatter(identity=identity), queue_size=8, stream=open(os.devnull, "wb")
)
root = logging.getLogger()
root.handlers = [handler]
root.setLevel(logging.INFO)
logging.captureWarnings(True)
# Nothing logged yet: the handler's writer has never started.

stop = threading.Event()
background = threading.Thread(target=stop.wait, daemon=True)
background.start()

signal.alarm(8)
pid = os.fork()
if pid == 0:
    os._exit(0)
os.waitpid(pid, 0)
signal.alarm(0)
stop.set()
background.join(timeout=2)
sys.stdout.flush()
sys.stderr.write("FORK_RETURNED_OK\\n")
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(
            0,
            result.returncode,
            f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )
        self.assertIn(b"FORK_RETURNED_OK", result.stderr)


class FlushAndShutdownTests(unittest.TestCase):
    """Transport-level flush()/atexit tests (design #162 §6.4)."""

    def tearDown(self):
        if _transport._state.writer is not None:
            _safe_stop(_transport._state.writer)
        _transport._state.handler = None
        _transport._state.writer = None
        _transport._state.stream = None
        _transport._state.was_running = False

    def test_flush_blocks_until_everything_queued_is_written(self):
        stream = io.BytesIO()
        handler = _transport.install(
            Formatter(identity=_identity()),
            queue_size=8,
            overflow="block",
            stream=stream,
        )
        logger = _bind_logger("semlog.tests.flush", handler)
        self.addCleanup(setattr, logger, "handlers", [])

        logger.info("app.queued")
        semlog.flush(timeout=2)

        line = json.loads(stream.getvalue().decode("utf-8").strip())
        self.assertEqual("app.queued", line["event_name"])

    def test_flush_is_a_noop_without_an_installed_pipeline(self):
        semlog.flush(timeout=1)  # must return promptly, not hang or raise

    def test_atexit_hook_registered_exactly_once_across_multiple_installs(self):
        # `_state.atexit_registered` is process-global (design #162 §3.4: the
        # hook registers once EVER per process); other tests calling
        # `configure()` earlier in the same suite run may have already set
        # it, so this test simulates a fresh process instead of depending on
        # being the first `install()` call in the whole run.
        previously_registered = _transport._state.atexit_registered
        _transport._state.atexit_registered = False
        try:
            with mock.patch("atexit.register") as register:
                _transport.install(Formatter(identity=_identity()), stream=io.BytesIO())
                _transport.install(Formatter(identity=_identity()), stream=io.BytesIO())
            register.assert_called_once()
        finally:
            _transport._state.atexit_registered = previously_registered

    def test_shutdown_drains_and_stops_the_writer(self):
        stream = io.BytesIO()
        handler = _transport.install(
            Formatter(identity=_identity()),
            queue_size=8,
            overflow="block",
            stream=stream,
        )
        logger = _bind_logger("semlog.tests.shutdown", handler)
        self.addCleanup(setattr, logger, "handlers", [])
        logger.info("app.before.shutdown")
        self.assertIsNotNone(_transport._state.writer)

        _transport._shutdown()

        self.assertIsNone(_transport._state.writer)
        line = json.loads(stream.getvalue().decode("utf-8").strip())
        self.assertEqual("app.before.shutdown", line["event_name"])


class FlushDeadWriterTests(unittest.TestCase):
    """flush() must return promptly, without waiting out the full timeout,
    once the writer thread has already died instead of processing the
    flush marker (design D1 flush()). No requirement id is declared for
    this fix yet on its own -- see `test_writer_never_starts_at_import`
    (`WriterTests`) and `test_class_budget.py` for
    the same deferred-citation precedent."""

    def tearDown(self):
        # This class deliberately builds writers whose `_thread` was
        # assigned but never actually started, or whose queue is full: a
        # bare `.stop()` on those can raise `RuntimeError`/`queue.Full`,
        # not just `_safe_stop`'s `AttributeError`. Reset state FIRST, so a
        # broken writer this class built on purpose can never leak into a
        # later test's `install()` idempotent-reset check either way. The
        # queue is drained before `.stop()` runs, so a still-full queue left
        # behind by a deliberately-broken writer can never make this
        # cleanup step itself block.
        writer = _transport._state.writer
        handler = _transport._state.handler
        _transport._state.handler = None
        _transport._state.writer = None
        _transport._state.stream = None
        _transport._state.was_running = False
        if isinstance(handler, SemlogQueueHandler):
            while True:
                try:
                    handler.queue.get_nowait()
                except queue.Empty:
                    break
        if writer is not None:
            try:
                writer.stop()
            except Exception:  # noqa: BLE001, S110 -- cleanup only, never re-raise
                pass

    def test_flush_returns_promptly_when_the_writer_thread_already_died(self):
        stream = io.BytesIO()
        handler = _transport.install(
            Formatter(identity=_identity()), queue_size=8, stream=stream
        )
        logger = _bind_logger("semlog.tests.flush.dead", handler)
        self.addCleanup(setattr, logger, "handlers", [])

        logger.info("app.starts.writer")  # lazily starts the writer
        writer = _transport._state.writer
        self.assertIsNotNone(writer)

        def _crash(record):
            raise RuntimeError("simulated writer crash, bypassing LP-006's fix")

        writer.handle = _crash
        with mock.patch("threading.excepthook"):  # keep the deliberate crash quiet
            handler.queue.put("this record kills the writer thread")
            writer._thread.join(timeout=2)
        self.assertFalse(writer._thread.is_alive())

        started = time.monotonic()
        _transport.flush(timeout=5)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 1.0)

    def test_flush_returns_promptly_with_pending_records_and_starts_no_new_writer(self):
        """LP-011 requires flush() to detect a dead writer and return
        promptly; it does not require recovering records left behind
        (design erratum D1). A dead writer with pending records leaves
        them queued, undelivered, and flush() starts no new writer."""
        stream = io.BytesIO()
        handler = _transport.install(
            Formatter(identity=_identity()), queue_size=8, stream=stream
        )
        # Never started: `_thread` stays `None`, so it reads as dead.
        dead_writer = Writer(handler.queue, stream=stream)
        _transport._state.writer = dead_writer
        handler.queue.put('{"event_name":"app.pending"}')

        started = time.monotonic()
        _transport.flush(timeout=5)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 1.0)
        self.assertIs(dead_writer, _transport._state.writer)
        self.assertEqual(b"", stream.getvalue())

    def test_shutdown_survives_a_writer_thread_assigned_but_never_started(self):
        """Real 3.12.0 shape: `QueueListener.start()` assigns `self._thread`
        before `Thread.start()` can raise, so a writer that failed to
        actually start still has a joinable-looking `_thread`.
        `_shutdown()`'s own `_ignore(_state.writer.stop)` call must survive
        the resulting `RuntimeError: cannot join thread before it is
        started` -- reached through the real `_start_writer()` ->
        `Writer.start()` -> `Thread.start()` path, not a hand-built
        `_thread` attribute. The queue is filled to capacity first, so this
        also exercises `enqueue_sentinel()`'s non-blocking branch for a
        writer that is not alive: a mutant that always blocks the sentinel
        put (instead of only when the writer is alive and draining) would
        otherwise hang here forever, since nothing ever drains this queue.
        `_shutdown()` runs on a bounded, joined thread, so that mutant fails
        in bounded time instead of hanging the whole suite."""
        stream = io.BytesIO()
        handler = _transport.install(
            Formatter(identity=_identity()), queue_size=1, stream=stream
        )
        handler.queue.put_nowait('{"event_name":"app.pending"}')  # fills the queue

        with (
            mock.patch.object(
                threading.Thread,
                "start",
                side_effect=RuntimeError(
                    "can't create new thread at interpreter shutdown"
                ),
            ),
            self.assertRaises(RuntimeError),
        ):
            _transport._start_writer()

        result = {}

        def call_shutdown():
            _transport._shutdown()
            result["done"] = True

        t = threading.Thread(target=call_shutdown, daemon=True)
        t.start()
        t.join(timeout=2)
        self.assertTrue(
            result.get("done", False), "_shutdown() did not return within 2s"
        )

    def test_shutdown_survives_a_full_queue_when_stopping_the_writer(self):
        """The queue.Full variant of the same 3.12.0 shutdown crash shape:
        a writer that is not alive keeps the non-blocking
        `enqueue_sentinel()` shape, which raises `queue.Full` when the
        queue has no room left for the stop sentinel. Runs `_shutdown()` on
        a daemon thread with a bounded join, so a regression here fails
        cleanly instead of hanging this test."""
        stream = io.BytesIO()
        handler = _transport.install(
            Formatter(identity=_identity()), queue_size=1, stream=stream
        )
        handler.queue.put_nowait("occupying the only slot")
        _transport._state.writer = Writer(handler.queue, stream=stream)

        result = {}

        def call_shutdown():
            _transport._shutdown()
            result["done"] = True

        t = threading.Thread(target=call_shutdown, daemon=True)
        t.start()
        t.join(timeout=2)
        self.assertTrue(
            result.get("done", False), "_shutdown() did not return within 2s"
        )

    def test_flush_returns_when_the_writer_dies_while_the_marker_is_pending(self):
        """Proves the writer-death check INSIDE flush()'s wait loop (not
        just the check made before entering it, already covered above):
        deleting that check must turn this test red, since flush() would
        then wait out the full timeout for a marker nobody will ever set."""
        stream = io.BytesIO()
        handler = _transport.install(
            Formatter(identity=_identity()), queue_size=8, stream=stream
        )
        logger = _bind_logger("semlog.tests.flush.dies.mid.wait", handler)
        self.addCleanup(setattr, logger, "handlers", [])

        logger.info("app.starts.writer")  # lazily starts the writer
        writer = _transport._state.writer
        self.assertIsNotNone(writer)
        _transport.flush(timeout=2)  # drain that first record cleanly

        def _crash_on_marker(record):
            if isinstance(record, threading.Event):
                # Simulating a writer crash on the marker, not a type check.
                raise RuntimeError(  # noqa: TRY004
                    "writer dies right as it picks up the marker"
                )

        writer.handle = _crash_on_marker

        started = time.monotonic()
        with mock.patch("threading.excepthook"):  # keep the deliberate crash quiet
            _transport.flush(timeout=5)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 1.0)

    def test_flush_timeout_zero_returns_quickly_without_waiting_a_full_slice(self):
        """flush(timeout=0) must not wait out a full 50ms slice when the
        writer is alive but still busy handling an earlier record."""
        gate = threading.Event()

        class SlowStream:
            def write(self, data):
                gate.wait(2)
                return len(data)

            def flush(self):
                pass

        handler = _transport.install(
            Formatter(identity=_identity()), queue_size=8, stream=SlowStream()
        )
        logger = _bind_logger("semlog.tests.flush.timeout0", handler)
        self.addCleanup(setattr, logger, "handlers", [])
        logger.info("app.x")
        time.sleep(0.05)  # let the writer pick it up and block inside write()

        started = time.monotonic()
        _transport.flush(timeout=0)
        elapsed = time.monotonic() - started

        gate.set()
        _transport.flush(timeout=2)  # drain cleanly before teardown
        self.assertLess(elapsed, 0.03)

    def test_flush_timeout_bounds_the_marker_put_when_the_queue_is_full_and_stuck(self):
        """flush()'s timeout must bound the marker put itself, not just the
        wait for it to be processed: a plain queue.put(marker) has no
        timeout at all, so a full queue with a stuck (but alive) writer can
        block flush() far longer than the timeout it was given. Runs the
        call on a daemon thread with a bounded join so a regression here
        fails cleanly instead of hanging this test."""
        gate = threading.Event()

        class StuckStream:
            def write(self, data):
                gate.wait(5)
                return len(data)

            def flush(self):
                pass

        handler = _transport.install(
            Formatter(identity=_identity()), queue_size=1, stream=StuckStream()
        )
        logger = _bind_logger("semlog.tests.flush.stuck", handler)
        self.addCleanup(setattr, logger, "handlers", [])
        logger.info("app.first")  # the writer picks this up and blocks in write()
        time.sleep(0.05)
        handler.queue.put_nowait("filler")  # the only free slot: queue now full

        result = {}

        def call_flush():
            started = time.monotonic()
            _transport.flush(timeout=0.1)
            result["elapsed"] = time.monotonic() - started

        t = threading.Thread(target=call_flush, daemon=True)
        t.start()
        t.join(timeout=2)
        gate.set()
        self.assertFalse(t.is_alive(), "flush(timeout=0.1) did not return within 2s")
        self.assertLess(result.get("elapsed", 999), 0.5)
        _transport.flush(timeout=2)


class ShutdownNonRootTests(unittest.TestCase):
    """`_shutdown` must not raise when its handler is not currently
    attached to the root logger, and the post-shutdown fallback handler
    must resolve `stderr` the same robust way `Writer.handle` resolves
    `stdout` (design D1 `_shutdown`). No requirement id is declared for
    this fix yet on its own -- see `test_writer_never_starts_at_import`
    (`WriterTests`) and `test_class_budget.py` for
    the same deferred-citation precedent."""

    def tearDown(self):
        if _transport._state.writer is not None:
            _safe_stop(_transport._state.writer)
        _transport._state.handler = None
        _transport._state.writer = None
        _transport._state.stream = None
        _transport._state.was_running = False

    def test_shutdown_swaps_state_handler_even_when_never_attached_to_root(self):
        stream = io.BytesIO()
        handler = _transport.install(
            Formatter(identity=_identity()), queue_size=8, stream=stream
        )
        # `handler` is never added to the root logger.
        _transport._shutdown()  # must not raise
        self.assertIsNot(handler, _transport._state.handler)
        self.assertNotIsInstance(_transport._state.handler, SemlogQueueHandler)

    def test_shutdown_replaces_root_handler_only_when_attached(self):
        stream = io.BytesIO()
        handler = _transport.install(
            Formatter(identity=_identity()), queue_size=8, stream=stream
        )
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            _transport._shutdown()
            self.assertNotIn(handler, root.handlers)
            self.assertIn(_transport._state.handler, root.handlers)
        finally:
            root.removeHandler(_transport._state.handler)

    def test_shutdown_swapped_handler_is_removed_by_a_later_attach_root(self):
        """The direct handler _shutdown() swaps in must carry the same
        `_semlog_root` marker `attach_root()` uses, or a later
        install()+attach_root() pair leaves it orphaned on root forever."""
        stream = io.BytesIO()
        handler = _transport.install(
            Formatter(identity=_identity()), queue_size=8, stream=stream
        )
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            _transport._shutdown()
            swapped = _transport._state.handler
            self.assertIn(swapped, root.handlers)
            self.assertTrue(getattr(swapped, "_semlog_root", False))

            new_handler = _transport.install(
                Formatter(identity=_identity()), queue_size=8, stream=io.BytesIO()
            )
            _transport.attach_root(new_handler)

            self.assertNotIn(swapped, root.handlers)
            self.assertIn(new_handler, root.handlers)
        finally:
            root.removeHandler(_transport._state.handler)

    def test_shutdown_falls_back_to_null_handler_when_stderr_is_closed(self):
        """A daemonizing process that closes sys.stderr before atexit runs
        must not crash _shutdown(): wrapping an already-closed buffer in a
        fresh TextIOWrapper raises ValueError; _shutdown() falls back to a
        NullHandler in that case instead of letting it escape."""
        stream = io.BytesIO()
        handler = _transport.install(
            Formatter(identity=_identity()), queue_size=8, stream=stream
        )
        root = logging.getLogger()
        root.addHandler(handler)

        closed_stderr = io.TextIOWrapper(io.BytesIO())
        closed_stderr.close()
        try:
            with mock.patch("sys.stderr", closed_stderr):
                _transport._shutdown()  # must not raise
            self.assertIsInstance(_transport._state.handler, logging.NullHandler)
        finally:
            root.removeHandler(_transport._state.handler)

    def test_post_shutdown_logging_falls_back_to_dunder_stderr(self):
        stream = io.BytesIO()
        handler = _transport.install(
            Formatter(identity=_identity()), queue_size=8, stream=stream
        )
        root = logging.getLogger()
        root.addHandler(handler)
        previous_level = root.level
        root.setLevel(logging.INFO)
        fallback = SimpleNamespace(buffer=io.BytesIO())
        try:
            with (
                mock.patch("sys.stderr", object()),
                mock.patch("sys.__stderr__", fallback),
            ):
                _transport._shutdown()
                root.info("app.after.shutdown")
        finally:
            root.removeHandler(_transport._state.handler)
            root.setLevel(previous_level)

        line = json.loads(fallback.buffer.getvalue().decode("utf-8").strip())
        self.assertEqual("app.after.shutdown", line["event_name"])


class ConcurrentEmissionTests(unittest.TestCase):
    """Proves: LP-008, LP-009"""

    def test_fifty_threads_hundred_records_each_yields_five_thousand_valid_lines(self):
        q = queue.Queue(maxsize=256)
        handler = SemlogQueueHandler(q, overflow="block")
        handler.setFormatter(Formatter(identity=_identity()))
        stream = io.BytesIO()
        writer = Writer(q, stream=stream)
        writer.start()
        self.addCleanup(_safe_stop, writer)

        def produce(thread_id):
            for i in range(100):
                # Each segment must start with a letter (the event-name
                # grammar): a bare digit segment would silently fall back
                # to `event_name=None` in every record (a real test bug
                # caught by this test's own first run).
                handler.emit(_record(event=f"app.thread{thread_id}.msg{i}"))

        threads = [threading.Thread(target=produce, args=(t,)) for t in range(50)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())

        _safe_stop(writer)  # deterministic full drain before reading output

        lines = stream.getvalue().decode("utf-8").splitlines()
        self.assertEqual(5000, len(lines))
        seen = set()
        for line in lines:
            parsed = json.loads(line)  # every line is well-formed, non-interleaved JSON
            seen.add(parsed["event_name"])
        self.assertEqual(5000, len(seen))  # no duplicate/corrupted event names
        self.assertEqual(0, handler.counters.dropped)


if __name__ == "__main__":
    unittest.main()
