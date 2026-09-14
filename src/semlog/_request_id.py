"""RFC 9562 UUIDv7 request-id generator (STANDARDS §8, TCP-010, design ADR-7).

Delegates to stdlib `uuid.uuid7()` on Python 3.14+; the bundled RFC 9562
§5.7 fallback for 3.10-3.13 lives in `_uuid7_fallback.py`, excluded from
the CP-017 source-line budget by filename.
"""

from __future__ import annotations

import sys

__all__ = ("generate_request_id",)

if sys.version_info >= (3, 14):
    import uuid

    def generate_request_id() -> str:
        """Return a new UUIDv7 string (delegates to stdlib on 3.14+)."""
        return str(uuid.uuid7())

else:
    from ._uuid7_fallback import generate_request_id
