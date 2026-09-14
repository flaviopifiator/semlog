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
import threading
import time
import unittest
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
