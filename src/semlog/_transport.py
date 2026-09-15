"""Transport: bounded queue, overflow, lazy-start writer, fork safety, flush
(design #162 §1.1/§2.1/§3.4/§6.2/§6.4; LP-003/006/007/008/009/010, CP-006).
Stream resolution is robust to a replaced `sys.stdout`/`sys.stderr` that
lacks a `.buffer` attribute; `flush()` detects a dead writer thread and
returns promptly instead of waiting out its full timeout (LP-006/011)."""

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
import time
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


def _resolve_stream(explicit, primary, fallback):
    """Resolve the binary write stream: `explicit`, else `primary.buffer`,
    else `fallback.buffer`; `None` when neither has one (LP-006)."""
    if explicit is not None:
        return explicit
    stream = getattr(primary, "buffer", None)
    if stream is not None:
        return stream
    return getattr(fallback, "buffer", None)


def _text_handler(sink):
    """UTF-8, backslashreplace `StreamHandler` over `sink`; `close()` only
    flushes, since `sink` may be a shared `sys.stdout`/`stderr` buffer."""
    text = io.TextIOWrapper(
        sink, encoding="utf-8", errors="backslashreplace", newline="\n"
    )
    text.close = text.flush  # never close a shared process-level buffer
    return logging.StreamHandler(text)


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

    def enqueue_sentinel(self):
        # A live writer drains the queue: wait for room instead of raising queue.Full.
        self.queue.put(self._sentinel, block=_writer_alive(self))

    def handle(self, record):
        # Resolution itself must never raise (LP-006): a replaced `sys.stdout`
        # without a `.buffer` attribute, or no stdout at all, must not kill
        # this thread or leave a flush marker unset.
        try:
            stream = _resolve_stream(self._stream, sys.stdout, sys.__stdout__)
        except Exception:  # noqa: BLE001 -- resolution itself must never raise
            stream = None
        if isinstance(record, threading.Event):
            if stream is not None:
                _ignore(stream.flush)  # best-effort flush before releasing the caller
            record.set()  # always set, even on a failed/absent stream
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
    """`queue=False` synchronous handler (design #162/#170 §3.3): a plain
    UTF-8/backslashreplace `StreamHandler`, no queue and no writer thread,
    robust to a `sys.stdout` without `.buffer` (LP-006); `NullHandler` when
    neither `sys.stdout` nor `sys.__stdout__` has one."""
    try:
        sink = _resolve_stream(stream, sys.stdout, sys.__stdout__)
    except Exception:  # noqa: BLE001 -- resolution itself must never raise
        sink = None
    if sink is None:
        return logging.NullHandler()
    return _text_handler(sink)


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
    """Drain/stop the writer before atexit's teardown; swap `_state.handler`
    to a direct stderr handler whether or not it was attached to root (only
    the root handler list is touched when it was, LP-012). Stopping the
    writer ignores any exception, not only `AttributeError`: on 3.12.0,
    `QueueListener.start()` assigns `_thread` before `Thread.start()` can
    raise, so a failed start can leave a writer whose `.stop()` raises
    `RuntimeError: cannot join thread before it is started`; a full queue
    makes the same `.stop()` call raise `queue.Full` instead."""
    handler = _state.handler
    if _state.writer is not None:
        flush(timeout=5)
        _ignore(_state.writer.stop)
        _state.writer = None
    if isinstance(handler, SemlogQueueHandler):
        try:
            sink = _resolve_stream(None, sys.stderr, sys.__stderr__)
            direct = _text_handler(sink) if sink is not None else logging.NullHandler()
        except Exception:  # noqa: BLE001 -- a closed sys.stderr raises ValueError here
            direct = logging.NullHandler()
        direct.setFormatter(handler.formatter)
        direct._semlog_root = True  # so a later attach_root() can remove it
        root = logging.getLogger()
        if handler in root.handlers:
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


_FLUSH_SLICE = 0.05


def _writer_alive(writer):
    thread = getattr(writer, "_thread", None)
    return thread is not None and thread.is_alive()


def flush(timeout=None):
    """Block until everything enqueued before this call is written (design
    §6.4). Returns promptly, without waiting out `timeout`, once the writer
    thread is dead or was never started (LP-011); it does not start one, so
    any record already queued when that happens stays queued, undelivered,
    until something else starts a writer."""
    handler = _state.handler
    if handler is None or not isinstance(handler, SemlogQueueHandler):
        return
    writer = _state.writer
    if writer is None or not _writer_alive(writer):
        return
    marker = threading.Event()
    deadline = None if timeout is None else time.monotonic() + timeout
    try:
        if deadline is None:
            handler.queue.put(marker)
        else:
            handler.queue.put(marker, timeout=max(0.0, deadline - time.monotonic()))
    except _queue.Full:
        return
    # The wait runs in slices bounded by what remains of `timeout`, so a
    # writer that dies mid-wait, or a deadline that passes, still returns
    # promptly -- including a `timeout=0` caller, which must not wait out a
    # full slice.
    while True:
        if deadline is None:
            slice_timeout = _FLUSH_SLICE
        else:
            slice_timeout = min(_FLUSH_SLICE, max(0.0, deadline - time.monotonic()))
        if marker.wait(timeout=slice_timeout):
            return
        if not _writer_alive(writer):
            return
        if deadline is not None and time.monotonic() >= deadline:
            return


def attach_root(handler, level=None):
    """Attach `handler` to root, replacing every previously attached
    semlog handler and leaving every other handler untouched; for later
    mode-resolution work (design D2), not yet called by anything in this
    module. Tracks "previously attached" by marking each handler this
    function itself attaches, rather than reading `_state.handler`:
    `install()` already overwrites `_state.handler` before `attach_root()`
    runs, so `_state.handler` can no longer identify a handler a prior
    call left on root, and it would otherwise stay there as an orphan.
    Sets an explicit `level` on root only when given, so a caller whose
    level must stay untouched can pass `None`."""
    root = logging.getLogger()
    for existing in list(root.handlers):
        if existing is not handler and getattr(existing, "_semlog_root", False):
            root.removeHandler(existing)
    handler._semlog_root = True
    if handler not in root.handlers:
        root.addHandler(handler)
    if level is not None:
        root.setLevel(level)
    _state.handler = handler


def capture_loggers(names):
    """Detach every handler already registered on each named logger and
    turn propagation on, so its records reach the handler this module
    installed on root instead (design D2)."""
    for name in names:
        captured = logging.getLogger(name)
        for existing in list(captured.handlers):
            captured.removeHandler(existing)
        captured.propagate = True
