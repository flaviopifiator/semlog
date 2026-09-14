"""Like-for-like scenario: every subject emits semlog's exact field set.

The default scenario (`subjects.py`) keeps each library's own natural,
default-configured output, so semlog renders far more than the others. This
view removes that difference: all four subjects emit the SAME 17 fields, in
semlog's envelope order, for the same call -- timestamp, severity text and
number, event name, body, scope name, the three call-site attributes, and
the 8 identity fields. Identity is configuration-time data in every
subject, merged into each record at format time.

- semlog: the real pipeline (`subjects.build_semlog_pipeline`), unchanged.
- stdlib baseline: `logging` plus a hand-written JSON formatter building
  the same record (`logging.StreamHandler`, synchronous).
- structlog ON TOP OF stdlib logging, as structlog's "standard library"
  documentation recommends for rendering within `logging`:
  `structlog.stdlib.LoggerFactory` plus `structlog.stdlib.BoundLogger`,
  ending in `ProcessorFormatter.wrap_for_formatter`, and a
  `structlog.stdlib.ProcessorFormatter` on a `logging.StreamHandler`.
  Unlike the default scenario's `PrintLoggerFactory`, every call pays
  stdlib dispatch and `LogRecord` creation, exactly as semlog does.
- loguru: loguru's documented custom-serialization recipe (a patcher that
  stores the serialized record in `extra`, and a sink format that prints
  it), synchronous (`enqueue=False`).

What stays deliberately different is the transport, as in the default
scenario: semlog renders and enqueues on the caller's thread and writes on
its writer thread; the other three write synchronously inside the call.

All subjects log under one scope name (`SCOPE_NAME`, this module's own
name, which is also what loguru reports for a call made from this module),
so at most one like-for-like subject may be alive at a time.

Every subject has a builder, `build_*(sink, attribute_keys)`, returning
`(emit, shutdown)` where `emit(attributes)` logs one record carrying exactly
those attribute keys, in that order. The `make_*` factories wrap a builder
with the three call-site attributes and the subject adapters' keyword
signature; the field-scaling scenario (`field_scaling.py`) calls the
builders directly with a growing number of attributes.
"""

from __future__ import annotations

import datetime
import importlib.util
import io
import json
import logging
import re

from .subjects import _semlog_identity, _stop_writer, build_semlog_pipeline

SCOPE_NAME = __name__
EVENT_NAME = "bench.request.completed"
IDENTITY = _semlog_identity()
CALL_ATTRIBUTES = ("app.user_id", "app.request_id", "app.duration_ms")


def envelope_fields(attribute_keys):
    """semlog's envelope order for a record carrying `attribute_keys`."""
    return (
        "timestamp",
        "severity_text",
        "severity_number",
        "event_name",
        "body",
        "otel.scope.name",
        *attribute_keys,
        *IDENTITY,
    )


FIELDS = envelope_fields(CALL_ATTRIBUTES)
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")

_REQUIRED_PACKAGE = {"loguru": "loguru", "structlog_stdlib": "structlog"}


def _severity_number(levelno):
    """The same OTel severity banding semlog applies (STANDARDS §3)."""
    return 1 + 4 * min(levelno // 10, 5) + min(levelno % 10, 3)


def _attributes(user_id, request_id, duration_ms):
    return {
        "app.user_id": user_id,
        "app.request_id": request_id,
        "app.duration_ms": duration_ms,
    }


def _with_call_signature(build):
    """Turn a builder into a `make_*` factory with the adapters' signature."""

    def make(sink):
        emit_attributes, shutdown = build(sink, CALL_ATTRIBUTES)

        def emit(*, user_id, request_id, duration_ms):
            emit_attributes(_attributes(user_id, request_id, duration_ms))

        return emit, shutdown

    return make


def _compact_json(payload):
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def _scope_logger(handler):
    logger = logging.getLogger(SCOPE_NAME)
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    return logger


def _text_stream(sink):
    return io.TextIOWrapper(sink, encoding="utf-8", newline="\n")


def build_semlog(sink, attribute_keys=CALL_ATTRIBUTES):
    handler, writer = build_semlog_pipeline(sink)
    logger = _scope_logger(handler)

    def emit(attributes):
        logger.info(EVENT_NAME, extra=attributes)

    def shutdown():
        _stop_writer(writer)
        logger.handlers = []

    return emit, shutdown


class _EnvelopeJSONFormatter(logging.Formatter):
    """semlog's field set, hand-written on stdlib only (CP-008/CP-013)."""

    def __init__(self, attribute_keys=CALL_ATTRIBUTES):
        super().__init__()
        self._attribute_keys = attribute_keys

    def format(self, record):
        created = datetime.datetime.fromtimestamp(
            record.created, tz=datetime.timezone.utc
        )
        payload = {
            "timestamp": created.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "severity_text": record.levelname,
            "severity_number": _severity_number(record.levelno),
            "event_name": record.getMessage(),
            "body": None,
            "otel.scope.name": record.name,
        }
        fields = record.__dict__
        for key in self._attribute_keys:
            payload[key] = fields[key]
        payload.update(IDENTITY)
        return _compact_json(payload)


def build_stdlib_baseline(sink, attribute_keys=CALL_ATTRIBUTES):
    text_stream = _text_stream(sink)
    handler = logging.StreamHandler(text_stream)
    handler.setFormatter(_EnvelopeJSONFormatter(attribute_keys))
    logger = _scope_logger(handler)

    def emit(attributes):
        logger.info(EVENT_NAME, extra=attributes)

    def shutdown():
        handler.flush()
        text_stream.flush()
        text_stream.detach()  # never close the caller's underlying sink
        logger.handlers = []

    return emit, shutdown


def _structlog_envelope(attribute_keys):
    """ProcessorFormatter step: rebuild the event dict as semlog's envelope,
    reading severity and scope from the wrapped stdlib `LogRecord`."""

    def processor(_logger, _method_name, event_dict):
        record = event_dict["_record"]
        envelope = {
            "timestamp": event_dict["timestamp"],
            "severity_text": record.levelname,
            "severity_number": _severity_number(record.levelno),
            "event_name": event_dict["event"],
            "body": None,
            "otel.scope.name": record.name,
        }
        for key in attribute_keys:
            envelope[key] = event_dict[key]
        envelope["_record"] = record
        envelope["_from_structlog"] = event_dict["_from_structlog"]
        envelope.update(IDENTITY)
        return envelope

    return processor


def build_structlog_stdlib(sink, attribute_keys=CALL_ATTRIBUTES):
    import structlog

    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    text_stream = _text_stream(sink)
    handler = logging.StreamHandler(text_stream)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=[
                _structlog_envelope(attribute_keys),
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(
                    separators=(",", ":"), ensure_ascii=False
                ),
            ],
        )
    )
    _scope_logger(handler)
    logger = structlog.get_logger(SCOPE_NAME)

    def emit(attributes):
        logger.info(EVENT_NAME, **attributes)

    def shutdown():
        handler.flush()
        text_stream.flush()
        text_stream.detach()
        logging.getLogger(SCOPE_NAME).handlers = []
        structlog.reset_defaults()

    return emit, shutdown


def _loguru_patcher(attribute_keys):
    """loguru's custom-serialization recipe: serialize into `extra`."""

    def patcher(record):
        utc = record["time"].astimezone(datetime.timezone.utc)
        level = record["level"]
        extra = record["extra"]
        payload = {
            "timestamp": utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "severity_text": level.name,
            "severity_number": _severity_number(level.no),
            "event_name": record["message"],
            "body": None,
            "otel.scope.name": record["name"],
        }
        for key in attribute_keys:
            payload[key] = extra[key]
        payload.update(IDENTITY)
        extra["serialized"] = _compact_json(payload)

    return patcher


def build_loguru(sink, attribute_keys=CALL_ATTRIBUTES):
    from loguru import logger as _base

    text_stream = _text_stream(sink)
    _base.remove()  # clear this process-wide singleton's own default handler
    handler_id = _base.add(
        text_stream,
        format="{extra[serialized]}",
        colorize=False,
        enqueue=False,  # loguru's own synchronous default
    )
    patched = _base.patch(_loguru_patcher(attribute_keys))

    def emit(attributes):
        patched.bind(**attributes).info(EVENT_NAME)

    def shutdown():
        _base.remove(handler_id)
        text_stream.flush()
        text_stream.detach()

    return emit, shutdown


BUILDERS = {
    "semlog": build_semlog,
    "stdlib_baseline": build_stdlib_baseline,
    "loguru": build_loguru,
    "structlog_stdlib": build_structlog_stdlib,
}

make_semlog = _with_call_signature(build_semlog)
make_stdlib_baseline = _with_call_signature(build_stdlib_baseline)
make_loguru = _with_call_signature(build_loguru)
make_structlog_stdlib = _with_call_signature(build_structlog_stdlib)

FACTORIES = {
    "semlog": make_semlog,
    "stdlib_baseline": make_stdlib_baseline,
    "loguru": make_loguru,
    "structlog_stdlib": make_structlog_stdlib,
}


def _installed(mapping):
    return {
        name: value
        for name, value in mapping.items()
        if name not in _REQUIRED_PACKAGE
        or importlib.util.find_spec(_REQUIRED_PACKAGE[name]) is not None
    }


def available_factories():
    """`FACTORIES`, minus any subject whose third-party package is missing."""
    return _installed(FACTORIES)


def available_builders():
    """`BUILDERS`, minus any subject whose third-party package is missing."""
    return _installed(BUILDERS)


def check_fairness(raw_records, fields=FIELDS):
    """Discrepancies across subjects' parsed records for the same call (empty
    when fair): every record must carry exactly `fields` in order, a
    well-formed UTC timestamp, and the same value as the first subject for
    every field except the timestamp."""
    problems = []
    reference_name = next(iter(raw_records))
    reference = raw_records[reference_name]
    for name, record in raw_records.items():
        if list(record) != list(fields):
            missing = [field for field in fields if field not in record]
            problems.append(
                f"{name} fields differ from the envelope order: {list(record)}"
                + (f" (missing {missing})" if missing else "")
            )
        if not TIMESTAMP_RE.match(str(record.get("timestamp"))):
            problems.append(
                f"{name} has a malformed timestamp: {record.get('timestamp')!r}"
            )
        for field in fields[1:]:
            if name != reference_name and record.get(field) != reference.get(field):
                problems.append(
                    f"{name} differs from {reference_name} on {field}: "
                    f"{record.get(field)!r} != {reference.get(field)!r}"
                )
    return problems
