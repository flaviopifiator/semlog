"""One immutable context snapshot per ``ContextVar`` (TCP-005), shared by
``operation()``, ``bind()`` and the middlewares."""

from __future__ import annotations

import contextlib
import contextvars
import dataclasses
import logging
import urllib.parse
from collections.abc import Mapping
from types import MappingProxyType

from ._baggage import defaults as _baggage_defaults
from ._baggage import parse_baggage, to_log_attributes
from ._request_id import generate_request_id
from ._trace import new_span, parse_traceparent

# Shared empty default. Python 3.11's dataclasses reject an unhashable default
# (mappingproxy only became hashable in 3.12), so it is supplied by a factory.
_EMPTY: Mapping[str, object] = MappingProxyType({})


@dataclasses.dataclass(frozen=True)
class Snapshot:
    """An immutable, point-in-time view of the bound operation context."""

    trace_id: str | None = None
    span_id: str | None = None
    trace_flags: str | None = None
    tracestate: str | None = None
    request_id: str | None = None
    baggage: Mapping[str, str] = dataclasses.field(default_factory=lambda: _EMPTY)
    attributes: Mapping[str, object] = dataclasses.field(default_factory=lambda: _EMPTY)


_current: contextvars.ContextVar[Snapshot | None] = contextvars.ContextVar(
    "semlog_context", default=None
)

# `current`/`push`/`pop`: the ContextVar's own bound get/set/reset need no
# wrapper -- `current()` reads the bound snapshot (or `None`); `push()`
# binds one and returns a reset token; `pop()` resets to the prior value.
current, push, pop = _current.get, _current.set, _current.reset


def snapshot_from_headers(get_header, *, baggage_allow=None) -> Snapshot:
    """Build an inbound `Snapshot` from a header getter (design #162 §2.2/§2.3):
    the shared primitive both middlewares and `operation()` bind from. The
    formatter applies the configured baggage prefix at format time, not here.
    `baggage_allow=None` (the default) falls back to `configure(baggage_
    allow=...)`'s process-wide default; `configure(accept_inbound_baggage=
    False)` ignores the inbound header entirely, regardless of any allowlist."""
    inbound = parse_traceparent(get_header("traceparent"))
    tracestate = get_header("tracestate") if inbound is not None else None
    (trace_id, span_id, trace_flags), tracestate = new_span(inbound, tracestate)
    if _baggage_defaults.accept_inbound_baggage:
        allow = (
            baggage_allow
            if baggage_allow is not None
            else _baggage_defaults.baggage_allow
        )
        baggage = to_log_attributes(parse_baggage(get_header("baggage")), allow)
    else:
        baggage = {}
    return Snapshot(
        trace_id=trace_id,
        span_id=span_id,
        trace_flags=trace_flags,
        tracestate=tracestate,
        request_id=generate_request_id(),
        baggage=baggage,
    )


@contextlib.contextmanager
def operation(headers=None, *, baggage_allow=None):
    """Bind a trace/baggage scope for non-HTTP work (design #162 §2.3,
    TCP-005): the same primitive the HTTP middlewares use. With `headers`,
    parses them like an inbound request; without, starts a child of the
    current operation if one is active, else a fresh trace."""
    parent = current()
    if headers is not None:
        snapshot = snapshot_from_headers(headers.get, baggage_allow=baggage_allow)
    elif parent is not None and parent.trace_id is not None:
        inbound = (parent.trace_id, parent.span_id, parent.trace_flags)
        (trace_id, span_id, trace_flags), tracestate = new_span(
            inbound, parent.tracestate
        )
        snapshot = dataclasses.replace(
            parent,
            trace_id=trace_id,
            span_id=span_id,
            trace_flags=trace_flags,
            tracestate=tracestate,
            request_id=generate_request_id(),
        )
    else:
        snapshot = snapshot_from_headers(
            lambda _name: None, baggage_allow=baggage_allow
        )
    token = push(snapshot)
    try:
        yield snapshot
    finally:
        pop(token)


_bind_warned = False


def bind(attributes: Mapping[str, object]) -> None:
    """Merge `attributes` into the active scope (TCP-005); outside a scope,
    warn once through the `semlog` logger and no-op (TCP-012)."""
    global _bind_warned
    snapshot = current()
    if snapshot is None:
        if not _bind_warned:
            _bind_warned = True
            logging.getLogger("semlog").warning(
                "semlog.log.bind_ignored", stack_info=True, semlog=True
            )
        return
    push(
        dataclasses.replace(snapshot, attributes={**snapshot.attributes, **attributes})
    )


def inject(headers: dict, *, trusted: bool = True) -> None:
    """Add outbound `traceparent`/`tracestate`/`baggage` to `headers` in
    place, client-agnostic (TCP-006/008/009/011); a no-op with no bound
    trace or an already-present `traceparent` (any case)."""
    snapshot = current()
    if snapshot is None or snapshot.trace_id is None:
        return
    if any(key.lower() == "traceparent" for key in headers):
        return
    headers["traceparent"] = (
        f"00-{snapshot.trace_id}-{snapshot.span_id}-{snapshot.trace_flags}"
    )
    if snapshot.tracestate is not None:
        headers["tracestate"] = snapshot.tracestate
    if trusted and snapshot.baggage:
        headers["baggage"] = ",".join(
            f"{key}={urllib.parse.quote(str(value))}"
            for key, value in snapshot.baggage.items()
        )
