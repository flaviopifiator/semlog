"""Subject adapters (task 5.1/5.2): one function per compared logger.
Every adapter returns `(emit, shutdown)`: `emit(*, user_id, request_id,
duration_ms)` performs exactly one logging call that writes exactly one
JSON line to the caller-supplied binary sink; `shutdown()` flushes and
releases the subject's resources (never closes the caller's sink).

Fairness (task 5.1): every subject is configured to encode the SAME
call-site content -- one event name plus three attributes (`user_id`,
`request_id`, `duration_ms`) -- as JSON, to the same kind of sink. Field
NAMES differ across libraries by design (each keeps its own natural
shape); `benchmark.fairness` normalizes those differences and documents
them. What is deliberately NOT equalized, and why, is disclosed in
BENCHMARKS.md's methodology section:

- semlog additionally renders its full OTel-shaped envelope (severity,
  identity, telemetry.sdk.*) on every record. That is semlog's real,
  always-on record contract (LRC-001..013) -- stripping it for the
  benchmark would measure a configuration no real semlog user has.
  `like_for_like.py` publishes the complementary view, where every
  subject renders that same envelope.
- semlog's writer is asynchronous (a queue plus a writer thread by
  design, LP-002/LP-009): its measured per-call cost is render+enqueue
  only, never the write itself. stdlib's `StreamHandler`, loguru
  (`enqueue=False`) and structlog's `PrintLogger` all write
  synchronously inside the call, by their own respective defaults.
  Both are each library's genuine default production behavior.
- I/O flush timing differs by each library's own default: stdlib's
  `StreamHandler` flushes after every record; loguru and structlog rely
  on Python's normal buffered text I/O. None of this is forced to be
  equal, since doing so would mean configuring a library away from how
  its own users actually run it.

The two third-party subjects (`loguru`, `structlog`, STANDARDS.md
CP-008/CP-013) are imported lazily, inside their own factory function,
so importing this module never requires them to be installed.
"""

from __future__ import annotations

import io
import json
import logging


def _semlog_identity() -> dict[str, object]:
    return {
        "service.name": "bench-svc",
        "service.namespace": None,
        "service.version": "0.0.0",
        "service.instance.id": "00000000-0000-0000-0000-000000000000",
        "deployment.environment.name": None,
        "telemetry.sdk.name": "semlog",
        "telemetry.sdk.version": "0.0.0",
        "telemetry.sdk.language": "python",
    }


def build_semlog_pipeline(stream, *, queue_maxsize=100_000, overflow="block"):
    """Build the same three fixed stages `semlog.configure()` wires
    internally (formatter, queue handler, writer thread), pointed at a
    caller-supplied binary `stream` instead of `sys.stdout`. Used directly
    (not through `configure()`) only because the public entry point has
    no sink parameter to redirect -- these are the exact same production
    classes (`semlog._format.Formatter`, `semlog._transport.
    SemlogQueueHandler`/`Writer`), not a reimplementation, so the
    measured pipeline is the one every `configure()` caller gets."""
    import queue as _queue

    from semlog._format import Formatter
    from semlog._transport import SemlogQueueHandler, Writer

    formatter = Formatter(identity=_semlog_identity(), namespace="app")
    q = _queue.Queue(maxsize=queue_maxsize)
    handler = SemlogQueueHandler(q, overflow=overflow)
    handler.setFormatter(formatter)
    writer = Writer(q, namespace="app", stream=stream)
    writer.start()
    return handler, writer


def _stop_writer(writer):
    try:
        writer.stop()  # drains everything queued before this call first
    except AttributeError:
        pass  # already stopped


def make_semlog(sink):
    handler, writer = build_semlog_pipeline(sink)
    logger = logging.getLogger(f"benchmark.semlog.{id(sink)}")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    def emit(*, user_id, request_id, duration_ms):
        logger.info(
            "bench.request.completed",
            extra={
                "app.user_id": user_id,
                "app.request_id": request_id,
                "app.duration_ms": duration_ms,
            },
        )

    def shutdown():
        _stop_writer(writer)
        logger.handlers = []

    return emit, shutdown


class _JSONLineFormatter(logging.Formatter):
    """A minimal, hand-written JSON formatter for the stdlib baseline --
    no third-party JSON logging package, per CP-008/CP-013."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "event": record.getMessage(),
            "user_id": getattr(record, "user_id", None),
            "request_id": getattr(record, "request_id", None),
            "duration_ms": getattr(record, "duration_ms", None),
        }
        return json.dumps(payload, separators=(",", ":"))


def make_stdlib_baseline(sink):
    text_stream = io.TextIOWrapper(sink, encoding="utf-8", newline="\n")
    handler = logging.StreamHandler(text_stream)
    handler.terminator = "\n"
    handler.setFormatter(_JSONLineFormatter())

    logger = logging.getLogger(f"benchmark.stdlib.{id(sink)}")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    def emit(*, user_id, request_id, duration_ms):
        logger.info(
            "bench.request.completed",
            extra={
                "user_id": user_id,
                "request_id": request_id,
                "duration_ms": duration_ms,
            },
        )

    def shutdown():
        handler.flush()
        text_stream.flush()
        text_stream.detach()  # never close the caller's underlying sink
        logger.handlers = []

    return emit, shutdown


def make_loguru(sink):
    from loguru import logger as _base

    text_stream = io.TextIOWrapper(sink, encoding="utf-8", newline="\n")
    _base.remove()  # clear this process-wide singleton's own default handler
    handler_id = _base.add(
        text_stream,
        format="{message}",
        serialize=True,
        colorize=False,
        enqueue=False,  # loguru's own synchronous default; fairness note above
    )
    bound = _base.bind()

    def emit(*, user_id, request_id, duration_ms):
        bound.bind(
            user_id=user_id, request_id=request_id, duration_ms=duration_ms
        ).info("bench.request.completed")

    def shutdown():
        _base.remove(handler_id)
        text_stream.flush()
        text_stream.detach()

    return emit, shutdown


def make_structlog(sink):
    import structlog

    text_stream = io.TextIOWrapper(sink, encoding="utf-8", newline="\n")
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=text_stream),
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=False,
    )
    logger = structlog.get_logger("benchmark.structlog")

    def emit(*, user_id, request_id, duration_ms):
        logger.info(
            "bench.request.completed",
            user_id=user_id,
            request_id=request_id,
            duration_ms=duration_ms,
        )

    def shutdown():
        text_stream.flush()
        text_stream.detach()

    return emit, shutdown


SUBJECT_FACTORIES = {
    "semlog": make_semlog,
    "stdlib_baseline": make_stdlib_baseline,
    "loguru": make_loguru,
    "structlog": make_structlog,
}
