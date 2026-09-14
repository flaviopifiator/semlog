"""Transport: bounded queue, overflow, lazy-start writer, fork safety, flush
(design #162 §1.1/§2.1/§3.4/§6.2/§6.4; LP-003/006/007/008/009/010, CP-006)."""

from __future__ import annotations

import atexit
import io
import json
import logging
import logging.handlers
import math
import os
import queue as _queue
import sys
import threading
from types import SimpleNamespace

_WARNING = logging.WARNING

# Module state, not a configuration object (`_config.py`'s pattern); the
# writer stays `None` until it starts lazily (LP-003).
_state = SimpleNamespace(
    handler=None,
    writer=None,
    namespace="app",
    stream=None,
    was_running=False,
    registered=False,
    atexit_registered=False,
)


def _ignore(fn, exc=Exception):
    """Run `fn`; discard the given exception type(s) (never raise, LP-006)."""
    try:
        fn()
    except exc:
        pass


class SemlogQueueHandler(logging.handlers.QueueHandler):
    """Transport entry: no handler lock, bounded overflow policy (LP-007/009)."""

    def __init__(self, q, *, overflow="block", counters=None, on_first_emit=None):
        super().__init__(q)
        self.overflow = overflow
        self.counters = counters if counters is not None else SimpleNamespace(dropped=0)
        self._on_first_emit = on_first_emit
        self._started = False
        self._start_lock = threading.Lock()

    def prepare(self, record):
        return self.format(record)

    def handle(self, record):
        if self.filter(record):  # no handler lock (design §6.3)
            self.emit(record)
        return True

    def emit(self, record):
        if self._on_first_emit is not None and not self._started:
            with self._start_lock:
                if not self._started:
                    self._started = True
                    self._on_first_emit()
        try:
            line = self.prepare(record)
        except Exception:  # noqa: BLE001 -- logging must never raise (design §6.5)
            self.counters.dropped += 1
            return
        q = self.queue
        if self.overflow == "block":
            q.put(line)
            return
        maxsize = q.maxsize
        reserved = record.levelno >= _WARNING
        threshold = maxsize if reserved else math.ceil(maxsize * 0.9)
        if maxsize and q.qsize() >= threshold:
            self.counters.dropped += 1
            return
        try:
            q.put_nowait(line)
        except _queue.Full:
            self.counters.dropped += 1


class Writer(logging.handlers.QueueListener):
    """Transport exit: writes lines to stdout, never raises (LP-003/006)."""

    def __init__(self, q, *, namespace="app", counters=None, stream=None):
        super().__init__(q)
        self.namespace = namespace
        self.counters = counters if counters is not None else SimpleNamespace(dropped=0)
        self._stream = stream
        self._reported = 0

    def handle(self, record):
        stream = self._stream if self._stream is not None else sys.stdout.buffer
        if isinstance(record, threading.Event):
            _ignore(stream.flush)  # best-effort flush before releasing the caller
            record.set()
            return
        try:
            stream.write(record.encode("utf-8", "backslashreplace") + b"\n")
            self._report_drops(stream)
            if self.queue.empty():
                stream.flush()
        except Exception:  # noqa: BLE001 -- never raise out of handle (LP-006)
            self.counters.dropped += 1

    def _report_drops(self, stream):
        """Best-effort `{ns}.log.records_dropped` notice for the counter delta."""
        delta = self.counters.dropped - self._reported
        if not delta:
            return
        self._reported = self.counters.dropped
        try:
            note = json.dumps(
                {f"{self.namespace}.log.records_dropped": delta}, separators=(",", ":")
            )
            stream.write((note + "\n").encode("utf-8"))
            stream.flush()
        except Exception:  # noqa: BLE001, S110 -- best-effort notice, never raise
            pass


def _start_writer():
    """Lazy-start callback (LP-003): build and start a `Writer` for the current queue."""
    if _state.handler is None:
        return
    _state.writer = Writer(
        _state.handler.queue,
        namespace=_state.namespace,
        counters=_state.handler.counters,
        stream=_state.stream,
    )
    _state.writer.start()


def _direct_handler(stream):
    """Build the `queue=False` synchronous handler (design #162/#170 §3.3):
    a plain stdlib `StreamHandler` writing UTF-8 with `backslashreplace`, no
    internal queue and no writer thread -- like a bare stdlib `StreamHandler`."""
    sink = stream if stream is not None else sys.stdout.buffer
    text = io.TextIOWrapper(
        sink, encoding="utf-8", errors="backslashreplace", newline="\n"
    )
    return logging.StreamHandler(text)


def install(
    formatter,
    *,
    queue=True,
    queue_size=10000,
    overflow="block",
    namespace="app",
    stream=None,
):
    """Build this process's queue handler; the writer starts lazily (design
    §3.3/§3.4). `queue=False` installs a synchronous handler instead."""
    if _state.writer is not None:  # idempotent: drain/stop the prior call's writer
        _ignore(_state.writer.stop, AttributeError)
    if queue:
        q = _queue.Queue(maxsize=queue_size)
        handler = SemlogQueueHandler(q, overflow=overflow, on_first_emit=_start_writer)
    else:
        handler = _direct_handler(stream)
    handler.setFormatter(formatter)
    _state.handler, _state.writer = handler, None
    _state.namespace, _state.stream = namespace, stream
    _state.was_running = False
    _register_at_fork()
    if not _state.atexit_registered:
        _state.atexit_registered = True
        atexit.register(_shutdown)
    return handler


def _shutdown():
    """Drain and stop the writer before atexit's own teardown, switching the
    root handler to a direct, threadless one (design §6.4)."""
    handler = _state.handler
    if _state.writer is not None:
        flush(timeout=5)
        _ignore(_state.writer.stop, AttributeError)
        _state.writer = None
    root = logging.getLogger()
    if isinstance(handler, SemlogQueueHandler) and handler in root.handlers:
        direct = logging.StreamHandler()
        direct.setFormatter(handler.formatter)
        root.handlers[root.handlers.index(handler)] = direct
        _state.handler = direct


def _register_at_fork():
    """Register the three at-fork hooks once per process (LP-003); POSIX only."""
    if _state.registered or not hasattr(os, "register_at_fork"):
        return
    _state.registered = True

    def before():
        _state.was_running = _state.writer is not None
        if _state.was_running:
            _ignore(_state.writer.stop, AttributeError)

    def after_in_parent():
        if _state.was_running:
            _start_writer()

    def after_in_child():
        handler = _state.handler
        if isinstance(handler, SemlogQueueHandler):
            handler.queue = _queue.Queue(maxsize=handler.queue.maxsize)
            handler.counters = SimpleNamespace(dropped=0)
            handler._started = False
        _state.writer, _state.was_running = None, False

    os.register_at_fork(
        before=before, after_in_parent=after_in_parent, after_in_child=after_in_child
    )


def flush(timeout=None):
    """Block until everything enqueued before this call is written (design §6.4)."""
    handler = _state.handler
    if handler is None or not isinstance(handler, SemlogQueueHandler):
        return
    if _state.writer is None:
        _start_writer()
    marker = threading.Event()
    handler.queue.put(marker)
    marker.wait(timeout=timeout)
