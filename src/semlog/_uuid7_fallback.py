"""Bundled RFC 9562 §5.7 UUIDv7 fallback for Python 3.10-3.13.

Excluded from the CP-017 source-line budget by filename (design #162/#170
§8; STANDARDS CP-017): `_request_id.py` delegates to stdlib `uuid.uuid7()`
on 3.14+ instead. A 48-bit millisecond timestamp, a 42-bit monotonic
counter (12 bits placed in `rand_a`, plus the high 30 bits of `rand_b`)
that increments instead of moving backward within the same millisecond or
across a clock regression, and 32 fresh random bits in the low end of
`rand_b` on every call.
"""

from __future__ import annotations

import secrets
import threading
import time
import uuid

_lock = threading.Lock()
_state = {"ts_ms": -1, "counter": 0}
_COUNTER_BITS = 42
_COUNTER_MAX = (1 << _COUNTER_BITS) - 1


def _next_ts_and_counter() -> tuple[int, int]:
    with _lock:
        now_ms = time.time_ns() // 1_000_000
        if now_ms > _state["ts_ms"]:
            _state["ts_ms"] = now_ms
            _state["counter"] = secrets.randbits(_COUNTER_BITS)
        else:
            # Same millisecond, or the clock moved backward: never move
            # the timestamp backward, keep ordering via the counter.
            _state["counter"] = (_state["counter"] + 1) & _COUNTER_MAX
        return _state["ts_ms"], _state["counter"]


def generate_request_id() -> str:
    """Return a new UUIDv7 string (bundled RFC 9562 §5.7 generator)."""
    ts_ms, counter = _next_ts_and_counter()
    rand_a = counter >> 30
    rand_b = ((counter & ((1 << 30) - 1)) << 32) | secrets.randbits(32)
    value = (ts_ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return str(uuid.UUID(int=value))
