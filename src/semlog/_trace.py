"""W3C Trace Context Level 1 codec: parses `traceparent`, generates new
spans (STANDARDS §8, §8.1; TCP-001..004). No `Traceparent` class (engram
#238): a trace triple is a plain ``(trace_id, parent_id, trace_flags)``
tuple.
"""

from __future__ import annotations

import re
import secrets

_HEX_RE = re.compile(r"^[0-9a-f]+$")


def parse_traceparent(header: str | None) -> tuple[str, str, str] | None:
    """Parse an inbound ``traceparent``; ``None`` when it must be treated
    as absent (TCP-002, TCP-003), else ``(trace_id, parent_id, trace_flags)``."""
    if not header:
        return None
    parts = header.split("-")
    if len(parts) < 4 or [len(p) for p in parts[:4]] != [2, 32, 16, 2]:
        return None
    version, trace_id, parent_id, trace_flags = parts[:4]
    if not all(_HEX_RE.match(field) for field in parts[:4]):
        return None
    if version == "ff" or (version == "00" and len(parts) != 4):
        return None
    if trace_id == "0" * 32 or parent_id == "0" * 16:
        return None
    return trace_id, parent_id, trace_flags


def new_span(
    inbound: tuple[str, str, str] | None, tracestate: str | None = None
) -> tuple[tuple[str, str, str], str | None]:
    """Generate a new per-operation span (TCP-004): reuses ``inbound``'s
    ``trace_id``/``trace_flags`` when present, else starts a fresh trace;
    ``tracestate`` is forwarded only when an inbound context was present."""
    if inbound is None:
        return (secrets.token_hex(16), secrets.token_hex(8), "01"), None
    trace_id, _parent_id, trace_flags = inbound
    return (trace_id, secrets.token_hex(8), trace_flags), tracestate
