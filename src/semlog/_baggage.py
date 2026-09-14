"""W3C Baggage parsing and allowlist codec (STANDARDS §8, TCP-007).

Splits an inbound baggage header into raw, percent-encoded members in
header order, skipping malformed members and truncating (never rejecting
outright) past the spec limits of 64 members or 8192 bytes.
`to_log_attributes` copies only the allowlisted keys into flat,
percent-decoded, UNPREFIXED attributes: the formatter applies the
configured baggage prefix once, at format time (design #162 §2.1 step 3c;
engram #239 follow-up) -- prefixing here too would double it once
`operation()`/`bind()` (Phase 3) wire this straight into `Snapshot.baggage`
(`baggage.baggage.x`). Outbound stripping at a trust boundary (TCP-008) is
a later task (`inject()`, 3.11/3.12).
"""

from __future__ import annotations

import re
import urllib.parse
from types import SimpleNamespace

_MAX_MEMBERS = 64
_MAX_BYTES = 8192
_TOKEN_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")

# Process-wide defaults set by `configure(baggage_allow=..., accept_inbound_
# baggage=...)` (design #162/#170 §3.3). `_context.snapshot_from_headers`
# reads this directly with no import cycle: `_config` imports `_format`,
# which imports `_context`, so `_context` cannot import `_config` back --
# this module sits below both, so both can depend on it safely.
defaults = SimpleNamespace(accept_inbound_baggage=True, baggage_allow=())


def parse_baggage(header: str | None) -> tuple[tuple[str, str], ...]:
    """Parse an inbound baggage header into ``(key, value)`` members."""
    if not header:
        return ()
    members: list[tuple[str, str]] = []
    total_bytes = 0
    for raw_member in header.split(","):
        if len(members) >= _MAX_MEMBERS:
            break
        candidate = raw_member.strip()
        if not candidate:
            continue
        member_bytes = len(candidate.encode("utf-8"))
        if total_bytes + member_bytes > _MAX_BYTES:
            break
        key, separator, value = candidate.split(";", 1)[0].partition("=")
        key = key.strip()
        if not separator or not _TOKEN_RE.match(key):
            continue
        members.append((key, value.strip()))
        total_bytes += member_bytes
    return tuple(members)


def to_log_attributes(
    members: tuple[tuple[str, str], ...], allow: tuple[str, ...]
) -> dict[str, str]:
    """Copy allowlisted members into flat, percent-decoded, unprefixed log
    attributes (TCP-007); the formatter applies the baggage prefix."""
    allowed = set(allow)
    return {
        key: urllib.parse.unquote(value) for key, value in members if key in allowed
    }
